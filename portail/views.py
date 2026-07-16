# portail/views.py
import json
import logging
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from django.views.generic import ListView, DetailView, DeleteView, TemplateView, FormView, View
from django.utils.safestring import mark_safe
from campaigns.models import (
    DemandeReservation,
    LigneCampagne,
    Reservation,
    ReservationLigne,
    STATUT_EN_ATTENTE,
    STATUT_CONFIRMEE,
)
from inventory.models import FacePanneau, Support

from .forms import ContactForm, Etape1Form, Etape2Form, Etape3Form

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
GEOJSON_CACHE_KEY     = 'portail_geojson'
GEOJSON_CACHE_TIMEOUT = 300   # 5 minutes
COMPTEURS_CACHE_KEY   = 'portail_compteurs'
COMPTEURS_CACHE_TIMEOUT = 120  # 2 minutes


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _color_for_support(support, faces_data):
    """Retourne la couleur Leaflet selon le statut consolidé des faces."""
    if support.type_support == 'ecran':
        return '#3b82f6'   # bleu — écran

    statuts = [f['statut'] for f in faces_data]
    if all(s == 'panne' for s in statuts):
        return '#6b7280'   # gris — tout en panne
    if all(s in ('occupe', 'panne') for s in statuts):
        return '#ef4444'   # rouge — tout occupé
    if any(s == 'libre' for s in statuts):
        return '#22c55e'   # vert — au moins une face libre
    return '#f97316'       # orange — toutes réservées


def _get_compteurs():
    """Retourne les 4 compteurs de la page d'accueil (avec cache)."""
    cached = cache.get(COMPTEURS_CACHE_KEY)
    if cached:
        return cached

    today = timezone.now().date()
    supports = Support.objects.filter(actif=True)
    nb_supports = supports.filter(type_support='panneau').count()
    nb_ecrans   = supports.filter(type_support='ecran').count()
    nb_villes   = supports.values('ville').distinct().count()

    # Faces libres = faces dont aucune LigneCampagne ni ReservationLigne active
    faces_occupees_ids = set(
        LigneCampagne.objects.filter(
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
            campagne__statut__in=['en_cours', 'a_venir'],
            face__isnull=False,
        ).values_list('face_id', flat=True)
    )
    faces_reservees_ids = set(
        ReservationLigne.objects.filter(
            reservation__date_fin__gte=timezone.now(),
            reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
        ).values_list('face_id', flat=True)
    )
    nb_libres = FacePanneau.objects.filter(
        support__actif=True,
        support__type_support='panneau',
        etat='bon',
    ).exclude(
        pk__in=faces_occupees_ids | faces_reservees_ids
    ).count()

    result = {
        'nb_supports': nb_supports,
        'nb_ecrans':   nb_ecrans,
        'nb_libres':   nb_libres,
        'nb_villes':   nb_villes,
    }
    cache.set(COMPTEURS_CACHE_KEY, result, COMPTEURS_CACHE_TIMEOUT)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Pages publiques
# ══════════════════════════════════════════════════════════════════════════════

# portail/views.py
"""
Vue "Vitrine" — Page d'accueil de la régie publicitaire.

Affiche, pour chaque ville où la régie est implantée, une carte récapitulative :
- image d'illustration de la ville
- nombre total de faces publicitaires (panneaux + écrans)
- nombre de faces actuellement libres
- répartition des faces par type de support (ex: "4m × 3m — Standard": 86, ...)

Hypothèse de structure de données (la "face" est l'unité commerciale vendable) :
- Un panneau statique (Support.type_support == 'panneau') se décompose en N
  FacePanneau (en général Face A / Face B). Chaque face est libre ou non
  selon FacePanneau.is_disponible().
- Un écran numérique (Support.type_support == 'ecran') n'a pas de sous-faces :
  le Support lui-même constitue une face unique, libre si Support.is_occupe()
  est False.
"""

from collections import defaultdict

from django.views import View
from django.shortcuts import render
from django.conf import settings




# ──────────────────────────────────────────────────────────────────────────
# Configuration des illustrations par ville.
# À terme, ceci peut devenir un champ `image` sur un modèle "Ville" dédié.
# En attendant, on mappe ici un chemin static + une valeur de repli.
# ──────────────────────────────────────────────────────────────────────────
VILLE_IMAGES = {
    'Koudougou': 'img/villes/image1 (1).jfif',
    'Ouagadougou': 'img/villes/image1 (6).jfif',
    'Bobo-Dioulasso':     'img/villes/image1 (5).jfif',
}
VILLE_IMAGE_DEFAUT = 'img/villes/default.jpg'


from collections import defaultdict
from django.views import View
from django.shortcuts import render

def _label_support(support: Support, formats_map: dict) -> tuple[str, dict]:
    """
    Retourne (categorie, label_data) pour regrouper les stats.
    label_data est un dictionnaire contenant les clés :
    - 'superficie' : ex "45m²" ou ""
    - 'dimensions' : ex "9x5" ou "Écran Numérique"
    - 'valeur_tri' : float pour trier du plus petit au plus grand
    """
    if support.type_support == Support.TYPE_PANNEAU:
        if support.format:
            fs = formats_map.get(support.format)
            if fs:
                categorie = fs.categorie or 'Autres'
                superficie_str = f"{fs.superficie:g}m²" if fs.superficie else ""
                valeur_tri = float(fs.superficie) if fs.superficie else 0.0
                
                label_data = {
                    'superficie': superficie_str,
                    'dimensions': f"({fs.code})",
                    'valeur_tri': valeur_tri
                }
                return categorie, label_data
                
        label_data = {
            'superficie': "",
            'dimensions': "Panneau (format non défini)",
            'valeur_tri': 0.0
        }
        return 'Autres', label_data
        
    label_data = {
        'superficie': "",
        'dimensions': "Écran Numérique",
        'valeur_tri': 0.0 # Les écrans se placeront au début du tri (0m²)
    }
    return 'Écran', label_data


