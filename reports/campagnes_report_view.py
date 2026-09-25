"""
Rapport global de la liste des campagnes — vue, contexte et export PDF/Excel.
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
from campaigns.models import Client, Campagne, STATUT_CHOICES


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


def _build_context_campagnes(filters: dict) -> dict:
    today = date.today()

    # ── Base queryset ────────────────────────────────────────────
    qs = (
        Campagne.objects
        .select_related("client")
        .prefetch_related(
            "lignes__face__support",
            "lignes__emplacement__marche",
            "visuels",
        )
        .order_by("-date_debut", "-id")
    )

    # ── Filtres ──────────────────────────────────────────────────
    q = filters.get("q", "").strip()
    statut = filters.get("statut", "").strip()
    client_pk = filters.get("client_pk", "").strip()
    type_support = filters.get("type_support", "").strip()
    date_debut = filters.get("date_debut", "").strip()
    date_fin = filters.get("date_fin", "").strip()

    if statut:
        qs = qs.filter(statut=statut)

    if client_pk:
        qs = qs.filter(client_id=client_pk)

    if type_support:
        qs = qs.filter(type_support=type_support)

    if date_debut:
        try:
            d_debut = datetime.strptime(date_debut, "%Y-%m-%d").date()
            qs = qs.filter(date_fin__gte=d_debut)
        except ValueError:
            pass

    if date_fin:
        try:
            d_fin = datetime.strptime(date_fin, "%Y-%m-%d").date()
            qs = qs.filter(date_debut__lte=d_fin)
        except ValueError:
            pass

    if q:
        qs = qs.filter(
            Q(nom__icontains=q)
            | Q(reference__icontains=q)
            | Q(client__nom__icontains=q)
        )

    all_clients = sorted(list(Client.objects.filter(actif=True).values("id", "nom")), key=lambda x: x["nom"])

    campagnes_data = []
    total_budget_global = 0
    total_en_cours = 0
    total_a_venir = 0
    total_terminees = 0
    total_supports_mobilises = 0

    for c in qs:
        # Supports / Faces
        lignes = list(c.lignes.all())
        nb_supports = len(set(
            l.face.support_id for l in lignes if l.face and l.face.support_id
        )) + len(set(
            l.emplacement_id for l in lignes if l.emplacement_id
        ))
        # Extraire la liste complète des supports et faces mobilisés
        supports_details = []
        for l in c.lignes.all():
            if l.face and l.face.support:
                sup = l.face.support
                supports_details.append({
                    "code": sup.code,
                    "nom": sup.nom,
                    "ville": sup.ville or "—",
                    "quartier": sup.quartier or "—",
                    "format": sup.format or sup.type_panneau or "—",
                    "face": l.face.label,
                    "type": "Panneau" if sup.type_support == "panneau" else "Écran",
                })
            elif l.emplacement:
                emp = l.emplacement
                marche_nom = emp.marche.nom if emp.marche else "Marché"
                supports_details.append({
                    "code": emp.code,
                    "nom": marche_nom,
                    "ville": emp.marche.ville if emp.marche else "—",
                    "quartier": emp.marche.quartier if emp.marche else "—",
                    "format": "Marché",
                    "face": "Emplacement",
                    "type": "Marché",
                })

        nb_supports = len(supports_details)
        budget = _get_campagne_montant(c)
        total_budget_global += budget
        total_supports_mobilises += nb_supports

        is_active = (c.statut == 'en_cours') or (c.date_debut and c.date_fin and c.date_debut <= today <= c.date_fin)

        if is_active:
            total_en_cours += 1
        elif c.statut == 'a_venir':
            total_a_venir += 1
        elif c.statut == 'terminee':
            total_terminees += 1

        duree_jours = (c.date_fin - c.date_debut).days + 1 if (c.date_debut and c.date_fin) else None

        campagnes_data.append({
            "campagne": c,
            "pk": c.pk,
            "reference": c.reference or f"CMP-{c.pk:04d}",
            "nom": c.nom,
            "client_nom": c.client.nom if c.client else "—",
            "client_contact": c.client.contact_nom if c.client else "—",
            "client_telephone": c.client.telephone if c.client else "—",
            "client_email": c.client.email if c.client else "—",
            "type_support": c.get_type_support_display() if hasattr(c, "get_type_support_display") else c.type_support,
            "type_raw": c.type_support,
            "statut_display": c.get_statut_display() if hasattr(c, "get_statut_display") else c.statut,
            "statut": c.statut,
            "is_active": is_active,
            "date_debut": c.date_debut,
            "date_fin": c.date_fin,
            "duree_jours": duree_jours,
            "nb_supports": nb_supports,
            "supports": supports_details,
            "budget": budget,
            "duree_passage": c.duree_passage,
            "frequence": c.frequence,
            "tranches_horaires": c.tranches_horaires,
            "notes": c.notes,
            "est_mere": c.est_mere,
        })

    return {
        "today": today,
        "campagnes": campagnes_data,
        "total_campagnes": len(campagnes_data),
        "total_en_cours": total_en_cours,
        "total_a_venir": total_a_venir,
        "total_terminees": total_terminees,
        "total_budget_global": total_budget_global,
        "total_supports_mobilises": total_supports_mobilises,
        "all_clients": all_clients,
        "statut_choices": STATUT_CHOICES,
        "filters": filters,
    }


class CampagnesReportView(LoginRequiredMixin, ClientStaffRequiredMixin, View):
    """Aperçu web interactif du rapport global des campagnes."""

    def get(self, request, *args, **kwargs):
        filters = {
            "q": request.GET.get("q", ""),
            "statut": request.GET.get("statut", ""),
            "client_pk": request.GET.get("client_pk", ""),
            "type_support": request.GET.get("type_support", ""),
            "date_debut": request.GET.get("date_debut", ""),
            "date_fin": request.GET.get("date_fin", ""),
        }
        context = _build_context_campagnes(filters)
        return render(request, "reports/apercu_campagnes.html", context)


class ExportCampagnesPdfView(LoginRequiredMixin, ClientStaffRequiredMixin, View):
    """Génération et téléchargement du rapport PDF global des campagnes."""

    def get(self, request, *args, **kwargs):
        filters = {
            "q": request.GET.get("q", ""),
            "statut": request.GET.get("statut", ""),
            "client_pk": request.GET.get("client_pk", ""),
            "type_support": request.GET.get("type_support", ""),
            "date_debut": request.GET.get("date_debut", ""),
            "date_fin": request.GET.get("date_fin", ""),
        }
        context = _build_context_campagnes(filters)
        html_string = render_to_string("reports/campagnes_report_pdf_export.html", context, request=request)
        
        pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
        filename = f"rapport_campagnes_{date.today().strftime('%Y%m%d')}.pdf"
        filename = f"rapport_campagnes_publicitaires_{date.today().strftime('%Y%m%d')}.pdf"
        response = HttpResponse(pdf_file, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ExportCampagnesExcelView(LoginRequiredMixin, ClientStaffRequiredMixin, View):
    """Export Excel de la liste des campagnes."""
    """Export Excel détaillé de la liste des campagnes."""

    def get(self, request, *args, **kwargs):
        import openpyxl
        filters = {
            "q": request.GET.get("q", ""),
            "statut": request.GET.get("statut", ""),
            "client_pk": request.GET.get("client_pk", ""),
            "type_support": request.GET.get("type_support", ""),
            "date_debut": request.GET.get("date_debut", ""),
            "date_fin": request.GET.get("date_fin", ""),
        }
        context = _build_context_campagnes(filters)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Campagnes Publicitaires"

        font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="932E2B", end_color="932E2B", fill_type="solid")

        headers = [
            "Référence", "Nom de la Campagne", "Client", "Type Support", "Date Début", "Date Fin",
            "Supports Alloués", "Budget Total (FCFA)", "Statut"
            "Référence Campagne", "Nom de la Campagne", "Client", "Contact Client", "Téléphone Client",
            "Type Support", "Période Début", "Période Fin", "Durée (jours)", "Statut",
            "Budget Total (FCFA)", "Code Support Mobilisé", "Nom Support", "Localisation", "Face / Slot"
        ]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for c in context["campagnes"]:
            ws.append([
                c["reference"],
                c["nom"],
                c["client_nom"],
                str(c["type_support"]),
                c["date_debut"].strftime("%d/%m/%Y") if c["date_debut"] else "—",
                c["date_fin"].strftime("%d/%m/%Y") if c["date_fin"] else "—",
                c["nb_supports"],
                c["budget"],
                c["statut_display"],
            ])
            if not c["supports"]:
                ws.append([
                    c["reference"], c["nom"], c["client_nom"], c["client_contact"], c["client_telephone"],
                    str(c["type_support"]),
                    c["date_debut"].strftime("%d/%m/%Y") if c["date_debut"] else "—",
                    c["date_fin"].strftime("%d/%m/%Y") if c["date_fin"] else "—",
                    c["duree_jours"] or "—", c["statut_display"], c["budget"],
                    "—", "Aucun support alloué", "—", "—"
                ])
            else:
                for s in c["supports"]:
                    loc_str = f"{s['ville']}, {s['quartier']}".strip(', ')
                    ws.append([
                        c["reference"], c["nom"], c["client_nom"], c["client_contact"], c["client_telephone"],
                        str(c["type_support"]),
                        c["date_debut"].strftime("%d/%m/%Y") if c["date_debut"] else "—",
                        c["date_fin"].strftime("%d/%m/%Y") if c["date_fin"] else "—",
                        c["duree_jours"] or "—", c["statut_display"], c["budget"],
                        s["code"], s["nom"], loc_str, s["face"]
                    ])

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"rapport_campagnes_{date.today().strftime('%Y%m%d')}.xlsx"
        filename = f"rapport_campagnes_publicitaires_{date.today().strftime('%Y%m%d')}.xlsx"
        response = HttpResponse(
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
