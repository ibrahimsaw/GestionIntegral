"""
Rapport d'état des supports — vue, contexte et export PDF/Excel.
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
from campaigns.models import Client, LigneCampagne, ReservationLigne, STATUT_CONFIRMEE
from inventory.models import *
from inventory.sorting import cle_code_support
from django.utils import timezone
from django.db.models import Case, CharField, Exists, OuterRef, Prefetch, Q, Subquery, Value, When


# ══════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════

OCCUPATION_CHOICES = {
    'libre': 'Libre',
    'occupe': 'Occupé',
    'reserve': 'Réservé',
}


# ══════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════



def _occupation_label(faces_data):
    nb_total   = len(faces_data)
    nb_panne   = sum(1 for f in faces_data if f['statut'] == 'panne')
    nb_occupe  = sum(1 for f in faces_data if f['statut'] == 'occupe')
    nb_reserve = sum(1 for f in faces_data if f['statut'] == 'reserve')

    if nb_total == 0:
        return 'libre'
    if nb_panne == nb_total:
        return 'panne'                          # toutes les faces en panne
    if nb_occupe == nb_total:
        return 'total_occupe'
    if nb_reserve == nb_total and nb_occupe == 0:
        return 'total_reserve'
    if nb_occupe > 0:
        return 'occupe'
    if nb_reserve > 0:
        return 'reserve'
    return 'libre'


def _match_filter(occupation_label, filter_occupation):
    """Vérifie si le label d'occupation du support correspond au filtre choisi."""
    if not filter_occupation:
        return True

    mapping = {
        'occupe'          : lambda o: o in ('occupe', 'total_occupe'),
        'libre'           : lambda o: o == 'libre',
        'reserve'         : lambda o: o in ('reserve', 'total_reserve'),
        'total_occupe'    : lambda o: o == 'total_occupe',
        'total_reserve'   : lambda o: o == 'total_reserve',
        'non_reserve'     : lambda o: o in ('libre', 'occupe'),
        'occupe_ou_reserve': lambda o: o != 'libre',
    }
    fn = mapping.get(filter_occupation)
    return fn(occupation_label) if fn else True


def _group_supports_by_quartier(panneaux):
    grouped = {}
    for panneau in panneaux:
        ville = (panneau.get("ville") or "").strip()
        quartier = (panneau.get("quartier") or "").strip()
        key = (ville.casefold(), quartier.casefold())
        grouped.setdefault(key, {
            "ville": ville or "Ville non renseignée",
            "quartier": quartier or "Quartier non renseigné",
            "items": [],
        })["items"].append(panneau)

    sections = list(grouped.values())
    for section in sections:
        section["nom"] = f'{section["ville"]} · {section["quartier"]}'
        section["total"] = len(section["items"])
        section["unites"] = sum(item["nb_faces"] for item in section["items"])
        section["items"].sort(key=lambda item: cle_code_support(item["code"]))

    return sorted(sections, key=lambda section: (
        section["ville"].casefold(),
        section["quartier"].casefold(),
    ))


# ══════════════════════════════════════════════════════════════════
# CONSTRUCTION DU CONTEXTE
# ══════════════════════════════════════════════════════════════════

