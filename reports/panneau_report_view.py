"""
Rapport d'état des panneaux — vue, contexte et export PDF/Excel.

Fonctionnalités :
  - Liste tous les panneaux avec leurs faces libres / occupées
  - Associe les campagnes actives (et futures) à chaque face
  - Filtres : ville, quartier, statut du panneau, occupation
  - Export PDF (WeasyPrint) et Excel (openpyxl)
"""

from datetime import date, datetime
import io

import pandas as pd
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views import View
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from weasyprint import HTML

from accounts.decorators import ClientStaffRequiredMixin
from campaigns.models import Campagne, Client
from inventory.models import *


# ══════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════

OCCUPATION_CHOICES = {
    "complet"    : "Complet (toutes faces occupées)",
    "partiel"    : "Partiellement occupé",
    "libre"      : "Entièrement libre",
    "only_occupe": "Occupé (au moins une face)",
}


# ══════════════════════════════════════════════════════════════════
# DESIGN
# ══════════════════════════════════════════════════════════════════

# def _get_design():
#     """
#     The function `_get_design` returns a dictionary containing color values for different design
#     elements.
#     """
#     from django.conf import settings
#     return getattr(settings, "GEOAD_DESIGN", {
#         "COLOR_PRIMARY"    : "#1a3a6e",
#         "COLOR_SECONDARY"  : "#f59e0b",
#         "COLOR_NOIR"       : "#1e293b",
#         "COLOR_DISPONIBLE" : "#16a34a",
#         "COLOR_OCCUPE"     : "#c47a00",
#         "COLOR_MAINTENANCE": "#dc2626",
#     })


# ══════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════

def _occupation_label(nb_faces_total, nb_occupees):
    if nb_faces_total == 0 or nb_occupees == 0:
        return "libre"
    if nb_occupees >= nb_faces_total:
        return "complet"
    return "partiel"


# ══════════════════════════════════════════════════════════════════
# CONSTRUCTION DU CONTEXTE
# ══════════════════════════════════════════════════════════════════

