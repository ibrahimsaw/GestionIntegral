import datetime
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from accounts.decorators import *
from campaigns.models import Campagne, Client
from django.http import HttpResponse
from calendar import monthrange
from django.template.loader import render_to_string
from dateutil.relativedelta import relativedelta
from weasyprint import HTML
import io
from django.views import View

import pandas as pd


class ReportsIndexView(LoginRequiredMixin, View):
    def get(self, request):
        campagnes = Campagne.objects.select_related('client').order_by('-date_debut')
        if request.user.is_client_role and request.user.client_profile:
            campagnes = campagnes.filter(client=request.user.client_profile)
        return render(request, 'reports/index.html', {'campagnes': campagnes})


def _check_campagne_permission(request, campagne):
    """Helper : lève PermissionDenied si l'utilisateur n'a pas accès à la campagne."""
    if request.user.is_client_role:
        if not (request.user.client_profile and campagne.client == request.user.client_profile):
            raise PermissionDenied
    elif not request.user.is_staff:
        raise PermissionDenied


DEFAULT_PLAGE = "06:00-22:00"

MOIS_FR = {
    1: "JANVIER",   2: "FÉVRIER",  3: "MARS",      4: "AVRIL",
    5: "MAI",       6: "JUIN",     7: "JUILLET",   8: "AOÛT",
    9: "SEPTEMBRE", 10: "OCTOBRE", 11: "NOVEMBRE", 12: "DÉCEMBRE",
}

# ══════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════

def _format_freq(campagne):
    freq = getattr(campagne, "frequence", None)
    if not freq:
        return "1/2 mn"
    if freq < 60:
        return f"Toutes les {freq} sec"
    minutes = freq // 60
    return f"Toutes les {minutes} min"


def _spots_du_mois(campagne, spots_total, annee, mois):
    """
    Calcule les spots d'une campagne pour un mois donné.
    Proratise selon le nombre de jours de la campagne dans ce mois.
    """
    debut = campagne.date_debut
    fin   = campagne.date_fin

    mois_debut = datetime.date(annee, mois, 1)
    mois_fin   = datetime.date(annee, mois, monthrange(annee, mois)[1])

    inter_debut = max(debut, mois_debut)
    inter_fin   = min(fin,   mois_fin)

    if inter_debut > inter_fin:
        return 0

    jours_dans_mois    = (inter_fin - inter_debut).days + 1
    duree_totale_jours = (fin - debut).days + 1

    if duree_totale_jours == 0:
        return 0

    return round(spots_total * jours_dans_mois / duree_totale_jours)


def _mois_couverts(date_debut, date_fin):
    """Retourne la liste des (annee, mois) couverts entre deux dates."""
    mois    = []
    courant = datetime.date(date_debut.year, date_debut.month, 1)
    fin     = datetime.date(date_fin.year,   date_fin.month,   1)
    while courant <= fin:
        mois.append((courant.year, courant.month))
        courant += relativedelta(months=1)
    return mois


# ══════════════════════════════════════════════════════════════════
# CONSTRUCTION DU CONTEXTE CAMPAGNE
# ══════════════════════════════════════════════════════════════════

def _build_context_campagne(campagne):
    """
    Construit le contexte complet pour le PDF / aperçu d'une campagne.
    """
    enfants = []
    campagnes = [campagne]

    if campagne.est_mere:
        enfants = list(campagne.sous_campagnes.all())
        campagnes = enfants

    # ── Infos générales ──────────────────────────────────────────
    infos = {
        "nom"                   : campagne.nom,
        "reference"             : campagne.reference,
        "date_debut"            : campagne.date_debut,
        "date_fin"              : campagne.date_fin,
        "duree_jours"           : campagne.duree_jours(),
        "statut"                : campagne.get_statut_display(),
        "type_support"          : "Campagne mère" if campagne.est_mere else campagne.get_type_support_display(),
        "effective_type_support": None,
        "notes"                 : campagne.notes,
        "contrat"               : campagne.contrat,
        "client"                : campagne.client,
        "campagne_mere"         : campagne.est_mere,
        "child_campaigns"       : enfants if campagne.est_mere else None,
        "child_count"           : len(enfants) if campagne.est_mere else 0,
        # Champs écran
        "frequence"      : campagne.frequence,
        "freq_display"   : _format_freq(campagne),
        "duree_passage"  : campagne.duree_passage,
        "tranches"       : campagne.tranches_horaires or "—",
    }

    # ── Lignes associées ─────────────────────────────────────────
    lignes = []
    if campagne.est_mere:
        for enfant in enfants:
            lignes.extend(list(enfant.lignes.all()))
    else:
        lignes = list(campagne.lignes.all())

    effective_support_types = {
        ligne.support.type_support
        for ligne in lignes
        if ligne.support and ligne.support.type_support
    }

    if campagne.type_support == "ecran":
        infos["effective_type_support"] = "ecran"
    elif campagne.type_support == "panneau":
        infos["effective_type_support"] = "panneau"
    elif "ecran" in effective_support_types:
        infos["effective_type_support"] = "ecran"
    elif "panneau" in effective_support_types:
        infos["effective_type_support"] = "panneau"
    else:
        infos["effective_type_support"] = ""

    # ── Détail supports ──────────────────────────────────────────
    supports = []

    if infos["effective_type_support"] == "ecran":
        support_dict = {}
        for ligne in lignes:
            if not ligne.support or ligne.support.type_support != "ecran":
                continue
            support = ligne.support
            support_dict.setdefault(support.pk, {
                "code"    : support.code,
                "nom"     : support.nom,
                "ville"   : support.ville,
                "quartier": support.quartier,
                "adresse" : support.adresse,
                "type"    : "Écran",
                "face"    : None,
            })
        supports = list(support_dict.values())

    elif infos["effective_type_support"] == "panneau":
        faces_dict = {}
        for ligne in lignes:
            if not ligne.support or ligne.support.type_support != "panneau":
                continue
            key = ligne.support.pk
            if key not in faces_dict:
                faces_dict[key] = {
                    "code"        : ligne.support.code,
                    "nom"         : ligne.support.nom,
                    "ville"       : ligne.support.ville,
                    "quartier"    : ligne.support.quartier,
                    "adresse"     : ligne.support.adresse,
                    "type"        : "Panneau",
                    "face_labels" : [],
                }
            if ligne.face and ligne.face.label not in faces_dict[key]["face_labels"]:
                faces_dict[key]["face_labels"].append(ligne.face.label)

        for v in faces_dict.values():
            supports.append({
                "code"    : v["code"],
                "nom"     : v["nom"],
                "ville"   : v["ville"],
                "quartier": v["quartier"],
                "adresse" : v["adresse"],
                "type"    : v["type"],
                "face"    : " & ".join(v["face_labels"]) if v["face_labels"] else "—",
            })

    supports_by_child = []
    if campagne.est_mere:
        for enfant in enfants:
            child_supports = []
            if infos["effective_type_support"] == "ecran":
                support_dict = {}
                for ligne in enfant.lignes.all():
                    if not ligne.support or ligne.support.type_support != "ecran":
                        continue
                    support = ligne.support
                    support_dict.setdefault(support.pk, {
                        "code"    : support.code,
                        "nom"     : support.nom,
                        "ville"   : support.ville,
                        "quartier": support.quartier,
                        "adresse" : support.adresse,
                        "type"    : "Écran",
                        "face"    : None,
                    })
                child_supports = list(support_dict.values())
            elif infos["effective_type_support"] == "panneau":
                faces_dict = {}
                for ligne in enfant.lignes.all():
                    if not ligne.support or ligne.support.type_support != "panneau":
                        continue
                    key = ligne.support.pk
                    if key not in faces_dict:
                        faces_dict[key] = {
                            "code"        : ligne.support.code,
                            "nom"         : ligne.support.nom,
                            "ville"       : ligne.support.ville,
                            "quartier"    : ligne.support.quartier,
                            "adresse"     : ligne.support.adresse,
                            "type"        : "Panneau",
                            "face_labels" : [],
                        }
                    if ligne.face and ligne.face.label not in faces_dict[key]["face_labels"]:
                        faces_dict[key]["face_labels"].append(ligne.face.label)

                child_supports = [
                    {
                        "code"    : v["code"],
                        "nom"     : v["nom"],
                        "ville"   : v["ville"],
                        "quartier": v["quartier"],
                        "adresse" : v["adresse"],
                        "type"    : v["type"],
                        "face"    : " & ".join(v["face_labels"]) if v["face_labels"] else "—",
                    }
                    for v in faces_dict.values()
                ]

            supports_by_child.append({
                "campagne": enfant,
                "supports": child_supports,
            })

    # ── Spots par mois (écran uniquement) ────────────────────────
    spots_par_mois = []

    if infos["effective_type_support"] == "ecran":
        spots_by_month = {}
        total_spots = 0
        for campagne_item in campagnes:
            spots_total = campagne_item.calculer_nombre_spots()
            total_spots += spots_total
            for (annee, mois) in _mois_couverts(campagne_item.date_debut, campagne_item.date_fin):
                spots_mois = _spots_du_mois(campagne_item, spots_total, annee, mois)
                if spots_mois == 0:
                    continue
                spots_by_month[(annee, mois)] = spots_by_month.get((annee, mois), 0) + spots_mois

        nb_ecrans = len({
            ligne.support.pk
            for ligne in lignes
            if ligne.support and ligne.support.type_support == "ecran"
        })

        total_general = sum(spots_by_month.values())
        for (annee, mois), spots_mois in sorted(spots_by_month.items()):
            spots_par_mois.append({
                "label"      : f"{MOIS_FR[mois]} {annee}",
                "spots"      : spots_mois,
                "spots_ecran": spots_mois // nb_ecrans if nb_ecrans else 0,
                "nb_ecrans"  : nb_ecrans,
            })

        if spots_par_mois:
            spots_par_mois.append({
                "label"      : "TOTAL",
                "spots"      : total_general,
                "spots_ecran": total_general // nb_ecrans if nb_ecrans else 0,
                "nb_ecrans"  : nb_ecrans,
                "is_total"   : True,
            })

    infos["support_count"] = len(supports)
    infos["total_spots"] = sum(c.calculer_nombre_spots() for c in campagnes)

    return {
        "campagne"         : campagne,
        "client"           : campagne.client,
        "today"            : datetime.date.today(),
        "infos"            : infos,
        "supports"         : supports,
        "supports_by_child": supports_by_child,
        "spots_par_mois"   : spots_par_mois,
    }