def _get_compteurs() -> dict:
    supports = (
        Support.objects
        .filter(actif=True)
        .select_related('ecran_info')
        .prefetch_related('faces')
    )

    formats_map = {f.code: f for f in FormatSupport.objects.all()}

    villes = defaultdict(lambda: {
        'total_faces': 0,
        'faces_libres': 0,
        # On utilise une clé sérialisée (JSON/string) pour le regroupement dans le defaultdict :
        'categories': defaultdict(lambda: defaultdict(lambda: {'total': 0, 'libre': 0, 'code': ''})),
    })

    formats_utilises = set()

    # Pour pouvoir utiliser le dictionnaire label_data comme clé de dictionnaire,
    # on le convertit temporairement en tuple nommé ou on stocke une référence.
    for support in supports:
        ville = support.ville or 'Non renseignée'
        data = villes[ville]
        categorie, label_data = _label_support(support, formats_map)

        # Clé unique pour grouper par format physique précis
        group_key = (label_data['superficie'], label_data['dimensions'], label_data['valeur_tri'])

        if support.type_support == Support.TYPE_PANNEAU:
            code_format = support.format
        else:
            code_format = getattr(getattr(support, 'ecran_info', None), 'cellule', '')

        if code_format:
            formats_utilises.add(code_format)

        if support.type_support == Support.TYPE_PANNEAU:
            faces = list(support.faces.all())
            if not faces:
                data['total_faces'] += 1
                entry = data['categories'][categorie][group_key]
                entry['total'] += 1
                entry['code'] = code_format
                continue

            for face in faces:
                data['total_faces'] += 1
                entry = data['categories'][categorie][group_key]
                entry['total'] += 1
                entry['code'] = code_format

                face_libre = (
                    face.etat == Support.ETAT_BON
                    and face.is_disponibles()
                )
                if face_libre:
                    data['faces_libres'] += 1
                    entry['libre'] += 1
        else:
            data['total_faces'] += 1
            entry = data['categories'][categorie][group_key]
            entry['total'] += 1
            entry['code'] = code_format

            support_libre = (
                support.etat == Support.ETAT_BON
                and not support.is_occupe()
            )
            if support_libre:
                data['faces_libres'] += 1
                entry['libre'] += 1

    villes_stats = []
    total_faces_reseau = 0
    total_faces_libres_reseau = 0

    categories_globales = defaultdict(lambda: {'total': 0, 'libre': 0})
    for data in villes.values():
        for categorie, group_keys in data['categories'].items():
            for group_key, counts in group_keys.items():
                categories_globales[categorie]['total'] += counts['total']
                categories_globales[categorie]['libre'] += counts['libre']

    categories_stats = [
        {
            'categorie': categorie,
            'total': stats['total'],
            'libre': stats['libre'],
            'occupe': stats['total'] - stats['libre'],
        }
        for categorie, stats in sorted(
            categories_globales.items(), key=lambda kv: -kv[1]['total']
        )
    ]

    for nom_ville, data in villes.items():
        supports_list = []
        categories_triees = sorted(
            data['categories'].items(),
            key=lambda kv: -sum(v['total'] for v in kv[1].values())
        )
        for categorie, group_keys in categories_triees:
            liste = []
            for group_key, counts in group_keys.items():
                superficie_val, dimensions_val, valeur_tri = group_key
                total = counts['total']
                libre = counts['libre']
                liste.append({
                    'superficie': superficie_val,
                    'dimensions': dimensions_val,
                    'valeur_tri': valeur_tri,       # Servira au tri
                    'format': counts['code'],
                    'count': total,
                    'libre': libre,
                    'occupe': total - libre,
                })
            
            # ── TRI DES FORMATS DE LA PLUS PETITE À LA PLUS GRANDE SUPERFICIE ──
            liste.sort(key=lambda item: item['valeur_tri'])

            supports_list.append({
                'categorie': categorie,
                'total': sum(c['total'] for c in group_keys.values()),
                'libre': sum(c['libre'] for c in group_keys.values()),
                'occupe': sum(c['total'] - c['libre'] for c in group_keys.values()),
                'liste': liste,
            })

        villes_stats.append({
            'nom': nom_ville,
            'image_url': VILLE_IMAGES.get(nom_ville, VILLE_IMAGE_DEFAUT),
            'total_faces': data['total_faces'],
            'faces_libres': data['faces_libres'],
            'faces_occupees': data['total_faces'] - data['faces_libres'],
            'supports': supports_list,
        })
        total_faces_reseau += data['total_faces']
        total_faces_libres_reseau += data['faces_libres']

    villes_stats.sort(key=lambda v: -v['total_faces'])

    return {
        'villes_stats': villes_stats,
        'categories_stats': categories_stats,
        'total_faces_reseau': total_faces_reseau,
        'total_faces_libres_reseau': total_faces_libres_reseau,
        'total_faces_occupees_reseau': total_faces_reseau - total_faces_libres_reseau,
        'nb_villes': len(villes_stats),
        'nb_formats': len(formats_utilises),
    }