def _build_context_panneaux(filters: dict) -> dict:
    today = date.today()

    # ── Base queryset ────────────────────────────────────────────
    qs = (
        Support.objects
        .filter(type_support="panneau")
        .prefetch_related("faces__lignes_campagne__campagne__client")
        .order_by("ville", "quartier", "nom")
    )

    # ── Filtres simples ──────────────────────────────────────────
    ville     = filters.get("ville", "").strip()
    quartier  = filters.get("quartier", "").strip()
    statut    = filters.get("statut", "").strip()
    q         = filters.get("q", "").strip()
    client_pk = filters.get("client_pk", "")

    if ville:
        qs = qs.filter(ville__iexact=ville)
    if quartier:
        qs = qs.filter(quartier__iexact=quartier)
    if statut:
        qs = qs.filter(etat=statut)
    if q:
        qs = (
            qs.filter(nom__icontains=q)
            | qs.filter(code__icontains=q)
            | qs.filter(adresse__icontains=q)
        )
    if client_pk:
        qs = qs.filter(
            lignes_campagne__campagne__client_id=client_pk
        ).distinct()

    # ── Construction des lignes ──────────────────────────────────
    filter_occupation = filters.get("occupation", "").strip()

    panneaux       = []
    total_faces    = 0
    total_occupees = 0

    for support in qs:
        faces_data  = []
        nb_occupees = 0

        for face in support.faces.all():
            campagnes_list = list(
                Campagne.objects
                .filter(lignes__face=face, date_fin__gte=today)
                .select_related("client")
                .distinct()
                .order_by("date_debut")
            )
            est_occupee = bool(campagnes_list)
            if est_occupee:
                nb_occupees += 1

            faces_data.append({
                "face"     : face,
                "label"    : face.label,
                "occupee"  : est_occupee,
                "campagnes": campagnes_list,
            })

        nb_faces_total = len(faces_data)
        occupation     = _occupation_label(nb_faces_total, nb_occupees)

        # Appliquer le filtre occupation APRÈS calcul
        if filter_occupation:
            if filter_occupation == "only_occupe":
                if occupation == "libre":
                    continue
            elif filter_occupation != occupation:
                continue

        total_faces    += nb_faces_total
        total_occupees += nb_occupees

        panneaux.append({
            "support"    : support,
            "code"       : support.code,
            "nom"        : support.nom,
            "ville"      : support.ville,
            "quartier"   : support.quartier,
            "adresse"    : support.adresse,
            "statut"     : support.get_etat_display() if hasattr(support, "get_etat_display") else support.etat,
            "etat_raw"   : support.etat,   # utile pour les classes CSS des badges
            "nb_faces"   : nb_faces_total,
            "nb_occupees": nb_occupees,
            "nb_libres"  : nb_faces_total - nb_occupees,
            "occupation" : occupation,
            "faces"      : faces_data,
        })

    # ── Données pour les <select> de filtres ────────────────────
    all_supports = Support.objects.filter(type_support="panneau")

    villes    = sorted({v.strip() for v in all_supports.values_list("ville",    flat=True) if v and v.strip()})
    quartiers = sorted({q.strip() for q in all_supports.values_list("quartier", flat=True) if q and q.strip()})
    statuts   = sorted({s.strip() for s in all_supports.values_list("etat",     flat=True) if s and s.strip()})
    clients   = Client.objects.order_by("nom")

    return {
        "panneaux"          : panneaux,
        "today"             : today,
        "filters"           : filters,
        "villes"            : villes,
        "quartiers"         : quartiers,
        "statuts"           : statuts,
        "clients"           : clients,
        "occupation_choices": OCCUPATION_CHOICES,
        "total_panneaux"    : len(panneaux),
        "total_faces"       : total_faces,
        "total_occupees"    : total_occupees,
        "total_libres"      : total_faces - total_occupees,
        #"DESIGN"            : _get_design(),
    }


def _filters_from_request(request) -> dict:
    return {
        "ville"      : request.GET.get("ville", ""),
        "quartier"   : request.GET.get("quartier", ""),
        "statut"     : request.GET.get("statut", ""),
        "occupation" : request.GET.get("occupation", ""),
        "client_pk"  : request.GET.get("client_pk", ""),
        "q"          : request.GET.get("q", ""),
    }


# ══════════════════════════════════════════════════════════════════
# VUES
# ══════════════════════════════════════════════════════════════════

class PanneauxReportView(ClientStaffRequiredMixin, View):
    """Aperçu HTML du rapport — inclut les filtres."""

    def get(self, request):
        filters = _filters_from_request(request)
        context = _build_context_panneaux(filters)
        return render(request, "reports/apercu_panneaux.html", context)