def _build_context_panneaux(filters: dict) -> dict:
    today = date.today()
    now   = timezone.now()

    jour = timezone.localdate(now)
    campagne_active = (
        Q(campagne__statut__in=('en_cours', 'a_venir'),
          campagne__date_debut__lte=jour, campagne__date_fin__gte=jour)
        | (Q(campagne__statut='en_pause', campagne__date_debut__lte=jour)
           & (Q(campagne__date_fin__gte=jour) | Q(campagne__date_mise_en_pause__lte=jour)))
    )
    lignes_actives = LigneCampagne.objects.filter(campagne_active)
    reservations_actives = ReservationLigne.objects.filter(
        face_id=OuterRef('pk'), reservation__statut=STATUT_CONFIRMEE,
        reservation__date_debut__lt=now, reservation__date_fin__gt=now,
    )
    afficher_client = filters.get('afficher_client', '1') == '1'
    faces_rapport = FacePanneau.objects.annotate(
        statut_rapport=Case(
            When(etat=Support.ETAT_PANNE, then=Value('panne')),
            When(Exists(lignes_actives.filter(face_id=OuterRef('pk'))), then=Value('occupe')),
            When(Exists(reservations_actives), then=Value('reserve')),
            default=Value('libre'),
            output_field=CharField(),
        ),
    )
    if afficher_client:
        reservation_rapport = reservations_actives.order_by('pk')
        faces_rapport = faces_rapport.annotate(
            client_reservation_rapport=Subquery(
                reservation_rapport.values('reservation__client__nom')[:1],
            ),
            debut_reservation_rapport=Subquery(reservation_rapport.values('reservation__date_debut')[:1]),
            fin_reservation_rapport=Subquery(reservation_rapport.values('reservation__date_fin')[:1]),
        ).prefetch_related(Prefetch(
            'lignes_campagne',
            queryset=LigneCampagne.objects.filter(campagne__date_fin__gte=today)
                .select_related('campagne__client').order_by('campagne__date_debut', 'campagne_id'),
            to_attr='lignes_rapport',
        ))

    # ── Base queryset ────────────────────────────────────────────
    qs = (
        Support.objects.filter(actif=True)
        .annotate(ecran_occupe_rapport=Exists(lignes_actives.filter(support_id=OuterRef('pk'))))
        .prefetch_related(Prefetch('faces', queryset=faces_rapport, to_attr='faces_rapport'))
        .order_by("ville", "quartier", "nom")
    )

    # ── Filtres simples ──────────────────────────────────────────
    ville     = filters.get("ville",     "").strip()
    quartier  = filters.get("quartier",  "").strip()
    statut    = filters.get("statut",    "").strip()
    q         = filters.get("q",         "").strip()
    client_pk = filters.get("client_pk", "")
    marche_pk = filters.get("marche_pk", "").strip()
    format_code = filters.get("format", "").strip()
    categories = filters.get("categories", [])
    if isinstance(categories, str):
        categories = [categories] if categories else []

    format_codes = set(
        Support.objects.filter(
            actif=True,
            type_support=Support.TYPE_PANNEAU,
        )
        .exclude(format="")
        .values_list("format", flat=True)
    )
    formats_supports = list(
        FormatSupport.objects.filter(
            code__in=format_codes,
        ).order_by("categorie", "code")
    )
    formats_par_categorie = {}
    for format_support in formats_supports:
        if format_support.categorie:
            formats_par_categorie.setdefault(
                format_support.categorie,
                [],
            ).append(format_support.code)

    if categories:
        non_market_filters = Q()
        if "Panneau" in categories:
            non_market_filters |= Q(type_support=Support.TYPE_PANNEAU, emplacement__isnull=True) & ~Q(format="1x2")
        if "Sucette" in categories:
            non_market_filters |= Q(type_support=Support.TYPE_PANNEAU, format="1x2", emplacement__isnull=True)
        if "Écran" in categories:
            non_market_filters |= Q(type_support=Support.TYPE_ECRAN)
        if "Marché" in categories:
            non_market_filters |= Q(emplacement__isnull=False)
        for categorie in categories:
            codes_formats = formats_par_categorie.get(categorie)
            if codes_formats:
                non_market_filters |= Q(
                    type_support=Support.TYPE_PANNEAU,
                    format__in=codes_formats,
                )
        qs = qs.filter(non_market_filters) if non_market_filters else qs.none()
    elif marche_pk:
        qs = qs.filter(emplacement__marche_id=marche_pk)
    else:
        qs = qs.exclude(emplacement__isnull=False)

    include_markets = (
        not format_code
        and (not categories or "Marché" in categories or bool(marche_pk))
    )
    if categories and categories == ["Marché"] and not marche_pk:
        qs = qs.none()

    if ville:
        qs = qs.filter(ville__iexact=ville)
    if quartier:
        qs = qs.filter(quartier__iexact=quartier)
    if format_code:
        qs = qs.filter(format=format_code)
    if statut:
        qs = qs.filter(etat=statut)
    if q:
        qs = (
            qs.filter(nom__icontains=q)
            | qs.filter(code__icontains=q)
            | qs.filter(adresse__icontains=q)
        )
    # Le filtre client est plus complexe car il nécessite de vérifier les campagnes actives sur les faces , meme celles reservees, pour éviter de montrer un panneau comme libre alors qu'il est réservé pour un client donné
    if client_pk:
        qs = qs.filter(
            Q(lignes_campagne__campagne__client_id=client_pk) |  # faces avec campagne active
            Q(faces__lignes_reservation__reservation__client_id=client_pk)       # faces réservées directement
        ).distinct()

    # ── Construction des lignes ──────────────────────────────────
    filter_occupation = filters.get("occupation", "").strip()

    panneaux       = []
    total_faces    = 0
    total_occupees = 0
    total_reservees = 0
    total_panne = 0
    total_libres = 0

    for support in qs:
        faces_data = []

        if support.type_support == Support.TYPE_ECRAN:
            statut_ecran = 'panne' if support.etat == Support.ETAT_PANNE else 'occupe' if support.ecran_occupe_rapport else 'libre'
            faces_data.append({
                "face": None,
                "label": "Écran",
                "statut": statut_ecran,
                "occupee": statut_ecran == 'occupe',
                "reservee": False,
                "en_panne": statut_ecran == 'panne',
                "campagnes": [],
            })

        for face in support.faces_rapport:
            campagnes = {ligne.campagne_id: ligne.campagne for ligne in getattr(face, 'lignes_rapport', ())}
            statut_face = face.statut_rapport

            faces_data.append({
                "face"     : face,
                "label"    : face.label,
                "statut"   : statut_face,           # 'occupe' | 'reserve' | 'libre'
                "occupee"  : statut_face == 'occupe',
                "reservee" : statut_face == 'reserve',
                "en_panne" : statut_face == 'panne',
                "campagnes": list(campagnes.values()),
                "client_reservation": getattr(face, 'client_reservation_rapport', ''),
                "debut_reservation": getattr(face, 'debut_reservation_rapport', None),
                "fin_reservation": getattr(face, 'fin_reservation_rapport', None),
            })

        nb_faces_total = len(faces_data)
        nb_en_panne    = sum(1 for f in faces_data if f['statut'] == 'panne')
        nb_occupees    = sum(1 for f in faces_data if f['statut'] == 'occupe')
        nb_reservees   = sum(1 for f in faces_data if f['statut'] == 'reserve')
        nb_libres      = sum(1 for f in faces_data if f['statut'] == 'libre')
        occupation     = _occupation_label(faces_data)

        # Appliquer le filtre occupation
        if not _match_filter(occupation, filter_occupation):
            continue

        total_faces     += nb_faces_total
        total_occupees  += nb_occupees
        total_reservees += nb_reservees
        total_panne += nb_en_panne
        total_libres += nb_libres

        panneaux.append({
            "support"     : support,
            "code"        : support.code,
            "code_ext"    : support.code_ext or "—",
            "nom"         : support.nom,
            "ancien_nom"  : support.ancien_nom,
            "type"        : "Écran" if support.type_support == Support.TYPE_ECRAN else "Sucette" if support.format == "1x2" else "Marché" if support.is_dans_marche else "Panneau",
            "ville"       : support.ville,
            "quartier"    : support.quartier,
            "adresse"     : support.adresse,
            "localisation": f"GPS : {support.latitude}, {support.longitude}" if not support.adresse and support.latitude is not None and support.longitude is not None else "",
            "statut"      : support.get_etat_display() if hasattr(support, "get_etat_display") else support.etat,
            "etat_raw"    : support.etat,
            "nb_faces"    : nb_faces_total,
            "nb_occupees" : nb_occupees,
            "nb_reservees": nb_reservees,
            "nb_en_panne" : nb_en_panne,
            "nb_libres"   : nb_libres,
            "occupation"  : occupation,
            "faces"       : faces_data,
        })

    # Un marché peut exister avant l'installation de son premier support.
    # On le garde visible dans le rapport pour suivre aussi les emplacements
    # encore disponibles à développer.
    emplacements = Emplacement.objects.select_related('support_installe').annotate(
        occupe_rapport=Exists(LigneCampagne.objects.filter(
            emplacement_id=OuterRef('pk'), campagne__date_fin__gte=now.date(),
        ).exclude(campagne__statut__in=('brouillon', 'annulee'))),
    )
    marches = Marche.objects.filter(actif=True).prefetch_related(
        Prefetch('emplacements', queryset=emplacements, to_attr='emplacements_rapport'),
    )
    if marche_pk:
        marches = marches.filter(pk=marche_pk)
    elif not include_markets:
        marches = marches.none()
    if ville:
        marches = marches.filter(ville__iexact=ville)
    if quartier:
        marches = marches.filter(quartier__iexact=quartier)
    if statut and statut != Support.ETAT_BON:
        marches = marches.none()
    for marche in marches:
        faces_marche = []
        for emplacement in marche.emplacements_rapport:
            support_installe = getattr(emplacement, "support_installe", None)
            statut_emplacement = "occupe" if emplacement.occupe_rapport else "libre"
            faces_marche.append({
                "face": None,
                "label": emplacement.code,
                "statut": statut_emplacement,
                "occupee": statut_emplacement == "occupe",
                "reservee": False,
                "en_panne": False,
                "campagnes": [],
                "support_code": support_installe.code if support_installe else None,
            })
        occupation_marche = _occupation_label(faces_marche)
        if not _match_filter(occupation_marche, filter_occupation):
            continue
        marche_occupees = sum(1 for face in faces_marche if face["occupee"])
        marche_libres = sum(1 for face in faces_marche if face["statut"] == "libre")
        supports_connectes = sum(
            emplacement.support_installe.actif
            for emplacement in marche.emplacements_rapport
            if getattr(emplacement, 'support_installe', None) is not None
        )
        total_faces += len(faces_marche)
        total_occupees += marche_occupees
        total_libres += marche_libres
        panneaux.append({
            "support": None,
            "code": f"MAR-{marche.pk:03d}",
            "nom": marche.nom,
            "type": "Marché",
            "ville": marche.ville,
            "quartier": marche.quartier,
            "adresse": marche.adresse,
            "localisation": f"GPS : {marche.latitude}, {marche.longitude}" if not marche.adresse and marche.latitude is not None and marche.longitude is not None else "",
            "statut": "Actif" if marche.actif else "Inactif",
            "etat_raw": "bon" if marche.actif else "panne",
            "nb_faces": len(faces_marche),
            "nb_occupees": marche_occupees,
            "nb_reservees": 0,
            "nb_en_panne": 0,
            "nb_libres": marche_libres,
            "occupation": occupation_marche,
            "faces": faces_marche,
            "supports_connectes": supports_connectes,
        })

    category_order = {"Panneau": 0, "Écran": 1, "Marché": 2, "Sucette": 3}
    panneaux.sort(key=lambda item: (
        category_order.get(item["type"], 99),
        item["ville"] or "",
        item["quartier"] or "",
        item["nom"] or "",
    ))
    categories_counts = {
        category: sum(item["nb_faces"] for item in panneaux if item["type"] == category)
        for category in category_order
    }
    for item in panneaux:
        item["categorie_count"] = categories_counts[item["type"]]
    categories_sections = [
        {
            "nom": category,
            "items": [item for item in panneaux if item["type"] == category],
            "total": sum(1 for item in panneaux if item["type"] == category),
            "unites": categories_counts[category],
        }
        for category in category_order
        if any(item["type"] == category for item in panneaux)
    ]

    # ── Données pour les <select> de filtres ────────────────────
    all_supports = Support.objects.filter(actif=True)

    villes    = sorted({v.strip() for v in all_supports.values_list("ville",    flat=True) if v and v.strip()})
    quartiers = sorted({q.strip() for q in all_supports.values_list("quartier", flat=True) if q and q.strip()})
    statuts   = sorted({s.strip() for s in all_supports.values_list("etat",     flat=True) if s and s.strip()})
    formats_rapport = [
        {"code": format_support.code, "label": str(format_support)}
        for format_support in formats_supports
    ]
    formats_connus = {format_support["code"] for format_support in formats_rapport}
    formats_rapport.extend(
        {"code": code, "label": code}
        for code in sorted(format_codes - formats_connus)
    )
    clients   = Client.objects.filter(
        type_entite=Client.TYPE_CLIENT,
    ).order_by("nom")
    marches_list = Marche.objects.filter(actif=True).order_by("nom")
    categories_rapport = ["Panneau", "Écran", "Marché", "Sucette"]
    categories_rapport.extend(
        categorie
        for categorie in sorted(formats_par_categorie, key=str.casefold)
        if categorie not in categories_rapport
    )
    afficher_etat = filters.get("afficher_etat", "1") == "1"
    afficher_type = len({panneau["type"] for panneau in panneaux}) > 1

    return {
        "panneaux"          : panneaux,
        "today"             : today,
        "filters"           : filters,
        "categories_selectionnees": categories,
        "afficher_etat": afficher_etat,
        "afficher_type": afficher_type,
        "nombre_colonnes_tableau": 4 + int(afficher_etat) + int(afficher_type),
        "afficher_client": afficher_client,
        "afficher_faces_libres": filters.get("afficher_faces_libres", "1") == "1",
        "afficher_faces_reservees": filters.get("afficher_faces_reservees", "1") == "1",
        "afficher_faces_occupees": filters.get("afficher_faces_occupees", "1") == "1",
        "categories_rapport": categories_rapport,
        "categories_par_format": {item.code: item.categorie for item in formats_supports},
        "categories_sections": categories_sections,
        "quartier_sections": _group_supports_by_quartier(panneaux),
        "villes"            : villes,
        "quartiers"         : quartiers,
        "statuts"           : statuts,
        "formats_rapport"   : formats_rapport,
        "clients"           : clients,
        "marches_list"      : marches_list,
        "occupation_choices": OCCUPATION_CHOICES,
        "total_panneaux"    : len(panneaux),
        "total_faces"       : total_faces,
        "total_occupees"    : total_occupees,
        "total_reservees"   : total_reservees,
        "total_panne" : total_panne,
        "total_libres"      : total_libres,
    }