class AccueilView(View):
    """Page d'accueil — vitrine publique de la régie publicitaire."""
    template_name = 'portail/vitrine.html'

    def get(self, request):
        compteurs = _get_compteurs()
        return render(request, self.template_name, {
            **compteurs,
            'oua_center': [12.3714, -1.5197],
        })
class CatalogueView(View):
    template_name = 'portail/catalogue.html'

    def get(self, request):
        today = timezone.now().date()

        # ── Faces libres par format (1 requête) ───────────────────────────
        faces_occupees_ids = set(
            LigneCampagne.objects.filter(
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
                campagne__statut__in=['en_cours', 'a_venir'],
                face__isnull=False,
            ).values_list('face_id', flat=True)
        )
        faces_reservees_ids = set(
            ReservationLigne.objects.filter(
                reservation__date_fin__gte=timezone.now(),
                reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
            ).values_list('face_id', flat=True)
        )
        indisponibles = faces_occupees_ids | faces_reservees_ids

        # ── Formats distincts avec stats ──────────────────────────────────
        formats_raw = (
            Support.objects
            .filter(type_support='panneau', actif=True)
            .exclude(format='')
            .values('format')
            .annotate(nb_supports=Count('id', distinct=True))
            .order_by('format')
        )

        formats_data = []
        for row in formats_raw:
            fmt = row['format']
            supports_fmt = Support.objects.filter(
                type_support='panneau', actif=True, format=fmt
            ).prefetch_related('faces')

            nb_faces_libres = 0
            photos = []
            for s in supports_fmt:
                for f in s.faces.all():
                    if f.pk not in indisponibles and f.etat == 'bon':
                        nb_faces_libres += 1
                if s.photo_principale and len(photos) < 4:
                    photos.append(s.photo_principale.url)

            formats_data.append({
                'format':         fmt,
                'nb_supports':    row['nb_supports'],
                'nb_faces_libres':nb_faces_libres,
                'photos':         photos,
            })

        # ── Écrans ────────────────────────────────────────────────────────
        ecrans = (
            Support.objects
            .filter(type_support='ecran', actif=True)
            .select_related('ecran_info')
        )
        ecrans_photos = [
            s.photo_principale.url
            for s in ecrans if s.photo_principale
        ][:4]

        return render(request, self.template_name, {
            'formats_data':  formats_data,
            'ecrans':        ecrans,
            'ecrans_photos': ecrans_photos,
            'nb_ecrans':     ecrans.count(),
        })


class SupportsListeView(View):
    template_name = 'portail/supports_liste.html'
    per_page = 20

    def get(self, request):
        fmt         = request.GET.get('format', '').strip()
        type_support= request.GET.get('type', '').strip()
        type_panneau= request.GET.get('type_panneau', '').strip()
        ville       = request.GET.get('ville', '').strip()
        quartier    = request.GET.get('quartier', '').strip()
        dispo       = request.GET.get('dispo', '').strip()
        q           = request.GET.get('q', '').strip()

        qs = (
            Support.objects
            .filter(actif=True)
            .prefetch_related(
                Prefetch(
                    'faces',
                    queryset=FacePanneau.objects.prefetch_related(
                        'lignes_campagne__campagne__client',
                        'lignes_reservation__reservation',
                    )
                )
            )
            .order_by('ville', 'quartier', 'code')
        )

        if type_support:
            qs = qs.filter(type_support=type_support)
        if type_panneau:
            codes = list(FormatSupport.objects.filter(categorie__iexact=type_panneau).values_list('code', flat=True))
            if codes:
                qs = qs.filter(format__in=codes)
            else:
                qs = qs.none()
        if fmt:
            qs = qs.filter(format=fmt)
        if ville:
            qs = qs.filter(ville__iexact=ville)
        if quartier:
            qs = qs.filter(quartier__iexact=quartier)
        if q:
            qs = qs.filter(
                Q(code__icontains=q) |
                Q(adresse__icontains=q) |
                Q(quartier__icontains=q)
            )

        # Enrichir chaque support avec le statut de ses faces
        today = timezone.now()
        supports_enrichis = []
        for support in qs:
            faces_data = []
            for face in support.faces.all():
                statut = face.get_statut(date_debut=today, date_fin=today)
                faces_data.append({'face': face, 'statut': statut})
            support._faces_data = faces_data

            # Filtre disponibilité
            if dispo == 'libre' and not any(f['statut'] == 'libre' for f in faces_data):
                continue
            if dispo == 'occupe' and not any(f['statut'] == 'occupe' for f in faces_data):
                continue

            supports_enrichis.append(support)

        # Pagination
        paginator  = Paginator(supports_enrichis, self.per_page)
        page_num   = request.GET.get('page', 1)
        page_obj   = paginator.get_page(page_num)

        # Données pour les <select> de filtres
        filter_kwargs = {'actif': True}
        if type_support:
            filter_kwargs['type_support'] = type_support

        villes = sorted({v for v in Support.objects.filter(**filter_kwargs).values_list('ville', flat=True) if v})
        quartiers = sorted({v for v in Support.objects.filter(
            **({**filter_kwargs, **({'ville': ville} if ville else {})})
        ).values_list('quartier', flat=True) if v})
        formats = sorted({v for v in Support.objects.filter(
            **filter_kwargs).exclude(format='').values_list('format', flat=True) if v})

        return render(request, self.template_name, {
            'page_obj':  page_obj,
            'villes':    villes,
            'quartiers': quartiers,
            'formats':   formats,
            'fmt':       fmt,
            'ville':     ville,
            'quartier':  quartier,
            'dispo':     dispo,
            'q':         q,
        })