class ExportPanneauxPdfView(ClientStaffRequiredMixin, View):
    """Télécharge le rapport filtré en PDF."""

    def get(self, request):
        filters = _filters_from_request(request)
        context = _build_context_panneaux(filters)

        html_string = render_to_string(
            "reports/panneaux_report_pdf.html",
            context,
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        # Nom de fichier reflétant les filtres actifs
        parts = ["etat_panneaux"]
        if filters.get("ville"):      parts.append(filters["ville"].replace(" ", "_"))
        if filters.get("quartier"):   parts.append(filters["quartier"].replace(" ", "_"))
        if filters.get("occupation"): parts.append(filters["occupation"])
        parts.append(f"{datetime.now():%Y%m%d}")
        filename = "_".join(parts) + ".pdf"

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ExportPanneauxExcelView(ClientStaffRequiredMixin, View):
    """Télécharge le rapport filtré en Excel."""

    def get(self, request):
        filters = _filters_from_request(request)
        context = _build_context_panneaux(filters)

        rows = []
        for panneau in context["panneaux"]:
            if not panneau["faces"]:
                rows.append({
                    "Code"     : panneau["code"],
                    "Nom"      : panneau["nom"],
                    "Ville"    : panneau["ville"],
                    "Quartier" : panneau["quartier"],
                    "Adresse"  : panneau["adresse"],
                    "Statut"   : panneau["statut"],
                    "Face"     : "—",
                    "Occupation": "Libre",
                    "Campagne" : "",
                    "Client"   : "",
                    "Début"    : "",
                    "Fin"      : "",
                })
                continue

            for face_data in panneau["faces"]:
                if face_data["campagnes"]:
                    for camp in face_data["campagnes"]:
                        rows.append({
                            "Code"     : panneau["code"],
                            "Nom"      : panneau["nom"],
                            "Ville"    : panneau["ville"],
                            "Quartier" : panneau["quartier"],
                            "Adresse"  : panneau["adresse"],
                            "Statut"   : panneau["statut"],
                            "Face"     : face_data["label"],
                            "Occupation": "Occupée",
                            "Campagne" : camp.nom,
                            "Client"   : camp.client.nom if camp.client else "—",
                            "Début"    : camp.date_debut.strftime("%d/%m/%Y"),
                            "Fin"      : camp.date_fin.strftime("%d/%m/%Y"),
                        })
                else:
                    rows.append({
                        "Code"     : panneau["code"],
                        "Nom"      : panneau["nom"],
                        "Ville"    : panneau["ville"],
                        "Quartier" : panneau["quartier"],
                        "Adresse"  : panneau["adresse"],
                        "Statut"   : panneau["statut"],
                        "Face"     : face_data["label"],
                        "Occupation": "Libre",
                        "Campagne" : "",
                        "Client"   : "",
                        "Début"    : "",
                        "Fin"      : "",
                    })

        df = pd.DataFrame(rows)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="État Panneaux", startrow=2)
            ws = writer.sheets["État Panneaux"]

            # Titre avec filtres actifs
            titre_parts = [f"État des panneaux — généré le {date.today():%d/%m/%Y}"]
            if filters.get("ville"):     titre_parts.append(f"Ville : {filters['ville']}")
            if filters.get("quartier"):  titre_parts.append(f"Quartier : {filters['quartier']}")
            if filters.get("occupation"):titre_parts.append(f"Occupation : {filters['occupation']}")
            ws["A1"] = "  |  ".join(titre_parts)
            ws["A1"].font = Font(bold=True, size=12, name="Arial")

            # En-têtes
            header_fill = PatternFill("solid", start_color="1a3a6e", end_color="1a3a6e")
            header_font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
            for cell in ws[3]:
                cell.fill      = header_fill
                cell.font      = header_font
                cell.alignment = Alignment(horizontal="center")

            # Coloration conditionnelle
            green  = PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE")
            orange = PatternFill("solid", start_color="FFEB9C", end_color="FFEB9C")
            occ_col_idx = list(df.columns).index("Occupation") + 1

            for row in ws.iter_rows(min_row=4, max_row=ws.max_row):
                occ_cell = row[occ_col_idx - 1]
                if occ_cell.value == "Libre":
                    occ_cell.fill = green
                elif occ_cell.value == "Occupée":
                    occ_cell.fill = orange

            # Largeurs automatiques
            for col in ws.columns:
                max_len = max(
                    (len(str(c.value)) for c in col if c.value is not None),
                    default=10,
                )
                ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 50)

            ws.freeze_panes = "A4"

        buffer.seek(0)

        parts = ["etat_panneaux"]
        if filters.get("ville"):      parts.append(filters["ville"].replace(" ", "_"))
        if filters.get("quartier"):   parts.append(filters["quartier"].replace(" ", "_"))
        if filters.get("occupation"): parts.append(filters["occupation"])
        parts.append(f"{datetime.now():%Y%m%d}")
        filename = "_".join(parts) + ".xlsx"

        response = HttpResponse(
            buffer,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response