"""
Rapport global de la liste des clients — vue, contexte et export PDF/Excel.
Rapport global du portefeuille clients — vue, contexte et export PDF/Excel.
"""

from datetime import date, datetime
import io

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views import View
from django.utils import timezone
from django.db.models import Q
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from weasyprint import HTML

from accounts.decorators import ClientStaffRequiredMixin
from campaigns.models import Client, Campagne, Contrat


def _get_campagne_montant(c):
    if hasattr(c, 'montant_total') and callable(c.montant_total):
        try:
            return float(c.montant_total() or 0)
            val = c.montant_total()
            if val is not None:
                return float(val)
        except Exception:
            pass
    return float(c.prix or 0)


def _build_context_clients(filters: dict) -> dict:
    today = date.today()

    # ── Base queryset ────────────────────────────────────────────
    qs = (
        Client.objects
        .prefetch_related(
            "campagnes",
            "campagnes__lignes__face__support",
            "campagnes__lignes__emplacement__marche",
            "campagnes__visuels",
            "contrats",
        )
        .order_by("nom")
    )

    # ── Filtres ──────────────────────────────────────────────────
    q = filters.get("q", "").strip()
    statut = filters.get("statut", "").strip()
    has_contrat = filters.get("has_contrat", "").strip()

    if statut:
        if statut.lower() in ("actif", "1", "true"):
            qs = qs.filter(actif=True)
        elif statut.lower() in ("inactif", "0", "false"):
            qs = qs.filter(actif=False)

    if q:
        qs = qs.filter(
            Q(nom__icontains=q)
            | Q(contact_nom__icontains=q)
            | Q(telephone__icontains=q)
            | Q(email__icontains=q)
            | Q(reference__icontains=q)
            | Q(adresse__icontains=q)
        )

    clients_data = []
    total_clients_actifs = 0
    total_campagnes_actives_global = 0
    total_contrats_actifs_global = 0
    total_budget_global = 0

    for client in qs:
        # Contrats
        contrats = list(client.contrats.all())
        contrats_actifs = [c for c in contrats if c.actif and (not c.date_fin or c.date_fin >= today)]
        has_active_contrat = len(contrats_actifs) > 0

        if has_contrat == "oui" and not has_active_contrat:
            continue
        elif has_contrat == "non" and has_active_contrat:
            continue

        # Campagnes
        campagnes = list(client.campagnes.exclude(statut='annulee'))
        campagnes_en_cours = [
            c for c in campagnes
            if c.statut == 'en_cours' or (c.date_debut and c.date_fin and c.date_debut <= today <= c.date_fin)
        ]
        
        budget_total_client = sum(_get_campagne_montant(c) for c in campagnes)
        # Campagnes détaillées
        campagnes_raw = list(client.campagnes.exclude(statut='annulee').order_by("-date_debut", "-id"))
        campagnes_list = []
        campagnes_actives_list = []

        for c in campagnes_raw:
            is_active = (c.statut == 'en_cours') or (c.date_debut and c.date_fin and c.date_debut <= today <= c.date_fin)
            montant = _get_campagne_montant(c)

            # Extraire les supports mobilisés
            supports_mobilises = []
            for l in c.lignes.all():
                if l.face and l.face.support:
                    sup = l.face.support
                    supports_mobilises.append({
                        "code": sup.code,
                        "nom": sup.nom,
                        "ville": sup.ville or "",
                        "quartier": sup.quartier or "",
                        "face": l.face.label,
                        "type": "Panneau" if sup.type_support == "panneau" else "Écran",
                    })
                elif l.emplacement:
                    emp = l.emplacement
                    marche_nom = emp.marche.nom if emp.marche else "Marché"
                    supports_mobilises.append({
                        "code": emp.code,
                        "nom": marche_nom,
                        "ville": emp.marche.ville if emp.marche else "",
                        "quartier": emp.marche.quartier if emp.marche else "",
                        "face": "Emplacement",
                        "type": "Marché",
                    })

            c_info = {
                "campagne": c,
                "pk": c.pk,
                "reference": c.reference or f"CMP-{c.pk:04d}",
                "nom": c.nom,
                "type_support": c.get_type_support_display() if hasattr(c, "get_type_support_display") else c.type_support,
                "statut": c.statut,
                "statut_display": c.get_statut_display() if hasattr(c, "get_statut_display") else c.statut,
                "date_debut": c.date_debut,
                "date_fin": c.date_fin,
                "duree_jours": (c.date_fin - c.date_debut).days + 1 if (c.date_debut and c.date_fin) else None,
                "montant": montant,
                "is_active": is_active,
                "supports": supports_mobilises,
                "nb_supports": len(supports_mobilises),
            }
            campagnes_list.append(c_info)
            if is_active:
                campagnes_actives_list.append(c_info)

        budget_total_client = sum(c["montant"] for c in campagnes_list)

        if client.actif:
            total_clients_actifs += 1

        total_campagnes_actives_global += len(campagnes_en_cours)
        total_campagnes_actives_global += len(campagnes_actives_list)
        total_contrats_actifs_global += len(contrats_actifs)
        total_budget_global += budget_total_client

        clients_data.append({
            "client": client,
            "pk": client.pk,
            "reference": client.reference or f"CLI-{client.pk:04d}",
            "nom": client.nom,
            "contact_nom": client.contact_nom or "—",
            "telephone": client.telephone or "—",
            "email": client.email or "—",
            "adresse": client.adresse or "—",
            "notes": client.notes or "",
            "actif": client.actif,
            "nb_campagnes_total": len(campagnes),
            "nb_campagnes_actives": len(campagnes_en_cours),
            "nb_campagnes_total": len(campagnes_list),
            "nb_campagnes_actives": len(campagnes_actives_list),
            "campagnes_actives": campagnes_actives_list,
            "campagnes": campagnes_list,
            "has_active_contrat": has_active_contrat,
            "contrats_actifs": contrats_actifs,
            "budget_total": budget_total_client,
            "created_at": client.created_at,
        })

    return {
        "today": today,
        "clients": clients_data,
        "total_clients": len(clients_data),
        "total_clients_actifs": total_clients_actifs,
        "total_campagnes_actives": total_campagnes_actives_global,
        "total_contrats_actifs": total_contrats_actifs_global,
        "total_budget_global": total_budget_global,
        "filters": filters,
    }