# ══════════════════════════════════════════════════════════════════
# VUES CAMPAGNE
# ══════════════════════════════════════════════════════════════════

class ExportCampagnePdfView(ClientStaffRequiredMixin, View):
    def get(self, request, pk):
        """Télécharge le détail d'une campagne en PDF."""
        campagne = get_object_or_404(
            Campagne.objects.select_related("client", "contrat")
                            .prefetch_related(
                                "lignes__support",
                                "lignes__face",
                                "sous_campagnes__lignes__support",
                                "sous_campagnes__lignes__face",
                            ),
            pk=pk,
        )

        html_string = render_to_string(
            "reports/campagne_pdf.html",
            _build_context_campagne(campagne),
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        filename = (
            f"campagne_{campagne.reference}"
            f"_{datetime.datetime.now():%Y%m%d}.pdf"
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PreviewCampagnePdfView(ClientStaffRequiredMixin, View):
    def get(self, request, pk):
        """Prévisualise le détail d'une campagne dans le navigateur."""
        campagne = get_object_or_404(
            Campagne.objects.select_related("client", "contrat")
                            .prefetch_related(
                                "lignes__support",
                                "lignes__face",
                                "sous_campagnes__lignes__support",
                                "sous_campagnes__lignes__face",
                            ),
            pk=pk,
        )
        return render(
            request,
            "reports/apercu_campagne.html",
            _build_context_campagne(campagne),
        )


# ══════════════════════════════════════════════════════════════════
# UTILITAIRES FILTRES & CONTEXTE CLIENT
# ══════════════════════════════════════════════════════════════════

def _extract_client_filters(request):
    return {
        "contrat"     : request.GET.get("contrat", ""),
        "type_support": request.GET.get("type_support", ""),
        "statut"      : request.GET.get("statut", ""),
        "date_debut"  : request.GET.get("date_debut", ""),
        "date_fin"    : request.GET.get("date_fin", ""),
        "annee"       : request.GET.get("annee", ""),
    }


def _apply_campagne_filters(qs, filters):
    if not filters:
        return qs
    statut_f   = filters.get("statut")
    date_deb_f = filters.get("date_debut")
    date_fin_f = filters.get("date_fin")
    annee_f    = filters.get("annee")

    if statut_f:
        qs = qs.filter(statut=statut_f)
    if date_deb_f:
        try:
            d_deb = datetime.datetime.strptime(date_deb_f, "%Y-%m-%d").date()
            qs = qs.filter(date_fin__gte=d_deb)
        except (ValueError, TypeError):
            pass
    if date_fin_f:
        try:
            d_fin = datetime.datetime.strptime(date_fin_f, "%Y-%m-%d").date()
            qs = qs.filter(date_debut__lte=d_fin)
        except (ValueError, TypeError):
            pass
    if annee_f:
        try:
            annee_int = int(annee_f)
            qs = qs.filter(date_debut__year__lte=annee_int, date_fin__year__gte=annee_int)
        except (ValueError, TypeError):
            pass
    return qs


def _build_blocs_ecran(client, filters=None):
    """
    Structure retournée :
    blocs = [
        {
            'contrat'     : <Contrat> ou None,
            'label_total' : str,
            'total_spots' : int,
            'reste_final' : int,
            'mois'        : [
                {
                    'label'       : "JANVIER 2025",
                    'total_spots' : int,
                    'reste_fin'   : int,
                    'lignes'      : [
                        {
                            'num'          : int,
                            'campagne'     : <Campagne>,
                            'freq_display' : str,
                            'nb_ecrans'    : int,
                            'spots'        : int,
                            'spots_total'  : int,
                            'reste'        : int,
                            'reste_display': str,
                            'ecrans_str'   : list of Support,
                        },
                    ],
                },
            ],
        },
    ]
    """
    filters = filters or {}
    contrat_f = filters.get("contrat", "")
    type_f    = filters.get("type_support", "")

    if type_f == "panneau":
        return []

    blocs   = []
    counter = 1

    # 1. Contrats
    if contrat_f != "sans_contrat":
        contrats = (
            client.contrats
            .order_by("date_debut")
            .prefetch_related("campagnes__lignes__support")
        )
        if contrat_f:
            contrats = contrats.filter(pk=contrat_f)

        for contrat in contrats:
            campagnes_qs = (
                contrat.campagnes
                .filter(lignes__support__type_support="ecran")
                .distinct()
                .order_by("date_debut")
            )
            campagnes_qs = _apply_campagne_filters(campagnes_qs, filters)
            campagnes = list(campagnes_qs)
            if not campagnes:
                continue

            spots_par_campagne = {
                c.pk: c.calculer_nombre_spots() for c in campagnes
            }

            tous_les_mois = sorted({
                ym
                for c in campagnes
                for ym in _mois_couverts(c.date_debut, c.date_fin)
            })

            reste               = contrat.nb_spots
            total_spots_contrat = 0
            mois_blocs          = []

            for (annee, mois) in tous_les_mois:
                if filters.get("annee"):
                    try:
                        if int(filters["annee"]) != annee:
                            continue
                    except (ValueError, TypeError):
                        pass

                label_mois       = f"{MOIS_FR[mois]} {annee}"
                lignes_mois      = []
                total_spots_mois = 0

                for campagne in campagnes:
                    spots_mois = _spots_du_mois(
                        campagne, spots_par_campagne[campagne.pk], annee, mois
                    )
                    if spots_mois == 0:
                        continue

                    lignes_ecrans = [
                        l for l in campagne.lignes.all()
                        if l.support and l.support.type_support == "ecran"
                    ]
                    nb_ecrans = len(lignes_ecrans)

                    total_spots_mois    += spots_mois
                    total_spots_contrat += spots_mois
                    reste               -= spots_mois

                    lignes_mois.append({
                        "num"          : counter,
                        "campagne"     : campagne,
                        "freq_display" : _format_freq(campagne),
                        "nb_ecrans"    : nb_ecrans,
                        "spots"        : spots_mois,
                        "spots_total"  : spots_par_campagne[campagne.pk],
                        "reste"        : reste,
                        "reste_display": f"{reste:,}",
                        "ecrans_str"   : lignes_ecrans,
                    })
                    counter += 1

                if lignes_mois:
                    mois_blocs.append({
                        "label"       : label_mois,
                        "total_spots" : total_spots_mois,
                        "reste_fin"   : reste,
                        "lignes"      : lignes_mois,
                    })

            if mois_blocs:
                blocs.append({
                    "contrat"    : contrat,
                    "label_total": (contrat.nom or contrat.get_type_contrat_display()).upper(),
                    "total_spots": total_spots_contrat,
                    "reste_final": reste,
                    "mois"       : mois_blocs,
                })

    # 2. Campagnes orphelines (sans contrat)
    if not contrat_f or contrat_f == "sans_contrat":
        campagnes_orphelines_qs = (
            Campagne.objects
            .filter(
                client=client,
                contrat__isnull=True,
                lignes__support__type_support="ecran",
            )
            .distinct()
            .order_by("date_debut")
            .prefetch_related("lignes__support")
        )
        campagnes_orphelines_qs = _apply_campagne_filters(campagnes_orphelines_qs, filters)
        campagnes_orphelines = list(campagnes_orphelines_qs)

        if campagnes_orphelines:
            spots_par_campagne = {
                c.pk: c.calculer_nombre_spots() for c in campagnes_orphelines
            }

            tous_les_mois = sorted({
                ym
                for c in campagnes_orphelines
                for ym in _mois_couverts(c.date_debut, c.date_fin)
            })

            total_spots_section = 0
            mois_blocs          = []

            for (annee, mois) in tous_les_mois:
                if filters.get("annee"):
                    try:
                        if int(filters["annee"]) != annee:
                            continue
                    except (ValueError, TypeError):
                        pass

                label_mois       = f"{MOIS_FR[mois]} {annee}"
                lignes_mois      = []
                total_spots_mois = 0

                for campagne in campagnes_orphelines:
                    spots_mois = _spots_du_mois(
                        campagne, spots_par_campagne[campagne.pk], annee, mois
                    )
                    if spots_mois == 0:
                        continue

                    lignes_ecrans = [
                        l for l in campagne.lignes.all()
                        if l.support and l.support.type_support == "ecran"
                    ]
                    nb_ecrans = len(lignes_ecrans)

                    total_spots_mois    += spots_mois
                    total_spots_section += spots_mois

                    lignes_mois.append({
                        "num"          : counter,
                        "campagne"     : campagne,
                        "freq_display" : _format_freq(campagne),
                        "nb_ecrans"    : nb_ecrans,
                        "spots"        : spots_mois,
                        "spots_total"  : spots_par_campagne[campagne.pk],
                        "reste"        : None,
                        "reste_display": "",
                        "ecrans_str"   : lignes_ecrans,
                    })
                    counter += 1

                if lignes_mois:
                    mois_blocs.append({
                        "label"       : label_mois,
                        "total_spots" : total_spots_mois,
                        "reste_fin"   : None,
                        "lignes"      : lignes_mois,
                    })

            if mois_blocs:
                blocs.append({
                    "contrat"    : None,
                    "label_total": "SANS CONTRAT",
                    "total_spots": total_spots_section,
                    "reste_final": None,
                    "mois"       : mois_blocs,
                })

    return blocs


def _build_blocs_panneaux(client, filters=None):
    """
    Structure des campagnes panneau d'un client, regroupées par contrat.
    Les faces sont regroupées par support (panneau).
    """
    filters   = filters or {}
    contrat_f = filters.get("contrat", "")
    type_f    = filters.get("type_support", "")

    if type_f == "ecran":
        return []

    blocs   = []
    counter = 1

    # 1. Campagnes avec contrat
    if contrat_f != "sans_contrat":
        contrats = (
            client.contrats
            .order_by("date_debut")
            .prefetch_related("campagnes__lignes__support", "campagnes__lignes__face")
        )
        if contrat_f:
            contrats = contrats.filter(pk=contrat_f)

        for contrat in contrats:
            campagnes_qs = (
                contrat.campagnes
                .filter(type_support="panneau")
                .distinct()
                .order_by("date_debut")
                .prefetch_related("lignes__support", "lignes__face")
            )
            campagnes_qs = _apply_campagne_filters(campagnes_qs, filters)
            campagnes = list(campagnes_qs)
            if not campagnes:
                continue

            lignes = []
            for campagne in campagnes:
                lignes_panneau = [
                    l for l in campagne.lignes.all()
                    if l.support and l.support.type_support == "panneau"
                ]
                faces_dict = {}
                for l in lignes_panneau:
                    key = l.support.pk
                    if key not in faces_dict:
                        faces_dict[key] = {
                            "support_code": l.support.code,
                            "support_nom" : l.support.nom,
                            "face_labels" : [],
                        }
                    if l.face and l.face.label not in faces_dict[key]["face_labels"]:
                        faces_dict[key]["face_labels"].append(l.face.label)

                faces = [
                    {
                        "support_code": v["support_code"],
                        "support_nom" : v["support_nom"],
                        "face_label"  : " & ".join(v["face_labels"]) if v["face_labels"] else "-",
                    }
                    for v in faces_dict.values()
                ]
                nb_faces = sum(len(v["face_labels"]) for v in faces_dict.values())

                lignes.append({
                    "num"        : counter,
                    "campagne"   : campagne,
                    "reference"  : campagne.reference,
                    "date_debut" : campagne.date_debut,
                    "date_fin"   : campagne.date_fin,
                    "duree_jours": campagne.duree_jours(),
                    "statut"     : campagne.get_statut_display(),
                    "notes"      : campagne.notes,
                    "faces"      : faces,
                    "nb_faces"   : nb_faces,
                })
                counter += 1

            if lignes:
                blocs.append({
                    "contrat"    : contrat,
                    "label"      : (contrat.nom or contrat.get_type_contrat_display()).upper(),
                    "total_faces": sum(l["nb_faces"] for l in lignes),
                    "lignes"     : lignes,
                })

    # 2. Campagnes sans contrat
    if not contrat_f or contrat_f == "sans_contrat":
        campagnes_sans_contrat_qs = (
            Campagne.objects
            .filter(client=client, contrat__isnull=True, type_support="panneau")
            .distinct()
            .order_by("date_debut")
            .prefetch_related("lignes__support", "lignes__face")
        )
        campagnes_sans_contrat_qs = _apply_campagne_filters(campagnes_sans_contrat_qs, filters)
        campagnes_sans_contrat = list(campagnes_sans_contrat_qs)

        if campagnes_sans_contrat:
            lignes = []
            for campagne in campagnes_sans_contrat:
                lignes_panneau = [
                    l for l in campagne.lignes.all()
                    if l.support and l.support.type_support == "panneau"
                ]
                faces_dict = {}
                for l in lignes_panneau:
                    key = l.support.pk
                    if key not in faces_dict:
                        faces_dict[key] = {
                            "support_code": l.support.code,
                            "support_nom" : l.support.nom,
                            "face_labels" : [],
                        }
                    if l.face and l.face.label not in faces_dict[key]["face_labels"]:
                        faces_dict[key]["face_labels"].append(l.face.label)

                faces = [
                    {
                        "support_code": v["support_code"],
                        "support_nom" : v["support_nom"],
                        "face_label"  : " & ".join(v["face_labels"]) if v["face_labels"] else "-",
                    }
                    for v in faces_dict.values()
                ]
                nb_faces = sum(len(v["face_labels"]) for v in faces_dict.values())

                lignes.append({
                    "num"        : counter,
                    "campagne"   : campagne,
                    "reference"  : campagne.reference,
                    "date_debut" : campagne.date_debut,
                    "date_fin"   : campagne.date_fin,
                    "duree_jours": campagne.duree_jours(),
                    "statut"     : campagne.get_statut_display(),
                    "notes"      : campagne.notes,
                    "faces"      : faces,
                    "nb_faces"   : nb_faces,
                })
                counter += 1

            if lignes:
                blocs.append({
                    "contrat"    : None,
                    "label"      : "SANS CONTRAT RATTACHÉ",
                    "total_faces": sum(l["nb_faces"] for l in lignes),
                    "lignes"     : lignes,
                })

    return blocs


def _build_context(client, filters=None):
    """Contexte partagé entre export PDF, Excel et preview."""
    filters        = filters or {}
    blocs_ecran    = _build_blocs_ecran(client, filters)
    blocs_panneaux = _build_blocs_panneaux(client, filters)
    contrats       = list(client.contrats.all().order_by("date_debut"))
    today          = datetime.date.today()
    contrats_actifs = [c for c in contrats if c.actif and c.date_debut <= today <= c.date_fin]

    # Années disponibles pour filtre
    all_campaigns_dates = client.campagnes.values_list('date_debut__year', 'date_fin__year')
    annees = set()
    for deb, fin in all_campaigns_dates:
        if deb:
            annees.add(deb)
        if fin:
            annees.add(fin)
    for c in contrats:
        if c.date_debut:
            annees.add(c.date_debut.year)
        if c.date_fin:
            annees.add(c.date_fin.year)
    annees_choices = sorted(list(annees), reverse=True)

    # ── KPI Globaux ──
    total_campagnes_ecran = sum(
        len(mois["lignes"])
        for bloc in blocs_ecran
        for mois in bloc["mois"]
    )
    total_spots_global = sum(
        bloc["total_spots"] for bloc in blocs_ecran
    )
    total_spots_engages = sum(
        bloc["contrat"].nb_spots
        for bloc in blocs_ecran
        if bloc.get("contrat") is not None
    ) or sum(c.nb_spots for c in contrats)

    total_campagnes_panneaux = sum(
        len(bloc["lignes"]) for bloc in blocs_panneaux
    )
    total_faces_global = sum(
        bloc["total_faces"] for bloc in blocs_panneaux
    )

    # ── Résumés Séparés par Contrat (Pas d'addition globale arbitraire) ──
    contrats_resume = []
    blocs_ecran_par_contrat = {}
    bloc_ecran_orphelin = None
    for b in blocs_ecran:
        if b.get("contrat"):
            blocs_ecran_par_contrat[b["contrat"].pk] = b
        else:
            bloc_ecran_orphelin = b

    blocs_panneaux_par_contrat = {}
    bloc_panneau_orphelin = None
    for b in blocs_panneaux:
        if b.get("contrat"):
            blocs_panneaux_par_contrat[b["contrat"].pk] = b
        else:
            bloc_panneau_orphelin = b

    contrat_f = filters.get("contrat", "")
    contrats_a_inclure = contrats
    if contrat_f and contrat_f != "sans_contrat":
        try:
            contrats_a_inclure = [c for c in contrats if str(c.pk) == str(contrat_f)]
        except Exception:
            pass
    elif contrat_f == "sans_contrat":
        contrats_a_inclure = []

    for c in contrats_a_inclure:
        b_ecran = blocs_ecran_par_contrat.get(c.pk)
        b_panneau = blocs_panneaux_par_contrat.get(c.pk)

        spots_engages = c.nb_spots
        spots_diffuses = b_ecran["total_spots"] if b_ecran else 0
        solde_restant = b_ecran["reste_final"] if (b_ecran and b_ecran.get("reste_final") is not None) else spots_engages
        taux_conso = round((spots_diffuses / spots_engages * 100), 1) if spots_engages > 0 else 0

        nb_camp_ecran = sum(len(m["lignes"]) for m in b_ecran["mois"]) if b_ecran else 0
        nb_camp_panneau = len(b_panneau["lignes"]) if b_panneau else 0
        nb_faces = b_panneau["total_faces"] if b_panneau else 0

        est_actif = c.actif and c.date_debut <= today <= c.date_fin

        contrats_resume.append({
            "contrat"             : c,
            "nom"                 : c.nom or c.get_type_contrat_display(),
            "type_display"        : c.get_type_contrat_display(),
            "date_debut"          : c.date_debut,
            "date_fin"            : c.date_fin,
            "est_actif"           : est_actif,
            "statut_display"      : "En cours" if est_actif else ("Échu" if c.date_fin < today else "À venir"),
            "spots_engages"       : spots_engages,
            "spots_diffuses"      : spots_diffuses,
            "solde_restant"       : solde_restant,
            "taux_consommation"   : taux_conso,
            "nb_campagnes_ecran"  : nb_camp_ecran,
            "nb_campagnes_panneau": nb_camp_panneau,
            "nb_faces"            : nb_faces,
            "total_campagnes"     : nb_camp_ecran + nb_camp_panneau,
        })

    # Hors contrat si présent
    if (bloc_ecran_orphelin or bloc_panneau_orphelin) and (not contrat_f or contrat_f == "sans_contrat"):
        spots_diffuses_hc = bloc_ecran_orphelin["total_spots"] if bloc_ecran_orphelin else 0
        nb_camp_ecran_hc = sum(len(m["lignes"]) for m in bloc_ecran_orphelin["mois"]) if bloc_ecran_orphelin else 0
        nb_camp_panneau_hc = len(bloc_panneau_orphelin["lignes"]) if bloc_panneau_orphelin else 0
        nb_faces_hc = bloc_panneau_orphelin["total_faces"] if bloc_panneau_orphelin else 0

        contrats_resume.append({
            "contrat"             : None,
            "nom"                 : "Campagnes Hors Contrat",
            "type_display"        : "Diffusions ponctuelles sans contrat cadre",
            "date_debut"          : None,
            "date_fin"            : None,
            "est_actif"           : False,
            "statut_display"      : "Ponctuel",
            "spots_engages"       : None,
            "spots_diffuses"      : spots_diffuses_hc,
            "solde_restant"       : None,
            "taux_consommation"   : None,
            "nb_campagnes_ecran"  : nb_camp_ecran_hc,
            "nb_campagnes_panneau": nb_camp_panneau_hc,
            "nb_faces"            : nb_faces_hc,
            "total_campagnes"     : nb_camp_ecran_hc + nb_camp_panneau_hc,
        })

    return {
        "client"                  : client,
        "contrats"                : contrats,
        "contrats_actifs"         : contrats_actifs,
        "contrats_resume"         : contrats_resume,
        "total_spots_engages"     : total_spots_engages,
        "blocs_ecran"             : blocs_ecran,
        "blocs_panneaux"          : blocs_panneaux,
        "today"                   : today,
        "filters"                 : filters,
        "annees_choices"          : annees_choices,
        # KPI globaux
        "total_campagnes_ecran"   : total_campagnes_ecran,
        "total_spots_global"      : total_spots_global,
        "total_campagnes_panneaux": total_campagnes_panneaux,
        "total_faces_global"      : total_faces_global,
    }


# ══════════════════════════════════════════════════════════════════
# VUES CLIENT
# ══════════════════════════════════════════════════════════════════

class ExportClientPdfView(ClientStaffRequiredMixin, View):
    def get(self, request, pk):
        """Télécharge le planning de diffusion d'un client en PDF."""
        client  = get_object_or_404(Client, pk=pk)
        filters = _extract_client_filters(request)

        html_string = render_to_string(
            "reports/client_pdf.html",
            _build_context(client, filters),
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        filename = (
            f"rapport_diffusion_{client.nom.replace(' ', '_')}"
            f"_{datetime.datetime.now():%Y%m%d}.pdf"
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PreviewClientPdfView(ClientStaffRequiredMixin, View):
    def get(self, request, pk):
        """Prévisualise le planning de diffusion dans le navigateur avec filtres."""
        client  = get_object_or_404(Client, pk=pk)
        filters = _extract_client_filters(request)
        return render(request, "reports/apercu_client.html", _build_context(client, filters))


class ExportClientExcelView(ClientStaffRequiredMixin, View):
    def get(self, request, pk):
        client  = get_object_or_404(Client, pk=pk)
        filters = _extract_client_filters(request)
        ctx     = _build_context(client, filters)

        buffer = io.BytesIO()

        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            titre_font  = Font(bold=True, size=13, name="Arial", color="1E293B")
            header_fill = PatternFill("solid", start_color="1E293B", end_color="1E293B")
            header_font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
            thin_border = Border(
                left=Side(style="thin", color="E2E8F0"),
                right=Side(style="thin", color="E2E8F0"),
                top=Side(style="thin", color="E2E8F0"),
                bottom=Side(style="thin", color="E2E8F0"),
            )

            has_sheet = False

            # ── 1. Feuille Écrans ──
            if ctx["blocs_ecran"]:
                rows_ecran = []
                for bloc in ctx["blocs_ecran"]:
                    for mois in bloc["mois"]:
                        for l in mois["lignes"]:
                            ecrans_val = " • ".join(s.nom for s in l["ecrans_str"]) if isinstance(l["ecrans_str"], list) else str(l["ecrans_str"])
                            c = l["campagne"]
                            rows_ecran.append({
                                "N°": l["num"],
                                "Campagne": c.nom,
                                "Contrat": bloc["label_total"],
                                "Mois": mois["label"],
                                "Période": f"{c.date_debut:%d/%m/%Y} → {c.date_fin:%d/%m/%Y}",
                                "Écrans": ecrans_val,
                                "Fréquence": l["freq_display"],
                                "Spots Mois": l["spots"],
                                "Total Campagne": l["spots_total"],
                                "Solde Restant": l["reste_display"] if l["reste_display"] else "—",
                            })

                if rows_ecran:
                    df_ecran = pd.DataFrame(rows_ecran)
                    df_ecran.to_excel(writer, index=False, sheet_name="Écrans Numériques", startrow=2)
                    ws = writer.sheets["Écrans Numériques"]
                    ws["A1"] = f"Rapport de Diffusion Écrans — {client.nom}"
                    ws["A1"].font = titre_font

                    for cell in ws[3]:
                        cell.fill = header_fill
                        cell.font = header_font
                        cell.alignment = Alignment(horizontal="center", vertical="center")

                    for row in ws.iter_rows(min_row=4, max_row=ws.max_row):
                        for cell in row:
                            cell.border = thin_border
                            cell.alignment = Alignment(vertical="center")

                    for col in ws.columns:
                        max_len = max((len(str(c.value or '')) for c in col), default=10)
                        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(max_len + 4, 12), 50)

                    ws.freeze_panes = "A4"
                    has_sheet = True

            # ── 2. Feuille Panneaux ──
            if ctx["blocs_panneaux"]:
                rows_panneaux = []
                for bloc in ctx["blocs_panneaux"]:
                    for l in bloc["lignes"]:
                        faces_str = " ; ".join(f"{f['support_nom']} ({f['face_label']})" for f in l["faces"])
                        c = l["campagne"]
                        rows_panneaux.append({
                            "N°": l["num"],
                            "Campagne": c.nom,
                            "Référence": l["reference"],
                            "Contrat": bloc["label"],
                            "Période": f"{l['date_debut']:%d/%m/%Y} → {l['date_fin']:%d/%m/%Y}",
                            "Durée (j)": l["duree_jours"],
                            "Statut": l["statut"],
                            "Supports & Faces": faces_str,
                            "Nb Faces": l["nb_faces"],
                        })

                if rows_panneaux:
                    df_panneaux = pd.DataFrame(rows_panneaux)
                    df_panneaux.to_excel(writer, index=False, sheet_name="Panneaux Statiques", startrow=2)
                    ws = writer.sheets["Panneaux Statiques"]
                    ws["A1"] = f"Rapport d'Affichage Panneaux — {client.nom}"
                    ws["A1"].font = titre_font

                    for cell in ws[3]:
                        cell.fill = header_fill
                        cell.font = header_font
                        cell.alignment = Alignment(horizontal="center", vertical="center")

                    for row in ws.iter_rows(min_row=4, max_row=ws.max_row):
                        for cell in row:
                            cell.border = thin_border
                            cell.alignment = Alignment(vertical="center")

                    for col in ws.columns:
                        max_len = max((len(str(c.value or '')) for c in col), default=10)
                        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(max_len + 4, 12), 50)

                    ws.freeze_panes = "A4"
                    has_sheet = True

            # Si aucune donnée filtrée
            if not has_sheet:
                df_empty = pd.DataFrame([{"Information": "Aucune donnée correspondant aux critères de filtre."}])
                df_empty.to_excel(writer, index=False, sheet_name="Synthèse", startrow=2)
                ws = writer.sheets["Synthèse"]
                ws["A1"] = f"Rapport de Diffusion — {client.nom}"
                ws["A1"].font = titre_font

        buffer.seek(0)

        filename = (
            f"rapport_diffusion_{client.nom.replace(' ', '_')}"
            f"_{datetime.datetime.now():%Y%m%d}.xlsx"
        )
        response = HttpResponse(
            buffer,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ══════════════════════════════════════════════════════════════════
# RAPPORT DÉTAILLÉ DE SUPPORT INDIVIDUEL (PDF & APERÇU)
# ══════════════════════════════════════════════════════════════════

def _build_context_support_detail(support, request=None):
    """
    Construit le contexte complet et exhaustif pour la fiche technique,
    les diffusions, les campagnes & planning, et le journal de maintenance du support.
    """
    today = datetime.date.today()
    from django.utils import timezone
    now = timezone.now()
    from campaigns.models import LigneCampagne, ReservationLigne

    # ── 1. Fiche Technique complète ──
    info_rows = [
        ('Code Réseau', support.code),
        ('Nom du Support', support.nom),
        ('Typologie', support.get_type_support_display()),
        ('Format Réseau', support.get_format_display() or support.format or 'Standard'),
        ('Dimensions (L × H)', support.dimensions or 'Standard'),
        ('Superficie Totale', f"{support.surface_m2} m²" if support.surface_m2 else '—'),
        ('Ville', support.ville or '—'),
        ('Quartier', support.quartier or '—'),
        ('Adresse / Repère', support.adresse or '—'),
        ('Coordonnées GPS', f"{support.latitude}, {support.longitude}" if support.latitude and support.longitude else '—'),
        ('Date d\'Installation', support.date_installation.strftime('%d/%m/%Y') if support.date_installation else '—'),
        ('État Opérationnel', support.get_etat_display()),
    ]

    if getattr(support, 'code_mairie', None):
        info_rows.insert(1, ('Code Mairie', support.code_mairie))

    if support.is_dans_marche:
        info_rows.append(('Marché Commercial', support.marche.nom))
        info_rows.append(('Emplacement Marché', support.emplacement.code if support.emplacement else '—'))

    if support.type_support == 'panneau':
        if support.type_panneau:
            info_rows.append(('Type de Structure', support.type_panneau))
        info_rows.append(('Capacité Faces', f"{support.faces.count()} Face{'s' if support.faces.count() > 1 else ''}"))

    # ── 2. Campagnes Actives, Réservations & Historique ──
    lignes_actives = list(
        LigneCampagne.objects.filter(
            support=support,
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).select_related('campagne__client', 'face').prefetch_related('campagne__visuels')
    )

    reservations = list(
        ReservationLigne.objects.filter(
            support=support,
            reservation__date_fin__gte=now,
            reservation__statut__in=['en_attente', 'confirmee'],
        ).select_related('reservation__client', 'face').order_by('reservation__date_debut')
    )

    historique = list(
        LigneCampagne.objects.filter(
            support=support,
            campagne__date_fin__lt=today,
        ).select_related('campagne__client', 'face').prefetch_related('campagne__visuels').order_by('-campagne__date_fin')[:15]
    )

    # ── 3. Maintenance & Interventions ──
    maintenances = list(
        support.maintenances.select_related('effectue_par', 'face').order_by('-date_intervention')
    )

    # ── 4. Faces & Visuels (Panneau) ──
    faces_data = []
    if support.type_support == 'panneau':
        for face in support.faces.all():
            statut = face.get_statut()
            camp_active = None
            visuel_actif = None
            for l in lignes_actives:
                if l.face_id == face.id:
                    camp_active = l.campagne
                    visuel_actif = l.visuel or (camp_active.visuels.first() if camp_active.visuels.exists() else None)
                    break
            
            # Réservation en attente sur cette face
            resa_active = None
            for r in reservations:
                if r.face_id == face.id:
                    resa_active = r.reservation
                    break

            faces_data.append({
                'face': face,
                'label': face.label,
                'eclairage': face.get_eclairage_display(),
                'notes': face.notes,
                'photo': face.photo,
                'statut': statut,
                'campagne': camp_active,
                'visuel_actif': visuel_actif,
                'reservation': resa_active,
            })

    # ── 5. DOOH Écran Numérique ──
    ecran_info = getattr(support, 'ecran_info', None)
    spots_data = []
    if support.type_support == 'ecran' and ecran_info:
        info_rows.append(('Résolution Écran', ecran_info.get_resolution_display()))
        if hasattr(ecran_info, 'format_detail') and ecran_info.format_detail:
            info_rows.append(('Cellules / Dalle', f"{ecran_info.format_detail.get('dimensions','')} ({ecran_info.format_detail.get('superficie','')}m²)"))
        h_on = ecran_info.heure_allumage.strftime('%H:%M') if ecran_info.heure_allumage else '06:00'
        h_off = ecran_info.heure_extinction.strftime('%H:%M') if ecran_info.heure_extinction else '22:00'
        info_rows.append(('Plage d\'Allumage', f"{h_on} — {h_off}"))
        info_rows.append(('Taux d\'Occupation', f"{support.taux_occupation_pourcentage}%"))

        for l in lignes_actives:
            visuel = l.visuel or (l.campagne.visuels.first() if l.campagne.visuels.exists() else None)
            freq = l.campagne.frequence or 120
            duree = l.campagne.duree_passage or 10
            spots_data.append({
                'campagne': l.campagne,
                'client': l.campagne.client,
                'duree': duree,
                'freq': freq,
                'passages_heure': 3600 // freq if freq > 0 else 30,
                'visuel': visuel,
            })

    return {
        'support': support,
        'today': today,
        'info_rows': info_rows,
        'faces_data': faces_data,
        'ecran_info': ecran_info,
        'spots_data': spots_data,
        'lignes_actives': lignes_actives,
        'reservations': reservations,
        'historique': historique,
        'maintenances': maintenances,
    }


class ExportSupportPdfView(ClientStaffRequiredMixin, View):
    """Télécharge la fiche détaillée et le rapport d'un support en PDF."""
    def get(self, request, pk=None, uuid=None):
        from inventory.models import Support
        if uuid:
            support = get_object_or_404(Support, uuid=uuid)
        else:
            support = get_object_or_404(Support, pk=pk)

        context = _build_context_support_detail(support, request=request)
        html_string = render_to_string(
            "reports/support_detail_pdf.html",
            context,
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        filename = f"rapport_support_{support.code}_{datetime.datetime.now():%Y%m%d_%H%M%S}.pdf"
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PreviewSupportPdfView(ClientStaffRequiredMixin, View):
    """Prévisualise le rapport détaillé du support dans le navigateur."""
    def get(self, request, pk=None, uuid=None):
        from inventory.models import Support
        if uuid:
            support = get_object_or_404(Support, uuid=uuid)
        else:
            support = get_object_or_404(Support, pk=pk)

        context = _build_context_support_detail(support, request=request)
        return render(
            request,
            "reports/apercu_support_detail.html",
            context,
        )


# ══════════════════════════════════════════════════════════════════
# RAPPORT DÉTAILLÉ DE MARCHÉ (PDF & APERÇU)
# ══════════════════════════════════════════════════════════════════

def _build_context_marche_detail(marche, request=None):
    """
    Construit le contexte complet pour la fiche et le rapport détaillé d'un Marché :
    localisation, statistiques, liste des emplacements et panneaux, campagnes actives,
    réservations, maintenances et photos.
    """
    from django.db.models import Q
    from django.utils import timezone
    from inventory.models import Support, Maintenance
    from campaigns.models import LigneCampagne, ReservationLigne

    today = timezone.now().date()
    now = timezone.now()

    info_rows = [
        ('Nom du Marché', marche.nom),
        ('Ville', marche.ville or '—'),
        ('Quartier', marche.quartier or '—'),
        ('Adresse / Localisation', marche.adresse or '—'),
        ('Coordonnées GPS', f"{marche.latitude}, {marche.longitude}" if marche.latitude and marche.longitude else '—'),
        ('Rayon du Périmètre', f"{marche.rayon_metres} mètres"),
        ('Capacité Totale', f"{marche.nb_emplacements} emplacement{'s' if marche.nb_emplacements > 1 else ''}"),
        ('Statut Opérationnel', 'Actif / En exploitation' if marche.actif else 'Inactif'),
        ('Date d\'Enregistrement', marche.created_at.strftime('%d/%m/%Y') if marche.created_at else '—'),
    ]

    # Emplacements et Statuts
    emplacements_qs = marche.emplacements.select_related('support_installe').prefetch_related('support_installe__faces').all()
    emplacements_data = []
    
    for emp in emplacements_qs:
        supp = getattr(emp, 'support_installe', None)
        
        # Campagne active sur cet emplacement ou son support
        q_filter = Q(emplacement=emp)
        if supp:
            q_filter |= Q(support=supp)
        
        lignes_emp = list(
            LigneCampagne.objects.filter(
                q_filter,
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
                campagne__statut__in=['en_cours', 'a_venir'],
            ).select_related('campagne__client', 'face').prefetch_related('campagne__visuels')
        )
        campagne_active = lignes_emp[0].campagne if lignes_emp else None
        visuel_url = None
        if lignes_emp:
            if lignes_emp[0].visuel:
                visuel_url = lignes_emp[0].visuel.url
            elif campagne_active and campagne_active.visuels.exists():
                visuel_url = campagne_active.visuels.first().fichier.url
        
        # Réservation active
        resa_active = None
        if supp:
            resa = ReservationLigne.objects.filter(
                support=supp,
                reservation__date_fin__gte=now,
                reservation__statut__in=['en_attente', 'confirmee'],
            ).select_related('reservation__client').first()
            if resa:
                resa_active = resa.reservation

        emplacements_data.append({
            'emplacement': emp,
            'code': emp.code,
            'notes': emp.notes,
            'is_libre': emp.is_libre(),
            'est_dans_campagne': emp.est_dans_campagne or bool(lignes_emp),
            'support': supp,
            'campagne_active': campagne_active,
            'visuel_url': visuel_url,
            'reservation_active': resa_active,
        })

    # Campagnes actives globales dans le marché
    campagnes_actives = list(
        LigneCampagne.objects.filter(
            Q(emplacement__marche=marche) | Q(support__emplacement__marche=marche),
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).select_related('campagne__client', 'emplacement', 'support', 'face').prefetch_related('campagne__visuels').distinct()
    )

    # Réservations futures dans le marché
    reservations = list(
        ReservationLigne.objects.filter(
            support__emplacement__marche=marche,
            reservation__date_fin__gte=now,
            reservation__statut__in=['en_attente', 'confirmee'],
        ).select_related('reservation__client', 'support', 'face').order_by('reservation__date_debut')
    )

    # Historique récent
    historique = list(
        LigneCampagne.objects.filter(
            Q(emplacement__marche=marche) | Q(support__emplacement__marche=marche),
            campagne__date_fin__lt=today,
        ).select_related('campagne__client', 'emplacement', 'support', 'face').order_by('-campagne__date_fin')[:20]
    )

    # Maintenances dans le marché
    maintenances = list(
        Maintenance.objects.filter(
            support__emplacement__marche=marche
        ).select_related('support', 'effectue_par', 'face').order_by('-date_intervention')[:20]
    )

    # Photos
    photos = []
    for emp_d in emplacements_data:
        supp = emp_d['support']
        if supp and supp.photo_principale:
            photos.append({'url': supp.photo_principale.url, 'label': f"Support {supp.code} (Emplacement {emp_d['code']})"})
        if emp_d.get('visuel_url'):
            photos.append({'url': emp_d['visuel_url'], 'label': f"Visuel Actif — Emplacement {emp_d['code']}"})

    for m in maintenances:
        if m.photo:
            photos.append({'url': m.photo.url, 'label': f"Maintenance Support {m.support.code} ({m.date_intervention.strftime('%d/%m/%Y')})"})

    return {
        'marche': marche,
        'today': today,
        'info_rows': info_rows,
        'emplacements_data': emplacements_data,
        'campagnes_actives': campagnes_actives,
        'reservations': reservations,
        'historique': historique,
        'maintenances': maintenances,
        'photos': photos,
    }


class ExportMarchePdfView(ClientStaffRequiredMixin, View):
    """Télécharge la fiche et le rapport détaillé d'un Marché en PDF."""
    def get(self, request, pk=None):
        from inventory.models import Marche
        marche = get_object_or_404(Marche, pk=pk)
        context = _build_context_marche_detail(marche, request=request)
        html_string = render_to_string(
            "reports/marche_detail_pdf.html",
            context,
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        filename = f"rapport_marche_{marche.pk}_{datetime.datetime.now():%Y%m%d_%H%M%S}.pdf"
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PreviewMarchePdfView(ClientStaffRequiredMixin, View):
    """Prévisualise le rapport détaillé du Marché dans le navigateur."""
    def get(self, request, pk=None):
        from inventory.models import Marche
        marche = get_object_or_404(Marche, pk=pk)
        context = _build_context_marche_detail(marche, request=request)
        return render(
            request,
            "reports/apercu_marche_detail.html",
            context,
        )


# ══════════════════════════════════════════════════════════════════
# RAPPORT DÉTAILLÉ D'EMPLACEMENT (PDF & APERÇU)
# ══════════════════════════════════════════════════════════════════

def _build_context_emplacement_detail(emplacement, request=None):
    """
    Construit le contexte complet pour la fiche et le rapport détaillé d'un Emplacement :
    caractéristiques, marché parent, panneau installé, campagnes, réservations et maintenances.
    """
    from django.db.models import Q
    from django.utils import timezone
    from inventory.models import Support, Maintenance
    from campaigns.models import LigneCampagne, ReservationLigne

    today = timezone.now().date()
    now = timezone.now()
    marche = emplacement.marche
    support = getattr(emplacement, 'support_installe', None)

    info_rows = [
        ('Code Emplacement', emplacement.code),
        ('Marché Commercial', marche.nom),
        ('Ville', marche.ville or '—'),
        ('Quartier', marche.quartier or '—'),
        ('Adresse du Marché', marche.adresse or '—'),
        ('Coordonnées GPS Marché', f"{marche.latitude}, {marche.longitude}" if marche.latitude and marche.longitude else '—'),
        ('Rayon / Périmètre', f"{marche.rayon_metres} mètres"),
        ('Disponibilité Support', 'Libre (aucun panneau installé)' if emplacement.is_libre() else f"Installé : {support.code} ({support.nom})"),
        ('Statut Publicitaire', 'En campagne active' if emplacement.est_dans_campagne else 'Disponible / Hors campagne'),
        ('Date Création', emplacement.created_at.strftime('%d/%m/%Y') if emplacement.created_at else '—'),
    ]

    support_info_rows = []
    faces_data = []
    if support:
        support_info_rows = [
            ('Code Support', support.code),
            ('Nom du Support', support.nom),
            ('Typologie', support.get_type_support_display()),
            ('Format Réseau', support.get_format_display() or support.format or 'Standard'),
            ('Dimensions (L × H)', support.dimensions or 'Standard'),
            ('Superficie', f"{support.surface_m2} m²" if support.surface_m2 else '—'),
            ('État Opérationnel', support.get_etat_display()),
            ('Date d\'Installation', support.date_installation.strftime('%d/%m/%Y') if support.date_installation else '—'),
        ]
        if getattr(support, 'code_mairie', None):
            support_info_rows.insert(1, ('Code Mairie', support.code_mairie))

        for face in support.faces.all():
            faces_data.append({
                'face': face,
                'label': face.label,
                'eclairage': face.get_eclairage_display(),
                'statut': face.get_statut(),
                'photo': face.photo,
                'notes': face.notes,
            })

    # Campagnes actives
    q_filter = Q(emplacement=emplacement)
    if support:
        q_filter |= Q(support=support)

    lignes_actives = list(
        LigneCampagne.objects.filter(
            q_filter,
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).select_related('campagne__client', 'face').prefetch_related('campagne__visuels')
    )

    # Réservations futures
    reservations = []
    if support:
        reservations = list(
            ReservationLigne.objects.filter(
                support=support,
                reservation__date_fin__gte=now,
                reservation__statut__in=['en_attente', 'confirmee'],
            ).select_related('reservation__client', 'face').order_by('reservation__date_debut')
        )

    # Historique des campagnes
    historique = list(
        LigneCampagne.objects.filter(
            q_filter,
            campagne__date_fin__lt=today,
        ).select_related('campagne__client', 'face').order_by('-campagne__date_fin')[:15]
    )

    # Maintenances
    maintenances = []
    if support:
        maintenances = list(
            support.maintenances.select_related('effectue_par', 'face').order_by('-date_intervention')[:15]
        )

    # Photos
    photos = []
    if support and support.photo_principale:
        photos.append({'url': support.photo_principale.url, 'label': 'Photo Principale du Support'})
    for f in faces_data:
        if f['photo']:
            photos.append({'url': f['photo'].url, 'label': f"Face {f['label']} — Structure"})
    for l in lignes_actives:
        if l.visuel:
            photos.append({'url': l.visuel.url, 'label': f"Visuel — {l.campagne.nom}"})
        elif l.campagne.visuels.exists():
            photos.append({'url': l.campagne.visuels.first().fichier.url, 'label': f"Visuel — {l.campagne.nom}"})
    for m in maintenances:
        if m.photo:
            photos.append({'url': m.photo.url, 'label': f"Maintenance du {m.date_intervention.strftime('%d/%m/%Y')}"})

    return {
        'emplacement': emplacement,
        'marche': marche,
        'support': support,
        'today': today,
        'info_rows': info_rows,
        'support_info_rows': support_info_rows,
        'faces_data': faces_data,
        'lignes_actives': lignes_actives,
        'reservations': reservations,
        'historique': historique,
        'maintenances': maintenances,
        'photos': photos,
    }


class ExportEmplacementPdfView(ClientStaffRequiredMixin, View):
    """Télécharge la fiche et le rapport détaillé d'un Emplacement en PDF."""
    def get(self, request, pk=None):
        from inventory.models import Emplacement
        emplacement = get_object_or_404(Emplacement, pk=pk)
        context = _build_context_emplacement_detail(emplacement, request=request)
        html_string = render_to_string(
            "reports/emplacement_detail_pdf.html",
            context,
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        filename = f"rapport_emplacement_{emplacement.code}_{datetime.datetime.now():%Y%m%d_%H%M%S}.pdf"
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PreviewEmplacementPdfView(ClientStaffRequiredMixin, View):
    """Prévisualise le rapport détaillé de l'Emplacement dans le navigateur."""
    def get(self, request, pk=None):
        from inventory.models import Emplacement
        emplacement = get_object_or_404(Emplacement, pk=pk)
        context = _build_context_emplacement_detail(emplacement, request=request)
        return render(
            request,
            "reports/apercu_emplacement_detail.html",
            context,
        )