def _filters_from_request(request) -> dict:
    return {
        "ville"     : request.GET.get("ville",     ""),
        "quartier"  : request.GET.get("quartier",  ""),
        "statut"    : request.GET.get("statut",    ""),
        "occupation": request.GET.get("occupation",""),
        "format"    : request.GET.get("format", "").strip(),
        "client_pk" : request.GET.get("client_pk", ""),
        "marche_pk" : request.GET.get("marche_pk", ""),
        "q"         : request.GET.get("q",         ""),
        "categories": request.GET.getlist("categorie"),
        "afficher_etat": request.GET.get("afficher_etat", "1"),
        "afficher_client": request.GET.get("afficher_client", "1"),
        "afficher_faces_libres": request.GET.get("afficher_faces_libres", "1"),
        "afficher_faces_reservees": request.GET.get("afficher_faces_reservees", "1"),
        "afficher_faces_occupees": request.GET.get("afficher_faces_occupees", "1"),
    }

# ══════════════════════════════════════════════════════════════════
# VUES
# ══════════════════════════════════════════════════════════════════

class SupportsReportView(ClientStaffRequiredMixin, View):
    """Aperçu HTML du rapport — inclut les filtres."""

    def get(self, request):
        filters = _filters_from_request(request)
        context = _build_context_panneaux(filters)
        supports_ville = Support.objects.filter(actif=True)
        marches_ville = Marche.objects.filter(actif=True)
        villes = set(context['villes'])
        villes.update(ville.strip() for ville in marches_ville.values_list('ville', flat=True) if ville and ville.strip())
        if filters['ville']:
            supports_ville = supports_ville.filter(ville__iexact=filters['ville'].strip())
            marches_ville = marches_ville.filter(ville__iexact=filters['ville'].strip())

        quartiers = set()
        categories = set()
        for quartier, type_support, code_format, emplacement_id in supports_ville.values_list(
            'quartier', 'type_support', 'format', 'emplacement_id',
        ):
            if quartier and quartier.strip():
                quartiers.add(quartier.strip())
            if type_support == Support.TYPE_ECRAN:
                categories.add('Écran')
            elif type_support == Support.TYPE_PANNEAU:
                categorie = context['categories_par_format'].get(code_format)
                if code_format == '1x2' and not emplacement_id:
                    categories.add('Sucette')
                elif categorie and categorie.casefold() != 'panneau':
                    categories.add(categorie)
        quartiers_marches = list(marches_ville.values_list('quartier', flat=True))
        if quartiers_marches:
            categories.add('Marché')
            quartiers.update(quartier.strip() for quartier in quartiers_marches if quartier and quartier.strip())
        context['categories_rapport'] = [
            categorie for categorie in context['categories_rapport'] if categorie in categories
        ]
        context['categories_rapport_masquees'] = [
            categorie for categorie in filters['categories']
            if len(context['categories_rapport']) <= 1 or categorie not in categories
        ]

        filtres_inferieurs = ('format', 'statut', 'occupation')
        if any(filters[nom] for nom in filtres_inferieurs):
            candidats = _build_context_panneaux({
                **filters, **dict.fromkeys(filtres_inferieurs, ''), 'afficher_client': '0',
            })['panneaux']
        else:
            candidats = context['panneaux']
        codes_disponibles = {item['support'].format for item in candidats if item['support']}
        etats_disponibles = {item['etat_raw'] for item in candidats}
        occupations_disponibles = {item['occupation'] for item in candidats}
        groupes = (
            ('ville', 'Ville', 'Toutes', [(ville, ville) for ville in sorted(villes)]),
            ('quartier', 'Quartier', 'Tous', [(quartier, quartier) for quartier in sorted(quartiers)]),
            ('format', 'Format', 'Tous', [
                (item['code'], item['label']) for item in context['formats_rapport']
                if item['code'] in codes_disponibles
            ]),
            ('statut', 'État', 'Tous', [
                (etat, label) for etat, label in Support._meta.get_field('etat').choices
                if etat in etats_disponibles
            ]),
            ('occupation', 'Occupation', 'Toutes', [
                (key, label) for key, label in context['occupation_choices'].items()
                if any(_match_filter(occupation, key) for occupation in occupations_disponibles)
            ]),
        )
        context['onglets_rapport'] = [
            {'nom': nom, 'titre': titre, 'actif': filters.get(nom, ''), 'options': [('', tous), *options]}
            for nom, titre, tous, options in groupes
            if len(options) > 1
        ]
        context['filtres_rapport_masques'] = {
            nom: filters[nom] for nom, _, _, options in groupes
            if filters.get(nom) and (len(options) <= 1 or filters[nom] not in dict(options))
        }
        return render(request, "reports/apercu_supports.html", context)