class ClientsReportView(LoginRequiredMixin, ClientStaffRequiredMixin, View):
    """Aperçu web interactif du rapport global des clients."""

    def get(self, request, *args, **kwargs):
        filters = {
            "q": request.GET.get("q", ""),
            "statut": request.GET.get("statut", ""),
            "has_contrat": request.GET.get("has_contrat", ""),
        }
        context = _build_context_clients(filters)
        return render(request, "reports/apercu_clients.html", context)


class ExportClientsPdfView(LoginRequiredMixin, ClientStaffRequiredMixin, View):
    """Génération et téléchargement du rapport PDF global des clients."""

    def get(self, request, *args, **kwargs):
        filters = {
            "q": request.GET.get("q", ""),
            "statut": request.GET.get("statut", ""),
            "has_contrat": request.GET.get("has_contrat", ""),
        }
        context = _build_context_clients(filters)
        html_string = render_to_string("reports/clients_report_pdf_export.html", context, request=request)
        
        pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
        filename = f"rapport_clients_{date.today().strftime('%Y%m%d')}.pdf"
        filename = f"rapport_portefeuille_clients_{date.today().strftime('%Y%m%d')}.pdf"
        response = HttpResponse(pdf_file, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ExportClientsExcelView(LoginRequiredMixin, ClientStaffRequiredMixin, View):
    """Export Excel du portefeuille clients."""

    def get(self, request, *args, **kwargs):
        import openpyxl
        filters = {
            "q": request.GET.get("q", ""),
            "statut": request.GET.get("statut", ""),
            "has_contrat": request.GET.get("has_contrat", ""),
        }
        context = _build_context_clients(filters)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Portefeuille Clients"

        font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="932E2B", end_color="932E2B", fill_type="solid")

        headers = [
            "Référence", "Raison Sociale / Nom", "Contact Principal", "Téléphone", "Email",
            "Adresse", "Statut", "Contrat Actif", "Campagnes Actives", "Total Campagnes", "Volume d'Affaires (FCFA)"
            "Adresse", "Statut Client", "Contrat Actif", "Réf Campagne", "Nom Campagne",
            "Type Support", "Période Début", "Période Fin", "Statut Campagne", "Budget Campagne (FCFA)"
        ]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for c in context["clients"]:
            ws.append([
                c["reference"],
                c["nom"],
                c["contact_nom"],
                c["telephone"],
                c["email"],
                c["adresse"],
                "Actif" if c["actif"] else "Inactif",
                "Oui" if c["has_active_contrat"] else "Non",
                c["nb_campagnes_actives"],
                c["nb_campagnes_total"],
                c["budget_total"],
            ])
            if not c["campagnes"]:
                ws.append([
                    c["reference"], c["nom"], c["contact_nom"], c["telephone"], c["email"],
                    c["adresse"], "Actif" if c["actif"] else "Inactif",
                    "Oui" if c["has_active_contrat"] else "Non",
                    "—", "Aucune campagne", "—", "—", "—", "—", 0
                ])
            else:
                for cmp in c["campagnes"]:
                    ws.append([
                        c["reference"], c["nom"], c["contact_nom"], c["telephone"], c["email"],
                        c["adresse"], "Actif" if c["actif"] else "Inactif",
                        "Oui" if c["has_active_contrat"] else "Non",
                        cmp["reference"], cmp["nom"], str(cmp["type_support"]),
                        cmp["date_debut"].strftime("%d/%m/%Y") if cmp["date_debut"] else "—",
                        cmp["date_fin"].strftime("%d/%m/%Y") if cmp["date_fin"] else "—",
                        cmp["statut_display"], cmp["montant"]
                    ])

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"rapport_clients_{date.today().strftime('%Y%m%d')}.xlsx"
        filename = f"rapport_portefeuille_clients_{date.today().strftime('%Y%m%d')}.xlsx"
        response = HttpResponse(
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