class SupportDetailView(DetailView):
    model = Support
    template_name = 'portail/support_detail.html'
    context_object_name = 'support'
    pk_url_kwarg = 'uuid'

    def get_object(self, queryset=None):
        """
        On centralise la récupération de l'objet ici pour éviter les conflits 
        et intercepter la 404 avant le chargement du contexte.
        """
        uuid_val = self.kwargs.get(self.pk_url_kwarg)
        
        # Si l'utilisateur est un client, on restreint la recherche à ses campagnes
        if self.request.user.is_authenticated and getattr(self.request.user, 'is_client', False):
            client = getattr(self.request.user, 'client_profile', None)
            if client is None:
                raise PermissionDenied
            
            # NOTE : Si vous voulez qu'un client puisse voir TOUS les panneaux pour réserver,
            # supprimez le filtre .filter(...) ci-dessous et mettez juste Support.objects.all()
            return get_object_or_404(
                Support.objects.filter(lignes_campagne__campagne__client=client).distinct(),
                uuid=uuid_val
            )
        
        # Sinon (Staff / Admin), on récupère le support normalement par son UUID
        return get_object_or_404(Support, uuid=uuid_val)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # On récupère l'objet déjà validé par get_object()
        support = self.object

        # Client courant pour get_statut (None si staff)
        client_courant = getattr(self.request.user, 'client_profile', None)

        today = timezone.now().date()
        now   = timezone.now()

        # ── Campagnes actives et historique ───────────────────────────────
        lignes_actives = LigneCampagne.objects.filter(
            support=support,
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
        ).select_related('campagne__client', 'face')

        historique = LigneCampagne.objects.filter(
            support=support,
            campagne__date_fin__lt=today,
        ).select_related('campagne__client').order_by('-campagne__date_fin')[:10]
        
        reservations = ReservationLigne.objects.filter(
            support=support,
            reservation__date_fin__gte=timezone.now(),
            reservation__statut__in=['en_attente', 'confirmee'],
        ).select_related('reservation__client').order_by('reservation__date_debut')

        # ── Informations générales ─────────────────────────────────────────
        info_rows = [
            ('Code',         support.code),
            ('Type',         support.get_type_support_display()),
            ('Ville',        support.ville or '—'),
            ('Quartier',     support.quartier or '—'),
            ('Installation', support.date_installation.strftime('%d/%m/%Y') if support.date_installation else '—'),
        ]

        # ── Maintenances ──────────────────────────────────────────────────
        maintenances = support.maintenances.order_by('-date_intervention')[:4]

        ecran       = None
        ecran_stats = []
        spots_data  = []

        # ── Logique spécifique PANNEAU ────────────────────────────────────
        if support.type_support == 'panneau':
            if support.format:
                info_rows.append(('Format',     support.get_format_display()))
                info_rows.append(('Dimensions', support.dimensions))
                info_rows.append(('Surface',    support.surface_m2))
                info_rows.append(('Type',       support.type_panneau))

            for face in support.faces.all():
                statut = face.get_statut(
                    date_debut=now,
                    client=client_courant
                )
                status_icon = (
                    'bi-check2'    if statut == 'libre'   else
                    'bi-broadcast' if statut == 'reserve' else
                    'bi-x-circle'
                )
                status_class = (
                    'bs-disponible' if statut == 'libre'   else
                    'bs-reserve'    if statut == 'reserve' else
                    'bs-occupe'
                )
                status_label = (
                    'Disponible' if statut == 'libre'   else
                    'Réservé'    if statut == 'reserve' else
                    'Occupé'
                )

                badge = mark_safe(
                    f'<span class="badge-status {status_class}">'
                    f'<i class="bi {status_icon} me-1"></i>{status_label}</span>'
                )
                notes     = f' — {face.notes}' if face.notes else ''
                face_info = mark_safe(
                    f'{face.get_eclairage_display()}{notes} {badge}'
                )
                info_rows.append((f'Face {face.label}', face_info))

        # ── Logique spécifique ÉCRAN ──────────────────────────────────────
        elif support.type_support == 'ecran':
            ecran = getattr(support, 'ecran_info', None)

            if ecran:
                h_allumage   = ecran.heure_allumage.strftime('%H:%M') if ecran.heure_allumage else '00:00'
                h_extinction = ecran.heure_extinction.strftime('%H:%M') if ecran.heure_extinction else '00:00'
                plage_str    = f'{h_allumage} – {h_extinction}'
                taux         = ecran.taux_occupation_pourcentage()

                info_rows += [
                    ('Résolution',      ecran.get_resolution_display()),
                    ('Cellule',         f'{ecran.cellule}'),
                    ('Type écran',      ecran.get_type_ecran_display()),
                    ('Plage diffusion', plage_str),
                    ('Occupation',      f'{taux}%'),
                ]

                ecran_stats = [
                    ('Résolution', 'bi-aspect-ratio', ecran.get_resolution_display(), 'var(--text)'),
                    ('Cellule',    'bi-tv',           f'{ecran.cellule}',              'var(--text)'),
                    ('Occupation', 'bi-pie-chart',    f'{taux}%',                      'var(--color-primary)'),
                    ('Diffusion',  'bi-clock',        plage_str,                       'var(--color-primary)'),
                ]

                for ligne in lignes_actives:
                    spots_data.append({
                        'id':       ligne.pk,
                        'name':     f"{ligne.campagne.client.nom} — {ligne.campagne.nom}",
                        'dur':      ligne.campagne.duree_passage or 20,
                        'interval': ligne.campagne.frequence or 120,
                    })

        # ── Contexte final ────────────────────────────────────────────────
        context.update({
            'support':        support,
            'lignes_actives': lignes_actives,
            'historique':     historique,
            'maintenances':   maintenances,
            'ecran':          ecran,
            'ecran_stats':    ecran_stats,
            'info_rows':      info_rows,
            'spots_data':     spots_data,
            'reservations':   reservations,
        })
        return context