class ExportSupportsPdfView(ClientStaffRequiredMixin, View):
    """Télécharge le rapport filtré en PDF."""

    def get(self, request):
        filters = _filters_from_request(request)
        context = _build_context_panneaux(filters)

        html_string = render_to_string(
            "reports/supports_report_pdf_export.html",
            context,
            request=request,
        )
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf()

        # Nom de fichier reflétant les filtres actifs
        parts = ["etat_supports"]
        if filters.get("ville"):      parts.append(filters["ville"].replace(" ", "_"))
        if filters.get("quartier"):   parts.append(filters["quartier"].replace(" ", "_"))
        if filters.get("occupation"): parts.append(filters["occupation"])
        parts.append(f"{datetime.now():%Y%m%d}")
        # ajouter HMS
        parts.append(f"{datetime.now():%H%M%S}")
        filename = "_".join(parts) + ".pdf"

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response






import io
import re
from datetime import date, datetime

import pandas as pd
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter


def _sanitize_sheet_name(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:\[\]]', '_', name)
    return cleaned[:31]


def _thin_border(color="E2E8F0"):
    side = Side(style="thin", color=color)
    return Border(left=side, right=side, top=side, bottom=side)


class ExportSupportsExcelView(ClientStaffRequiredMixin, View):

    def get(self, request):
        filters = _filters_from_request(request)
        context = _build_context_panneaux(filters)
        now     = timezone.now()

        # ── Nombre max de faces ──
        max_faces = max(
            (len(panneau["faces"]) for panneau in context["panneaux"]),
            default=1
        )

        # ── Colonnes sheet principal ──
        base_cols = ["Code", "Nom", "Ville", "Quartier", "Adresse"]
        if context["afficher_etat"]:
            base_cols.append("Statut")
        face_cols = [f"Face {chr(65 + i)}" for i in range(max_faces)]
        all_cols  = base_cols + face_cols

        # ── Lignes + collecte données clients ──
        rows         = []
        clients_data = {}

        for panneau in context["panneaux"]:
            nom_affiche = panneau["nom"]
            if panneau.get("ancien_nom"):
                nom_affiche += f' ({panneau["ancien_nom"]})'
            row = {
                "Code"    : panneau["code"],
                "Nom"     : nom_affiche,
                "Ville"   : panneau["ville"],
                "Quartier": panneau["quartier"],
                "Adresse" : panneau["adresse"],
                "Statut"  : panneau["statut"],
            }

            for i, face_data in enumerate(panneau["faces"]):
                lettre = chr(65 + i)
                statut = face_data["statut"]

                if statut == "occupe":
                    campagnes = face_data["campagnes"]
                    contenu = "● Occupée"
                    if context["afficher_client"] and campagnes:
                        camp = campagnes[0]
                        client_nom = camp.client.nom if camp.client else "Inconnu"
                        contenu = f"● Occupée\n{client_nom} — {camp.nom}"
                        clients_data.setdefault(client_nom, []).append({
                            "Code"    : panneau["code"],
                            "Nom"     : nom_affiche,
                            "Ville"   : panneau["ville"],
                            "Quartier": panneau["quartier"],
                            "Adresse" : panneau["adresse"],
                            "Face"    : lettre,
                            "Statut"  : "Occupée",
                            "Campagne": camp.nom,
                            "Début"   : camp.date_debut.strftime("%d/%m/%Y"),
                            "Fin"     : camp.date_fin.strftime("%d/%m/%Y"),
                        })

                elif statut == "reserve":
                    contenu = "◈ Réservée"
                    if context["afficher_client"]:
                        client_nom = face_data.get("client_reservation")
                        if client_nom:
                            contenu = f"◈ Réservée\n{client_nom}"
                            clients_data.setdefault(client_nom, []).append({
                                "Code"    : panneau["code"],
                                "Nom"     : nom_affiche,
                                "Ville"   : panneau["ville"],
                                "Quartier": panneau["quartier"],
                                "Adresse" : panneau["adresse"],
                                "Face"    : lettre,
                                "Statut"  : "Réservée",
                                "Campagne": "— (Réservation)",
                                "Début"   : face_data["debut_reservation"].strftime("%d/%m/%Y"),
                                "Fin"     : face_data["fin_reservation"].strftime("%d/%m/%Y"),
                            })

                else:
                    contenu = "__"

                row[f"Face {lettre}"] = contenu

            rows.append(row)

        df = pd.DataFrame(rows, columns=all_cols)

        # ── Styles Design 2 — Moderne contrasté ──
        # En-têtes
        header_fill = PatternFill("solid", start_color="0F172A", end_color="0F172A")
        header_font = Font(bold=True, color="E2E8F0", name="Arial", size=10)

        # Titre
        titre_font  = Font(bold=True, size=13, name="Arial", color="0F172A")

        # Faces
        fill_occupe   = PatternFill("solid", start_color="DCFCE7", end_color="DCFCE7")
        font_occupe   = Font(bold=True, color="166534", name="Arial", size=10)

        fill_reserve  = PatternFill("solid", start_color="EDE9FE", end_color="EDE9FE")
        font_reserve  = Font(bold=True, color="5B21B6", name="Arial", size=10)

        fill_libre    = PatternFill("solid", start_color="F1F5F9", end_color="F1F5F9")
        font_libre    = Font(color="64748B", name="Arial", size=10)

        # En panne : toute la ligne
        fill_panne_row  = PatternFill("solid", start_color="FFF7ED", end_color="FFF7ED")
        fill_panne_face = PatternFill("solid", start_color="FEE2E2", end_color="FEE2E2")
        font_panne_row  = Font(color="9A3412", name="Arial", size=10)
        font_panne_face = Font(bold=True, color="991B1B", name="Arial", size=10)

        # Lignes normales
        fill_normal = PatternFill("solid", start_color="FFFFFF", end_color="FFFFFF")
        font_normal = Font(color="0F172A", name="Arial", size=10)

        border_normal = _thin_border("E2E8F0")
        border_panne  = _thin_border("FED7AA")

        # ── Export Excel ──
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

            # ════════════════════════════════
            # SHEET PRINCIPAL — État Supports
            # ════════════════════════════════
            df.to_excel(writer, index=False, sheet_name="État Supports", startrow=2)
            ws = writer.sheets["État Supports"]

            # Titre ligne 1
            titre_parts = [f"État des supports — généré le {date.today():%d/%m/%Y}"]
            if filters.get("ville"):      titre_parts.append(f"Ville : {filters['ville']}")
            if filters.get("quartier"):   titre_parts.append(f"Quartier : {filters['quartier']}")
            if filters.get("occupation"): titre_parts.append(f"Occupation : {filters['occupation']}")
            if filters.get("statut"):     titre_parts.append(f"État : {filters['statut']}")
            if filters.get("categories"):
                titre_parts.append(f"Catégories : {', '.join(filters['categories'])}")
            if filters.get("format"):
                format_label = next(
                    (
                        item["label"]
                        for item in context["formats_rapport"]
                        if item["code"] == filters["format"]
                    ),
                    filters["format"],
                )
                titre_parts.append(f"Format : {format_label}")
            ws["A1"] = "  |  ".join(titre_parts)
            ws["A1"].font = titre_font

            # En-têtes ligne 3
            for cell in ws[3]:
                cell.fill      = header_fill
                cell.font      = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border    = _thin_border("1E293B")

            ws.row_dimensions[3].height = 22

            # Index colonnes
            face_col_indices = [i + 1 for i, col in enumerate(all_cols) if col.startswith("Face ")]
            # ── Coloration par ligne ──
            for row_index, row in enumerate(
                ws.iter_rows(min_row=4, max_row=ws.max_row)
            ):
                en_panne = context["panneaux"][row_index]["etat_raw"] == "panne"

                for cell in row:
                    cell.font   = font_panne_row if en_panne else font_normal
                    cell.fill   = fill_panne_row if en_panne else fill_normal
                    cell.border = border_panne   if en_panne else border_normal
                    cell.alignment = Alignment(vertical="center", wrap_text=False)

                # Surcharge cellules faces
                for idx in face_col_indices:
                    cell = row[idx - 1]
                    cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")

                    if en_panne:
                        cell.fill = fill_panne_face
                        cell.font = font_panne_face
                        if cell.value and "Libre" in str(cell.value):
                            cell.value = "⚠ En panne"
                        elif cell.value:
                            cell.value = "⚠ En panne"
                    else:
                        val = str(cell.value) if cell.value else ""
                        if "Occupée" in val:
                            cell.fill = fill_occupe
                            cell.font = font_occupe
                        elif "Réservée" in val:
                            cell.fill = fill_reserve
                            cell.font = font_reserve
                        else:
                            cell.fill = fill_libre
                            cell.font = font_libre

                ws.row_dimensions[row[0].row].height = 30

            # Largeurs colonnes
            col_widths = {
                "Code": 10, "Nom": 50, "Ville": 15,
                "Quartier": 15, "Adresse": 35, "Statut": 14,
            }
            for i, col_name in enumerate(all_cols, start=1):
                ws.column_dimensions[get_column_letter(i)].width = col_widths.get(col_name, 28)

            ws.freeze_panes = f"{get_column_letter(len(base_cols) + 1)}4"

            # ════════════════════════════════
            # SHEETS PAR CLIENT
            # ════════════════════════════════
            for client_nom, lignes in sorted(clients_data.items()):
                sheet_name = _sanitize_sheet_name(client_nom)

                panneaux_client = {}
                for ligne in lignes:
                    code = ligne["Code"]
                    if code not in panneaux_client:
                        panneaux_client[code] = {
                            "Code"    : ligne["Code"],
                            "Nom"     : ligne["Nom"],
                            "Ville"   : ligne["Ville"],
                            "Quartier": ligne["Quartier"],
                            "Adresse" : ligne["Adresse"],
                        }
                    lettre = ligne["Face"]
                    if ligne["Statut"] == "Occupée":
                        panneaux_client[code][f"Face {lettre}"] = (
                            f"● Occupée\n{ligne['Campagne']}\n{ligne['Début']} → {ligne['Fin']}"
                        )
                    else:
                        panneaux_client[code][f"Face {lettre}"] = (
                            f"◈ Réservée\n{ligne['Début']} → {ligne['Fin']}"
                        )

                face_lettres = sorted(set(l["Face"] for l in lignes))
                cols_client  = ["Code", "Nom", "Ville", "Quartier", "Adresse"] + [f"Face {l}" for l in face_lettres]
                rows_client  = list(panneaux_client.values())
                df_client    = pd.DataFrame(rows_client, columns=cols_client)

                df_client.to_excel(writer, index=False, sheet_name=sheet_name, startrow=2)
                ws_c = writer.sheets[sheet_name]

                # Titre
                ws_c["A1"] = f"Supports de {client_nom} — {date.today():%d/%m/%Y}"
                ws_c["A1"].font = titre_font

                # En-têtes
                for cell in ws_c[3]:
                    cell.fill      = header_fill
                    cell.font      = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border    = _thin_border("1E293B")
                ws_c.row_dimensions[3].height = 22

                face_col_indices_client = [
                    i + 1 for i, col in enumerate(cols_client) if col.startswith("Face ")
                ]

                for row in ws_c.iter_rows(min_row=4, max_row=ws_c.max_row):
                    for cell in row:
                        cell.font      = font_normal
                        cell.fill      = fill_normal
                        cell.border    = border_normal
                        cell.alignment = Alignment(vertical="center")

                    for idx in face_col_indices_client:
                        cell = row[idx - 1]
                        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
                        val = str(cell.value) if cell.value else ""
                        if "Occupée" in val:
                            cell.fill = fill_occupe
                            cell.font = font_occupe
                        elif "Réservée" in val:
                            cell.fill = fill_reserve
                            cell.font = font_reserve

                    ws_c.row_dimensions[row[0].row].height = 42

                col_widths_client = {
                    "Code": 10, "Nom": 40, "Ville": 15,
                    "Quartier": 15, "Adresse": 35,
                }
                for i, col_name in enumerate(cols_client, start=1):
                    ws_c.column_dimensions[get_column_letter(i)].width = col_widths_client.get(col_name, 28)

                ws_c.freeze_panes = "F4"

            resume_rows = [
                {"Indicateur": "Supports", "Valeur": context["total_panneaux"]},
                {"Indicateur": "Faces totales", "Valeur": context["total_faces"]},
            ]
            for filtre, cle, valeur in (
                ("afficher_faces_libres", "Faces libres", context["total_libres"]),
                ("afficher_faces_reservees", "Faces réservées", context["total_reservees"]),
                ("afficher_faces_occupees", "Faces occupées", context["total_occupees"]),
            ):
                if context[filtre]:
                    resume_rows.append({"Indicateur": cle, "Valeur": valeur})

            resume_name = "Résumé"
            suffix = 2
            while resume_name in writer.sheets:
                resume_name = f"Résumé {suffix}"
                suffix += 1
            pd.DataFrame(resume_rows, columns=["Indicateur", "Valeur"]).to_excel(
                writer,
                index=False,
                sheet_name=resume_name,
                startrow=2,
            )
            ws_resume = writer.sheets[resume_name]
            ws_resume["A1"] = f"Résumé du rapport — généré le {date.today():%d/%m/%Y}"
            ws_resume["A1"].font = titre_font
            for cell in ws_resume[3]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = _thin_border("1E293B")
            ws_resume.column_dimensions["A"].width = 28
            ws_resume.column_dimensions["B"].width = 16
            ws_resume.freeze_panes = "A4"

        buffer.seek(0)

        parts = ["etat_supports"]
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













