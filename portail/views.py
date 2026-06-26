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

class AccueilView(View):
    template_name = 'portail/accueil.html'

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
        fmt     = request.GET.get('format', '').strip()
        ville   = request.GET.get('ville', '').strip()
        quartier= request.GET.get('quartier', '').strip()
        dispo   = request.GET.get('dispo', '').strip()
        q       = request.GET.get('q', '').strip()

        qs = (
            Support.objects
            .filter(type_support='panneau', actif=True)
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
        villes    = sorted({v for v in Support.objects.filter(
            type_support='panneau', actif=True).values_list('ville', flat=True) if v})
        quartiers = sorted({v for v in Support.objects.filter(
            type_support='panneau', actif=True, **(({'ville': ville}) if ville else {}),
        ).values_list('quartier', flat=True) if v})
        formats   = sorted({v for v in Support.objects.filter(
            type_support='panneau', actif=True).exclude(format='').values_list('format', flat=True) if v})

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


class SupportDetailView(View):
    template_name = 'portail/support_detail.html'

    def get(self, request, uuid):
        support = get_object_or_404(
            Support.objects
            .select_related('ecran_info')
            .prefetch_related(
                Prefetch(
                    'faces',
                    queryset=FacePanneau.objects.prefetch_related(
                        'lignes_campagne__campagne__client',
                        'lignes_reservation__reservation',
                    )
                ),
                'maintenances',
            ),
            uuid=uuid, actif=True
        )

        today = timezone.now()

        # ── Statut des faces ──────────────────────────────────────────────
        faces_data = []
        if support.type_support == 'panneau':
            for face in support.faces.all():
                statut = face.get_statut(date_debut=today, date_fin=today)

                # Prochaine disponibilité si occupée
                prochaine_dispo = None
                if statut in ('occupe', 'reserve'):
                    lc = (
                        LigneCampagne.objects.filter(
                            face=face,
                            campagne__date_fin__gte=today.date(),
                            campagne__statut__in=['en_cours', 'a_venir'],
                        )
                        .order_by('-campagne__date_fin')
                        .first()
                    )
                    if lc:
                        prochaine_dispo = lc.campagne.date_fin + timedelta(days=1)

                faces_data.append({
                    'face':            face,
                    'statut':          statut,
                    'prochaine_dispo': prochaine_dispo,
                })

        # ── Calendrier 3 mois ─────────────────────────────────────────────
        calendriers = []
        if support.type_support == 'panneau':
            debut_periode = today.date().replace(day=1)
            for _ in range(3):
                # Fin du mois courant
                if debut_periode.month == 12:
                    fin_mois = debut_periode.replace(year=debut_periode.year+1, month=1, day=1) - timedelta(days=1)
                else:
                    fin_mois = debut_periode.replace(month=debut_periode.month+1, day=1) - timedelta(days=1)

                # Jours occupés sur ce mois (1 requête par mois)
                jours_occupes = set()
                lignes = LigneCampagne.objects.filter(
                    support=support,
                    campagne__date_debut__lte=fin_mois,
                    campagne__date_fin__gte=debut_periode,
                    campagne__statut__in=['en_cours', 'a_venir'],
                )
                for lc in lignes:
                    cur = max(lc.campagne.date_debut, debut_periode)
                    end = min(lc.campagne.date_fin, fin_mois)
                    while cur <= end:
                        jours_occupes.add(cur)
                        cur += timedelta(days=1)

                # Semaines du mois
                semaines = []
                cur = debut_periode
                while cur.weekday() != 0:
                    cur -= timedelta(days=1)
                while cur <= fin_mois:
                    semaine = []
                    for _ in range(7):
                        if cur.month == debut_periode.month:
                            semaine.append({
                                'date':    cur,
                                'occupe':  cur in jours_occupes,
                                'passe':   cur < today.date(),
                            })
                        else:
                            semaine.append(None)
                        cur += timedelta(days=1)
                    semaines.append(semaine)

                calendriers.append({
                    'mois':     debut_periode,
                    'semaines': semaines,
                })

                # Mois suivant
                if debut_periode.month == 12:
                    debut_periode = debut_periode.replace(year=debut_periode.year+1, month=1)
                else:
                    debut_periode = debut_periode.replace(month=debut_periode.month+1)

        return render(request, self.template_name, {
            'support':      support,
            'faces_data':   faces_data,
            'calendriers':  calendriers,
            'maps_url': (
                f"https://www.google.com/maps?q="
                f"{support.latitude},{support.longitude}"
            ),
        })


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

class ReserverEtape1View(View):
    """Sélection des emplacements sur la carte."""
    template_name = 'portail/reserver_etape1.html'

    def _panier(self, request):
        """Retourne les faces déjà dans le panier session."""
        uuids = request.session.get('demande_faces_uuids', [])
        if not uuids:
            return []
        return list(
            FacePanneau.objects.filter(uuid__in=uuids)
            .select_related('support')
        )

    def get(self, request):
        # Pré-sélection depuis query param ?face=<uuid>
        face_uuid = request.GET.get('face')
        if face_uuid:
            faces_uuids = request.session.get('demande_faces_uuids', [])
            if face_uuid not in faces_uuids:
                faces_uuids.append(face_uuid)
            request.session['demande_faces_uuids'] = faces_uuids

        form = Etape1Form(initial={
            'faces_selectionnees': json.dumps(
                request.session.get('demande_faces_uuids', [])
            )
        })
        return render(request, self.template_name, {
            'form':   form,
            'panier': self._panier(request),
        })

    def post(self, request):
        form = Etape1Form(request.POST)
        if form.is_valid():
            request.session['demande_faces_uuids']    = form.cleaned_data['faces_uuids']
            request.session['demande_supports_uuids'] = form.cleaned_data['supports_uuids']
            return redirect('portail:reserver_etape2')

        return render(request, self.template_name, {
            'form':   form,
            'panier': self._panier(request),
        })


class ReserverEtape2View(View):
    """Sélection de la période et du projet."""
    template_name = 'portail/reserver_etape2.html'

    def _get_selections(self, request):
        faces_uuids    = request.session.get('demande_faces_uuids', [])
        supports_uuids = request.session.get('demande_supports_uuids', [])
        faces    = list(FacePanneau.objects.filter(uuid__in=faces_uuids).select_related('support'))
        supports = list(Support.objects.filter(uuid__in=supports_uuids))
        return faces, supports

    def get(self, request):
        faces, supports = self._get_selections(request)
        if not faces and not supports:
            messages.warning(request, "Veuillez d'abord sélectionner des emplacements.")
            return redirect('portail:reserver_etape1')

        # Pré-remplissage depuis session si retour arrière
        initial = request.session.get('demande_etape2', {})
        form = Etape2Form(initial=initial)
        return render(request, self.template_name, {
            'form':     form,
            'faces':    faces,
            'supports': supports,
        })

    def post(self, request):
        faces, supports = self._get_selections(request)
        form = Etape2Form(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            request.session['demande_etape2'] = {
                'date_debut':  str(d['date_debut']),
                'date_fin':    str(d['date_fin']),
                'nom_campagne': d.get('nom_campagne', ''),
                'message':     d.get('message', ''),
            }
            return redirect('portail:reserver_etape3')

        return render(request, self.template_name, {
            'form':     form,
            'faces':    faces,
            'supports': supports,
        })


class ReserverEtape3View(View):
    """Coordonnées du visiteur et soumission finale."""
    template_name = 'portail/reserver_etape3.html'

    def _check_session(self, request):
        """Vérifie que les étapes précédentes ont été complétées."""
        faces_uuids = request.session.get('demande_faces_uuids', [])
        etape2      = request.session.get('demande_etape2', {})
        if not faces_uuids or not etape2.get('date_debut'):
            return False
        return True

    def _get_recap(self, request):
        faces_uuids    = request.session.get('demande_faces_uuids', [])
        supports_uuids = request.session.get('demande_supports_uuids', [])
        etape2         = request.session.get('demande_etape2', {})
        faces    = list(FacePanneau.objects.filter(uuid__in=faces_uuids).select_related('support'))
        supports = list(Support.objects.filter(uuid__in=supports_uuids))
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
        from datetime import date as date_type

        date_debut = date_type.fromisoformat(etape2['date_debut'])
        date_fin   = date_type.fromisoformat(etape2['date_fin'])

        # ── Créer la DemandeReservation ───────────────────────────────────
        demande = DemandeReservation.objects.create(
            nom_contact          = d['nom_contact'],
            societe              = d.get('societe', ''),
            email                = d['email'],
            telephone            = d['telephone'],
            accepte_contact      = d.get('accepte_contact', False),
            date_debut_souhaitee = date_debut,
            date_fin_souhaitee   = date_fin,
            nom_campagne         = etape2.get('nom_campagne', ''),
            message              = etape2.get('message', ''),
            statut               = DemandeReservation.STATUT_NOUVELLE,
        )
        if faces:
            demande.faces_souhaitees.set(faces)
        if supports:
            demande.supports_souhaites.set(supports)

        # ── Emails ───────────────────────────────────────────────────────
        self._send_emails(demande, faces, supports, etape2)

        # ── Nettoyage session ─────────────────────────────────────────────
        for key in ('demande_faces_uuids', 'demande_supports_uuids', 'demande_etape2'):
            request.session.pop(key, None)

        return redirect('portail:confirmation', uuid=demande.uuid)

    def _send_emails(self, demande, faces, supports, etape2):
        recap_emplacements = '\n'.join(
            [f"  - {f.support.code} · Face {f.label} · {f.support.quartier}" for f in faces] +
            [f"  - Écran {s.code} · {s.quartier}" for s in supports]
        ) or '  (aucun)'

        corps_staff = (
            f"Nouvelle demande reçue : {demande.reference}\n\n"
            f"Contact   : {demande.nom_contact} ({demande.societe or '—'})\n"
            f"Email     : {demande.email}\n"
            f"Téléphone : {demande.telephone}\n\n"
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
                    'detail_url':   f"/portail/support/{support.uuid}/",
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