class ServicesView(TemplateView):
    template_name = 'portail/services.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ecrans = (
            Support.objects
            .filter(type_support='ecran', actif=True)
            .select_related('ecran_info')
            .order_by('ville', 'code')
        )

        # Compteurs par ville
        stats_villes = (
            Support.objects
            .filter(actif=True)
            .values('ville', 'type_support')
            .annotate(nb=Count('id'))
        )
        villes_data = {}
        for row in stats_villes:
            v = row['ville']
            if v not in villes_data:
                villes_data[v] = {'panneaux': 0, 'ecrans': 0}
            if row['type_support'] == 'panneau':
                villes_data[v]['panneaux'] = row['nb']
            else:
                villes_data[v]['ecrans'] = row['nb']

        # Tableau spots/jour (durée × fréquence)
        durees    = [5, 10, 15, 20]
        frequences= [60, 120, 300, 600]  # secondes
        heures_jour = 17  # 06h-23h
        tableau_spots = []
        for d in durees:
            row = {'duree': d, 'cols': []}
            for f in frequences:
                spots = round((3600 / f) * heures_jour)
                row['cols'].append(spots)
            tableau_spots.append(row)

        ctx.update({
            'ecrans':        ecrans,
            'villes_data':   villes_data,
            'tableau_spots': tableau_spots,
            'freq_labels':   ['1 min', '2 min', '5 min', '10 min'],
        })
        return ctx


class ContactView(View):
    template_name = 'portail/contact.html'

    def get(self, request):
        return render(request, self.template_name, {'form': ContactForm()})

    def post(self, request):
        form = ContactForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                # Email au staff
                send_mail(
                    subject=f"[INTEGRAL] Contact : {d['objet']}",
                    message=(
                        f"De : {d['nom']} <{d['email']}>\n"
                        f"Tél : {d.get('telephone', '—')}\n\n"
                        f"Objet : {d['objet']}\n\n"
                        f"Message :\n{d['message']}"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[settings.CONTACT_EMAIL],
                    fail_silently=False,
                )
                # Email de confirmation au visiteur
                send_mail(
                    subject="Votre message a bien été reçu — Régie INTEGRAL",
                    message=(
                        f"Bonjour {d['nom']},\n\n"
                        "Nous avons bien reçu votre message et reviendrons vers vous "
                        "dans les meilleurs délais.\n\n"
                        "Cordialement,\nL'équipe Régie INTEGRAL"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[d['email']],
                    fail_silently=True,
                )
            except Exception as exc:
                logger.error("Erreur envoi email contact : %s", exc)

            return redirect('portail:contact_confirmation')

        return render(request, self.template_name, {'form': form})


class ContactConfirmationView(TemplateView):
    template_name = 'portail/contact_confirmation.html'


# ══════════════════════════════════════════════════════════════════════════════
# Wizard réservation
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Wizard réservation
# ══════════════════════════════════════════════════════════════════════════════
import json
import logging
from datetime import date as date_type

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View

from inventory.models import FacePanneau, Support, FormatSupport
from campaigns.models import DemandeReservation
from .forms import Etape1Form, Etape2Form, Etape3Form

logger = logging.getLogger(__name__)


import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
# Assurez-vous d'importer vos modèles et formulaires correctement
# from .models import FacePanneau
# from .forms import Etape1Form

class ReserverEtape1View(View):
    
    """Étape 1 — Sélection des emplacements sur la carte + période souhaitée."""
    template_name = 'portail/reserver_etape1.html'

    def _panier(self, request):
        """Retourne les faces déjà dans le panier session."""
        ids = request.session.get('demande_faces_uuids', [])
        if not ids:
            return []
        return list(
            FacePanneau.objects.filter(pk__in=ids)
            .select_related('support')
        )

    def _filtres(self, request):
        """Récupère les filtres de présélection depuis les query params."""
        return {
            'ville': request.GET.get('ville', '').strip(),
            'categorie': request.GET.get('categorie', '').strip(),
            'format': request.GET.get('format', '').strip(),
        }

    def get(self, request):
        # Pré-sélection depuis query param ?face=<id>
        face_id = request.GET.get('face')
        if face_id:
            faces_ids = request.session.get('demande_faces_uuids', [])
            if face_id not in faces_ids:
                faces_ids.append(face_id)
            request.session['demande_faces_uuids'] = faces_ids

        # Filtres venant de la vitrine (ville / catégorie / format)
        filtres = self._filtres(request)

        # Période déjà saisie précédemment (retour en arrière depuis étape 2/3)
        periode = request.session.get('demande_periode', {})

        form = Etape1Form(initial={
            'faces_selectionnees': json.dumps(
                request.session.get('demande_faces_uuids', [])
            ),
            'supports_selectionnees': json.dumps(
                request.session.get('demande_supports_uuids', [])
            ),
            'date_debut': periode.get('date_debut'),
            'date_fin':   periode.get('date_fin'),
        })
        return render(request, self.template_name, {
            'form':   form,
            'panier': self._panier(request),
            'filtres': filtres,
        })

    def post(self, request):
        form = Etape1Form(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            request.session['demande_faces_uuids']    = d['faces_uuids']
            request.session['demande_supports_uuids'] = d['supports_uuids']
            request.session['demande_periode'] = {
                'date_debut': str(d['date_debut']),
                'date_fin':   str(d['date_fin']),
            }
            return redirect('portail:reserver_etape2')

        return render(request, self.template_name, {
            'form':   form,
            'panier': self._panier(request),
            'filtres': self._filtres(request),
        })


class ReserverEtape2View(View):
    """Étape 2 — Détails complémentaires du projet (campagne / message, facultatif)."""
    template_name = 'portail/reserver_etape2.html'

    def _get_selections(self, request):
        faces_ids    = request.session.get('demande_faces_uuids', [])
        supports_ids = request.session.get('demande_supports_uuids', [])
        faces    = list(FacePanneau.objects.filter(pk__in=faces_ids).select_related('support'))
        supports = list(Support.objects.filter(pk__in=supports_ids))
        return faces, supports

    def get(self, request):
        faces, supports = self._get_selections(request)
        periode = request.session.get('demande_periode', {})

        if (not faces and not supports) or not periode.get('date_debut'):
            messages.warning(request, "Veuillez d'abord sélectionner des emplacements et une période.")
            return redirect('portail:reserver_etape1')

        initial = request.session.get('demande_etape2', {})
        form = Etape2Form(initial=initial)
        return render(request, self.template_name, {
            'form':     form,
            'faces':    faces,
            'supports': supports,
            'periode':  periode,
        })

    def post(self, request):
        faces, supports = self._get_selections(request)
        form = Etape2Form(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            request.session['demande_etape2'] = {
                'nom_campagne': d.get('nom_campagne', ''),
                'message':      d.get('message', ''),
            }
            return redirect('portail:reserver_etape3')

        return render(request, self.template_name, {
            'form':     form,
            'faces':    faces,
            'supports': supports,
            'periode':  request.session.get('demande_periode', {}),
        })


class ReserverEtape3View(View):
    """Étape 3 — Coordonnées du visiteur et soumission finale."""
    template_name = 'portail/reserver_etape3.html'

    def _check_session(self, request):
        faces_ids    = request.session.get('demande_faces_uuids', [])
        supports_ids = request.session.get('demande_supports_uuids', [])
        periode      = request.session.get('demande_periode', {})
        if (not faces_ids and not supports_ids) or not periode.get('date_debut'):
            return False
        return True

    def _get_recap(self, request):
        faces_ids    = request.session.get('demande_faces_uuids', [])
        supports_ids = request.session.get('demande_supports_uuids', [])
        periode      = request.session.get('demande_periode', {})
        etape2_extra = request.session.get('demande_etape2', {})

        faces    = list(FacePanneau.objects.filter(pk__in=faces_ids).select_related('support'))
        supports = list(Support.objects.filter(pk__in=supports_ids))

        # Dict reconstruit pour rester compatible avec le template existant
        # (qui attend etape2.date_debut / etape2.date_fin / etape2.nom_campagne / etape2.message)
        etape2 = {
            'date_debut':   periode.get('date_debut'),
            'date_fin':     periode.get('date_fin'),
            'nom_campagne': etape2_extra.get('nom_campagne', ''),
            'message':      etape2_extra.get('message', ''),
        }
        return faces, supports, etape2

    def get(self, request):
        if not self._check_session(request):
            messages.warning(request, "Veuillez compléter les étapes précédentes.")
            return redirect('portail:reserver_etape1')

        faces, supports, etape2 = self._get_recap(request)
        form = Etape3Form()
        return render(request, self.template_name, {
            'form':     form,
            'faces':    faces,
            'supports': supports,
            'etape2':   etape2,
        })

    def post(self, request):
        if not self._check_session(request):
            return redirect('portail:reserver_etape1')

        faces, supports, etape2 = self._get_recap(request)
        form = Etape3Form(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form, 'faces': faces,
                'supports': supports, 'etape2': etape2,
            })

        d = form.cleaned_data

        date_debut = date_type.fromisoformat(etape2['date_debut'])
        date_fin   = date_type.fromisoformat(etape2['date_fin'])

        # ── NOUVEAU : type_client / reference_client_saisie ─────────────────
        # DemandeReservation.save() se charge du rapprochement automatique
        # avec un Client existant si la référence saisie matche (voir le
        # modèle). Ici on transmet juste ce que le visiteur a rempli.
        demande = DemandeReservation.objects.create(
            nom_contact              = d['nom_contact'],
            societe                  = d.get('societe', ''),
            email                    = d['email'],
            telephone                = d['telephone'],
            accepte_contact          = d.get('accepte_contact', False),
            type_client              = d.get('type_client', DemandeReservation.TYPE_CLIENT_NOUVEAU),
            reference_client_saisie  = d.get('reference_client_saisie', ''),
            date_debut_souhaitee     = date_debut,
            date_fin_souhaitee       = date_fin,
            nom_campagne             = etape2.get('nom_campagne', ''),
            message                  = etape2.get('message', ''),
            statut                   = DemandeReservation.STATUT_NOUVELLE,
        )
        if faces:
            demande.faces_souhaitees.set(faces)
        if supports:
            demande.supports_souhaites.set(supports)

        self._send_emails(demande, faces, supports, etape2)

        for key in ('demande_faces_uuids', 'demande_supports_uuids', 'demande_periode', 'demande_etape2'):
            request.session.pop(key, None)

        return redirect('portail:confirmation', uuid=demande.uuid)

    def _send_emails(self, demande, faces, supports, etape2):
        recap_emplacements = '\n'.join(
            [f"  - {f.support.code} · Face {f.label} · {f.support.quartier}" for f in faces] +
            [f"  - Écran {s.code} · {s.quartier}" for s in supports]
        ) or '  (aucun)'

        # ── NOUVEAU : mention du type de client dans l'email staff ──────────
        if demande.type_client == DemandeReservation.TYPE_CLIENT_EXISTANT:
            if demande.client_resolu_automatiquement:
                ligne_client = f"Client existant : rapproché automatiquement avec {demande.client.nom} ({demande.client.reference})"
            else:
                ligne_client = (
                    f"Client existant déclaré, référence saisie « {demande.reference_client_saisie or '—'} » "
                    f"— NON TROUVÉE, vérification manuelle nécessaire."
                )
        else:
            ligne_client = "Nouveau client"

        corps_staff = (
            f"Nouvelle demande reçue : {demande.reference}\n\n"
            f"Contact   : {demande.nom_contact} ({demande.societe or '—'})\n"
            f"Email     : {demande.email}\n"
            f"Téléphone : {demande.telephone}\n"
            f"{ligne_client}\n\n"
            f"Période   : {demande.date_debut_souhaitee:%d/%m/%Y} → {demande.date_fin_souhaitee:%d/%m/%Y}\n"
            f"Campagne  : {demande.nom_campagne or '—'}\n\n"
            f"Emplacements souhaités :\n{recap_emplacements}\n\n"
            f"Message :\n{demande.message or '—'}\n\n"
            f"Traiter : {getattr(settings, 'SITE_URL', '')}/staff/demandes/{demande.uuid}/"
        )
        try:
            send_mail(
                subject=f"[GeoAd] Nouvelle demande {demande.reference}",
                message=corps_staff,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.CONTACT_EMAIL],
                fail_silently=False,
            )
        except Exception as exc:
            logger.error("Email staff échoué pour demande %s : %s", demande.reference, exc)

        corps_visiteur = (
            f"Bonjour {demande.nom_contact},\n\n"
            f"Votre demande de réservation a bien été enregistrée.\n"
            f"Référence : {demande.reference}\n\n"
            f"Emplacements demandés :\n{recap_emplacements}\n\n"
            f"Période souhaitée : {demande.date_debut_souhaitee:%d/%m/%Y} → {demande.date_fin_souhaitee:%d/%m/%Y}\n\n"
            f"Notre équipe vous contactera à l'adresse {demande.email} "
            f"sous 24 heures ouvrables.\n\n"
            f"Cordialement,\nL'équipe Régie INTEGRAL"
        )
        try:
            send_mail(
                subject=f"Votre demande de réservation {demande.reference} — Régie INTEGRAL",
                message=corps_visiteur,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[demande.email],
                fail_silently=True,
            )
        except Exception as exc:
            logger.error("Email visiteur échoué : %s", exc)


class ConfirmationView(View):
    template_name = 'portail/confirmation.html'

    def get(self, request, uuid):
        demande = get_object_or_404(DemandeReservation, uuid=uuid)
        return render(request, self.template_name, {'demande': demande})
# ══════════════════════════════════════════════════════════════════════════════
# API JSON
# ══════════════════════════════════════════════════════════════════════════════

class ApiGeoJsonView(View):
    """
    Retourne un GeoJSON de tous les supports actifs pour Leaflet.
    Mise en cache 5 minutes.
    """

    def get(self, request):
        cached = cache.get(GEOJSON_CACHE_KEY)
        if cached:
            return JsonResponse(cached, safe=False)

        today = timezone.now()

        supports = (
            Support.objects
            .filter(actif=True)
            .select_related('ecran_info')
            .prefetch_related(
                Prefetch(
                    'faces',
                    queryset=FacePanneau.objects.prefetch_related(
                        'lignes_campagne__campagne',
                        'lignes_reservation__reservation',
                    )
                )
            )
        )

        features = []
        for support in supports:
            if support.type_support == 'panneau':
                faces_data = []
                for face in support.faces.all():
                    statut = face.get_statut(date_debut=today, date_fin=today)
                    faces_data.append({
                        'uuid':   str(face.uuid) if hasattr(face, 'uuid') else str(face.pk),
                        'label':  face.label,
                        'statut': statut,
                    })
                color = _color_for_support(support, faces_data)
            else:
                faces_data = []
                color = '#3b82f6'

            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [float(support.longitude), float(support.latitude)],
                },
                'properties': {
                    'uuid':         str(support.uuid),
                    'code':         support.code,
                    'nom':          support.nom,
                    'type_support': support.type_support,
                    'format':       support.format,
                    'ville':        support.ville,
                    'quartier':     support.quartier,
                    'adresse':      support.adresse,
                    'color':        color,
                    'faces':        faces_data,
                    'detail_url':   f"/gestion/portail/support/{support.uuid}/",
                },
            })

        geojson = {'type': 'FeatureCollection', 'features': features}
        cache.set(GEOJSON_CACHE_KEY, geojson, GEOJSON_CACHE_TIMEOUT)
        return JsonResponse(geojson, safe=False)


class ApiCheckDispoView(View):
    """
    Vérifie la disponibilité d'une liste de faces sur une période.
    Body JSON : { "faces": ["uuid1","uuid2"], "date_debut":"YYYY-MM-DD", "date_fin":"YYYY-MM-DD" }
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'JSON invalide'}, status=400)

        faces_uuids = body.get('faces', [])
        date_debut_str = body.get('date_debut', '')
        date_fin_str   = body.get('date_fin', '')

        if not faces_uuids or not date_debut_str or not date_fin_str:
            return JsonResponse({'error': 'Paramètres manquants'}, status=400)

        try:
            from datetime import date as date_type
            date_debut = date_type.fromisoformat(date_debut_str)
            date_fin   = date_type.fromisoformat(date_fin_str)
        except ValueError:
            return JsonResponse({'error': 'Format de date invalide (YYYY-MM-DD)'}, status=400)

        if date_fin <= date_debut:
            return JsonResponse({'error': 'date_fin doit être > date_debut'}, status=400)

        faces = (
            FacePanneau.objects.filter(uuid__in=faces_uuids)
            .select_related('support')
            .prefetch_related(
                Prefetch(
                    'lignes_campagne',
                    queryset=LigneCampagne.objects.filter(
                        campagne__date_debut__lte=date_fin,
                        campagne__date_fin__gte=date_debut,
                        campagne__statut__in=['en_cours', 'a_venir'],
                    ).select_related('campagne'),
                ),
                Prefetch(
                    'lignes_reservation',
                    queryset=ReservationLigne.objects.filter(
                        reservation__date_debut__lte=timezone.make_aware(
                            timezone.datetime.combine(date_fin, timezone.datetime.max.time())
                        ),
                        reservation__date_fin__gte=timezone.make_aware(
                            timezone.datetime.combine(date_debut, timezone.datetime.min.time())
                        ),
                        reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                    ).select_related('reservation'),
                ),
            )
        )

        resultats = []
        for face in faces:
            periodes_occupees = []

            for lc in face.lignes_campagne.all():
                periodes_occupees.append({
                    'debut': str(lc.campagne.date_debut),
                    'fin':   str(lc.campagne.date_fin),
                    'type':  'campagne',
                    'label': lc.campagne.nom,
                })

            for rl in face.lignes_reservation.all():
                periodes_occupees.append({
                    'debut': str(rl.reservation.date_debut.date()),
                    'fin':   str(rl.reservation.date_fin.date()),
                    'type':  'reservation',
                    'label': rl.reservation.nom,
                })

            resultats.append({
                'face_uuid':       str(face.uuid) if hasattr(face, 'uuid') else str(face.pk),
                'face_label':      face.label,
                'support_code':    face.support.code,
                'disponible':      len(periodes_occupees) == 0,
                'periodes_occupees': periodes_occupees,
            })

        return JsonResponse({'resultats': resultats})

from django.conf import settings
from django.core.mail import send_mail
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_protect
import logging

logger = logging.getLogger(__name__)


class ContactFormView(View):
    """Traite le formulaire de contact et envoie un email au staff."""

    def post(self, request):
        nom        = request.POST.get('nom', '').strip()
        email      = request.POST.get('email', '').strip()
        telephone  = request.POST.get('telephone', '').strip()
        societe    = request.POST.get('societe', '').strip()
        sujet      = request.POST.get('sujet', '').strip()
        message    = request.POST.get('message', '').strip()
        conditions = request.POST.get('conditions')

        # Validation minimale côté serveur (ne jamais faire confiance au JS seul)
        if not all([nom, email, sujet, message, conditions]):
            return JsonResponse(
                {'success': False, 'error': "Veuillez remplir tous les champs obligatoires."},
                status=400,
            )

        sujets_labels = {
            'demande-reservation': 'Demande de réservation',
            'info-tarif': 'Informations tarifaires',
            'probleme-technique': 'Problème technique',
            'autre': 'Autre',
        }
        sujet_label = sujets_labels.get(sujet, sujet)

        corps = (
            f"Nouveau message reçu via le formulaire de contact\n\n"
            f"Nom       : {nom}\n"
            f"Société   : {societe or '—'}\n"
            f"Email     : {email}\n"
            f"Téléphone : {telephone or '—'}\n"
            f"Sujet     : {sujet_label}\n\n"
            f"Message :\n{message}\n"
        )

        try:
            send_mail(
                subject=f"[Contact site] {sujet_label} — {nom}",
                message=corps,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.CONTACT_EMAIL],
                fail_silently=False,
                reply_to=[email],  # pour pouvoir répondre directement au visiteur
            )
        except Exception as exc:
            logger.error("Email formulaire de contact échoué : %s", exc)
            return JsonResponse(
                {'success': False, 'error': "Une erreur est survenue lors de l'envoi. Veuillez réessayer."},
                status=500,
            )

        return JsonResponse({'success': True, 'message': "Votre message a bien été envoyé. Merci !"})




