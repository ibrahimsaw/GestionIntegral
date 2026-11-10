import calendar
import json
import uuid
from datetime import date, datetime, time, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count, Sum, DecimalField, Value, Max
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView, DetailView, ListView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db import transaction
from urllib.parse import urlencode

from django.urls import reverse_lazy
from accounts.models import AuditLog
from accounts.decorators import *
from accounts.audit import log_action
from .models import *
from inventory.models import (
    Support, FacePanneau, EcranNumerique, FormatSupport, Emplacement, Marche,
    SupportDeletionRequest, format_sort_key, formats_croissants,
    calculer_heures_chevauchement,
)
from .forms import (
    ClientForm,
    ContratForm,
    CampagneForm,
    LigneCampagneForm,
    PreuveDiffusionForm,
    DemandeCampagneForm,
    EcranParametresExploitationForm,
    EcranBilanMensuelForm,
    PackageEcranForm,
    DemandeGestionContactForm,
    normaliser_categorie_format,
)
from piges.models import PigeFichier
from planning.models import LogDiffusion
from portail.forms import Etape1Form
# from .mixins import *
from datetime import datetime, date, timedelta
today = timezone.now().date()
from core.mixins import SortableListMixin
from django.core.files.storage import default_storage

class GetClientContratsView(StaffRequiredMixin, View):
    def get(self, request, client_id):
        qs = Contrat.objects.filter(client_id=client_id, actif=True)
        d1 = request.GET.get('date_debut')
        d2 = request.GET.get('date_fin')
        if d1 and d2:
            try:
                qs = qs.filter(date_debut__lte=d1, date_fin__gte=d2)
            except Exception:
                pass

        data = [
            {
                'id': c.pk,
                'nom': c.nom,
                'dates': f"{c.date_debut.strftime('%d/%m/%Y')} → {c.date_fin.strftime('%d/%m/%Y')}"
            }
            for c in qs
        ]
        return JsonResponse(data, safe=False)

class GetClientCampagnesMeresView(StaffRequiredMixin, View):
    def get(self, request, client_id):
        qs = Campagne.objects.filter(client_id=client_id, est_mere=True)
        exclude_id = request.GET.get('exclude')
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)

        data = [
            {
                'id': c.pk,
                'nom': c.nom,
                'reference': c.reference or '',
                'display': f"{c.nom} ({c.reference})" if c.reference else c.nom,
            }
            for c in qs.order_by('nom')
        ]
        return JsonResponse(data, safe=False)


from django.http import HttpResponse
from django.utils.html import format_html, format_html_join
from django.urls import reverse


from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .models import Client, Campagne


def api_dashboard_clients_actifs(request):
    ville = request.GET.get('ville', '').strip()
    today = timezone.now().date()

    clients_qs = Client.objects.filter(
        actif=False,
        campagnes__date_debut__lte=today,
        campagnes__date_fin__gte=today,
    ).exclude(
        campagnes__statut__in=[Campagne.STATUT_BROUILLON, Campagne.STATUT_ANNULEE]
    ).distinct()

    if ville:
        clients_qs = clients_qs.filter(campagnes__lignes__support__ville__iexact=ville).distinct()

    clients_qs = clients_qs.order_by('nom')

    if not clients_qs.exists():
        return HttpResponse('<p class="text-muted text-center py-3">Aucun client actif trouvé.</p>')

    rows = format_html_join(
        '',
        '''<a href="#" class="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
               onclick="loadModalContent('{}', '{} — Campagnes'); return false;">
              <span><i class="bi bi-building me-2"></i>{}</span>
              <i class="bi bi-chevron-right text-muted"></i>
           </a>''',
        (
            (reverse('api_dashboard_client_campagnes', args=[c.pk]), c.nom, c.nom)
            for c in clients_qs
        )
    )
    return HttpResponse(format_html('<div class="list-group">{}</div>', rows))


def api_dashboard_campagnes(request, statut):
    ville = request.GET.get('ville', '').strip()
    campagnes_qs = Campagne.objects.filter(statut=statut).select_related('client').order_by('-date_debut')

    if ville:
        campagnes_qs = campagnes_qs.filter(lignes__support__ville__iexact=ville).distinct()

    if not campagnes_qs.exists():
        return HttpResponse('<p class="text-muted text-center py-3">Aucune campagne trouvée.</p>')

    rows = format_html_join(
        '',
        '''<a href="{}" class="list-group-item list-group-item-action">
              <div class="d-flex justify-content-between align-items-center">
                <strong>{}</strong>
                <span class="badge bg-light text-dark border">{} → {}</span>
              </div>
              <small class="text-muted">{}</small>
           </a>''',
        (
            (
                reverse('campagne_detail', args=[c.pk]),
                c.nom,
                c.date_debut.strftime('%d/%m/%Y'),
                c.date_fin.strftime('%d/%m/%Y'),
                c.client.nom,
            )
            for c in campagnes_qs
        )
    )
    return HttpResponse(format_html('<div class="list-group">{}</div>', rows))


def api_dashboard_client_campagnes(request, client_id):
    client = get_object_or_404(Client, pk=client_id)
    campagnes_qs = client.campagnes.filter(
        statut__in=['en_cours', 'a_venir', 'en_pause']
    ).order_by('-date_debut')

    back_button = format_html(
        '''<button class="btn btn-sm btn-outline-secondary mb-3"
                   onclick="loadModalContent('{}', 'Clients actifs')">
              <i class="bi bi-arrow-left me-1"></i>Retour
           </button>''',
        reverse('api_dashboard_clients_actifs')
    )

    if not campagnes_qs.exists():
        html = format_html(
            '{}<p class="text-muted text-center py-3">Aucune campagne en cours ou à venir pour ce client.</p>',
            back_button
        )
        return HttpResponse(html)

    rows = format_html_join(
        '',
        '''<a href="{}" class="list-group-item list-group-item-action">
              <div class="d-flex justify-content-between align-items-center">
                <strong>{}</strong>
                <span class="badge bg-{}">{}</span>
              </div>
              <small class="text-muted">{} → {}</small>
           </a>''',
        (
            (
                reverse('campagne_detail', args=[c.pk]),
                c.nom,
                c.statut,
                c.get_statut_display(),
                c.date_debut.strftime('%d/%m/%Y'),
                c.date_fin.strftime('%d/%m/%Y'),
            )
            for c in campagnes_qs
        )
    )
    return HttpResponse(format_html('{}<div class="list-group">{}</div>', back_button, rows))

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Sum, Q, Value, DecimalField
from django.db.models.functions import Coalesce
from django.template.defaultfilters import slugify
from django.utils import timezone
from django.views.generic import TemplateView

from inventory.models import Support, FacePanneau, FormatSupport, Marche
from .models import (
    Campagne, Client, LigneCampagne, ReservationLigne,
    STATUT_EN_ATTENTE, STATUT_CONFIRMEE,
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'campaigns/accueil_parc.html'

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff_regie_role:
            messages.error(request, "Permission refusée.")
            return redirect(f"{reverse('dashboard')}#cat-ecrans")

        action = request.POST.get('led_action')
        if action in ('exploitation', 'bilan'):
            try:
                support_id = int(request.POST.get('ecran_id', ''))
            except (TypeError, ValueError):
                messages.error(request, "Sélectionnez un écran valide.")
                return redirect(f"{reverse('dashboard')}#cat-ecrans")
            ecran = get_object_or_404(
                EcranNumerique.objects.select_related('support'),
                support_id=support_id,
            )
            ville = request.POST.get('ville', '').strip()
            if ville and ecran.support.ville.casefold() != ville.casefold():
                messages.error(request, "L’écran sélectionné ne correspond pas à la ville.")
                return redirect(f"{reverse('dashboard')}#cat-ecrans")

            if action == 'exploitation':
                instance = EcranParametresExploitation.objects.filter(
                    ecran=ecran
                ).first() or EcranParametresExploitation(ecran=ecran)
                form = EcranParametresExploitationForm(
                    request.POST, instance=instance
                )
                label = "paramètres d'exploitation"
            else:
                try:
                    annee = int(request.POST.get('annee', ''))
                    mois = int(request.POST.get('mois', ''))
                    if not 2000 <= annee <= 2100 or not 1 <= mois <= 12:
                        raise ValueError
                except (TypeError, ValueError):
                    messages.error(request, "La période du bilan est invalide.")
                    return redirect(f"{reverse('dashboard')}#cat-ecrans")
                instance = EcranBilanMensuel.objects.filter(
                    ecran=ecran, annee=annee, mois=mois
                ).first() or EcranBilanMensuel(
                    ecran=ecran, annee=annee, mois=mois
                )
                form = EcranBilanMensuelForm(request.POST, instance=instance)
                label = "bilan mensuel"

            if form.is_valid():
                form.save()
                messages.success(request, f"Les {label} de {ecran.support.code} ont été enregistrés.")
            else:
                errors = '; '.join(
                    f'{field}: {error}'
                    for field, field_errors in form.errors.items()
                    for error in field_errors
                )
                messages.error(
                    request,
                    f"Enregistrement impossible : {errors}",
                )
        elif action == 'package':
            contrat_id = request.POST.get('contrat')
            instance = None
            if contrat_id and contrat_id.isdecimal():
                instance = PackageEcran.objects.filter(
                    contrat_id=int(contrat_id)
                ).first()
            form = PackageEcranForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, "Le package écran a été enregistré.")
            else:
                errors = '; '.join(
                    f'{field}: {error}'
                    for field, field_errors in form.errors.items()
                    for error in field_errors
                )
                messages.error(
                    request,
                    f"Enregistrement impossible : {errors}",
                )
        else:
            messages.error(request, "Action de sauvegarde inconnue.")

        params = {
            key: request.POST[key]
            for key in (
                'ville', 'ecran_annee', 'ecran_date', 'ecran_id', 'led_tab'
            )
            if request.POST.get(key)
        }
        redirect_url = reverse('dashboard')
        if params:
            redirect_url += f"?{urlencode(params)}"
        return redirect(f"{redirect_url}#cat-ecrans")

    SUPPORT_GROUP_DEFINITIONS = (
        ('statique_12m2', 'Statique — 12 m²', '12m2'),
        ('statique_sucettes', 'Statique — Sucettes', 'sucette'),
        ('statique_geants', 'Statique — Géant', 'geant'),
        ('ecrans', 'Écrans numériques', 'ecran'),
        ('marches', 'Marchés', 'marche'),
        ('autres_statiques', 'Autres supports statiques', 'autres'),
    )

    CATEGORY_COLORS = {
        'Standard': 'var(--color-primary)',
        'Géant': '#f59e0b',
        'Sucette': '#a78bfa',
        'Marché': '#f97316',
    }
    DEFAULT_CATEGORY_COLOR = '#64748b'
    COLOR_ECRANS = '#06b6d4'
    COLOR_PANNE = '#94a3b8'
    INVENTORY_LABELS = {
        'statique_12m2': '12m2',
        'statique_sucettes': 'Sucette',
        'statique_geants': 'Géant',
        'autres_statiques': 'Autres',
    }

    @staticmethod
    def _support_category_key(support, format_categories):
        """Classe un support depuis son type, son rattachement marché et son format."""
        if support.type_support == Support.TYPE_ECRAN:
            return 'ecrans'
        categorie = format_categories.get(support.format, '')
        categorie_slug = slugify(categorie)
        if support.emplacement_id or categorie_slug == 'marche':
            return 'marches'
        if categorie_slug in ('12m2', 'standard'):
            return 'statique_12m2'
        if categorie_slug == 'sucette':
            return 'statique_sucettes'
        if categorie_slug == 'geant':
            return 'statique_geants'
        return 'autres_statiques'

    def _build_support_and_campaign_groups(
        self, ville_active, campagnes_actives_list, campagnes_a_venir_list,
        faces_occupees_ids=None, faces_reservees_ids=None,
    ):
        faces_occupees_ids = faces_occupees_ids or set()
        faces_reservees_ids = faces_reservees_ids or set()
        format_categories = dict(
            FormatSupport.objects.values_list('code', 'categorie')
        )
        groups = {
            key: {
                'key': key,
                'label': label,
                'inventory_label': self.INVENTORY_LABELS.get(key, label),
                'kind': 'statique' if key.startswith('statique_') or key == 'autres_statiques' else key,
                'color': {
                    'statique_12m2': 'var(--color-primary)',
                    'statique_sucettes': '#a78bfa',
                    'statique_geants': '#f59e0b',
                    'ecrans': self.COLOR_ECRANS,
                    'marches': '#f97316',
                    'autres_statiques': self.DEFAULT_CATEGORY_COLOR,
                }[key],
                'campagnes': [],
                'nb_supports': 0,
                'nb_actifs': 0,
                'nb_inactifs': 0,
                'nb_bon': 0,
                'nb_maintenance': 0,
                'nb_panne': 0,
                'nb_faces': 0,
                'nb_faces_bon': 0,
                'nb_faces_maintenance': 0,
                'nb_faces_panne': 0,
                'nb_faces_occupees': 0,
                'nb_faces_reservees': 0,
                'nb_faces_libres': 0,
                'nb_campagnes': 0,
            }
            for key, label, _ in self.SUPPORT_GROUP_DEFINITIONS
        }
        supports_qs = Support.objects.prefetch_related('faces')
        if ville_active:
            supports_qs = supports_qs.filter(ville__iexact=ville_active)
        for support in supports_qs:
            key = self._support_category_key(support, format_categories)
            group = groups[key]
            group['nb_supports'] += 1
            group['nb_actifs' if support.actif else 'nb_inactifs'] += 1
            state_key = {
                Support.ETAT_BON: 'nb_bon',
                Support.ETAT_A_MAINTENIR: 'nb_maintenance',
                Support.ETAT_PANNE: 'nb_panne',
            }.get(support.etat)
            if state_key:
                group[state_key] += 1

            if support.type_support == Support.TYPE_PANNEAU:
                for face in support.faces.all():
                    group['nb_faces'] += 1
                    face_state_key = {
                        Support.ETAT_BON: 'nb_faces_bon',
                        Support.ETAT_A_MAINTENIR: 'nb_faces_maintenance',
                        Support.ETAT_PANNE: 'nb_faces_panne',
                    }.get(face.etat)
                    if face_state_key:
                        group[face_state_key] += 1
                    if face.etat in Support.ETATS_OPERATIONNELS:
                        if face.pk in faces_occupees_ids:
                            group['nb_faces_occupees'] += 1
                        elif face.pk in faces_reservees_ids:
                            group['nb_faces_reservees'] += 1
                        else:
                            group['nb_faces_libres'] += 1

        for campaign in campagnes_actives_list + campagnes_a_venir_list:
            campaign_keys = set()
            faces_reservees = set()
            ecrans_reserves = set()
            emplacements_reserves = set()
            for ligne in campaign.lignes.all():
                support = ligne.support
                if support is None and ligne.face_id:
                    support = ligne.face.support
                if ligne.face_id:
                    faces_reservees.add(ligne.face_id)
                if support is not None:
                    if support.type_support == Support.TYPE_ECRAN:
                        ecrans_reserves.add(support.pk)
                    campaign_keys.add(self._support_category_key(support, format_categories))
                elif ligne.emplacement_id:
                    campaign_keys.add('marches')
                if ligne.emplacement_id:
                    emplacements_reserves.add(ligne.emplacement_id)
            campaign.nb_faces_reservees = len(faces_reservees)
            campaign.nb_ecrans_reserves = len(ecrans_reserves)
            campaign.nb_emplacements_reserves = len(emplacements_reserves)
            for key in campaign_keys:
                groups[key]['campagnes'].append(campaign)

        support_groups = []
        campaign_groups = []
        for key, label, _ in self.SUPPORT_GROUP_DEFINITIONS:
            group = groups[key]
            group['campagnes'].sort(key=lambda campaign: (campaign.date_debut, campaign.nom.casefold()))
            group['nb_campagnes'] = len(group['campagnes'])
            support_groups.append(group)
            campaign_groups.append(group)
        return support_groups, campaign_groups

    @staticmethod
    def _build_market_stats(ville_active, faces_occupees_ids, faces_reservees_ids):
        """Compte les panneaux et faces installés par marché, sans confondre emplacements et panneaux."""
        marches = Marche.objects.filter(ville__iexact=ville_active).order_by('nom')
        marches = marches.prefetch_related('emplacements__support_installe__faces')
        stats = []
        for marche in marches:
            supports = [
                emplacement.support_installe
                for emplacement in marche.emplacements.all()
                if hasattr(emplacement, 'support_installe')
                and emplacement.support_installe.type_support == Support.TYPE_PANNEAU
            ]
            faces = [face for support in supports for face in support.faces.all()]
            occupees = sum(
                1 for face in faces
                if face.etat in Support.ETATS_OPERATIONNELS and face.pk in faces_occupees_ids
            )
            reservees = sum(
                1 for face in faces
                if face.etat in Support.ETATS_OPERATIONNELS
                and face.pk in faces_reservees_ids
                and face.pk not in faces_occupees_ids
            )
            faces_bonnes = sum(
                1 for face in faces if face.etat in Support.ETATS_OPERATIONNELS
            )
            stats.append({
                'nom': marche.nom,
                'nb_panneaux': len(supports),
                'nb_faces': len(faces),
                'nb_faces_occupees': occupees,
                'nb_faces_reservees': reservees,
                'nb_faces_libres': faces_bonnes - occupees - reservees,
            })
        return stats

    @staticmethod
    def _count_occupied_days(intervals, year_start, year_end):
        """Compte les jours occupés sans compter deux fois les campagnes qui se chevauchent."""
        clipped = sorted(
            (max(start, year_start), min(end, year_end))
            for start, end in intervals
            if start <= year_end and end >= year_start
        )
        if not clipped:
            return 0

        occupied_days = 0
        current_start, current_end = clipped[0]
        for start, end in clipped[1:]:
            if start <= current_end + timedelta(days=1):
                current_end = max(current_end, end)
                continue
            occupied_days += (current_end - current_start).days + 1
            current_start, current_end = start, end
        return occupied_days + (current_end - current_start).days + 1

    @classmethod
    def _build_parc_analytics(
        cls, format_category_stats, format_categories_codes, ville_active, today,
        faces_occupees_ids, faces_reservees_ids,
    ):
        """Indicateurs détaillés par catégorie au format du tableau de bord de démonstration."""
        year_start = date(today.year, 1, 1)
        year_end = date(today.year, 12, 31)
        year_days = (year_end - year_start).days + 1
        elapsed_days = (today - year_start).days + 1
        tomorrow = today + timedelta(days=1)
        month_ranges = [
            (date(today.year, m, 1), date(today.year, m, calendar.monthrange(today.year, m)[1]))
            for m in range(1, 13)
        ]
        month_lengths = [(end - start).days + 1 for start, end in month_ranges]

        meta = {
            'year': today.year,
            'today': today.isoformat(),
            'month': today.month,
            'elapsed_days': elapsed_days,
            'year_days': year_days,
            'lecture_seule': False,
        }

        codes_all = [code for codes in format_categories_codes.values() for code in codes]
        if not codes_all:
            return [], meta

        formats = {f.code: f for f in FormatSupport.objects.filter(code__in=codes_all)}
        faces_qs = (
            FacePanneau.objects
            .filter(support__format__in=codes_all)
            .select_related('support__emplacement__marche')
            .order_by('support__code', 'label')
        )
        if ville_active:
            faces_qs = faces_qs.filter(support__ville__iexact=ville_active)
        faces = list(faces_qs)
        face_ids = [face.pk for face in faces]

        statuts_campagne = [
            Campagne.STATUT_EN_COURS,
            Campagne.STATUT_A_VENIR,
            Campagne.STATUT_EN_PAUSE,
            Campagne.STATUT_TERMINEE,
        ]

        camp_intervals = {}
        for face_id, start, end in LigneCampagne.objects.filter(
            face_id__in=face_ids,
            campagne__statut__in=statuts_campagne,
            campagne__date_debut__lte=year_end,
            campagne__date_fin__gte=year_start,
        ).values_list('face_id', 'campagne__date_debut', 'campagne__date_fin'):
            camp_intervals.setdefault(face_id, []).append((start, end))

        last_end = dict(
            LigneCampagne.objects.filter(
                face_id__in=face_ids,
                campagne__statut__in=statuts_campagne,
                campagne__date_debut__lte=today,
            ).values('face_id').annotate(
                derniere=Max('campagne__date_fin')
            ).values_list('face_id', 'derniere')
        )

        resa_intervals = {}
        for face_id, start, end in ReservationLigne.objects.filter(
            face_id__in=face_ids,
            reservation__statut=STATUT_CONFIRMEE,
            reservation__date_fin__gte=timezone.make_aware(
                datetime.combine(tomorrow, time.min)
            ),
            reservation__date_debut__lt=timezone.make_aware(
                datetime.combine(year_end + timedelta(days=1), time.min)
            ),
        ).values_list('face_id', 'reservation__date_debut', 'reservation__date_fin'):
            start = timezone.localtime(start).date() if timezone.is_aware(start) else start.date()
            end = timezone.localtime(end).date() if timezone.is_aware(end) else end.date()
            resa_intervals.setdefault(face_id, []).append((max(start, tomorrow), end))

        rows = []
        for face in faces:
            fid = face.pk
            support = face.support
            campagnes = camp_intervals.get(fid, [])
            realise = [(s, min(e, today)) for s, e in campagnes if s <= today]
            securise = campagnes + resa_intervals.get(fid, [])

            jours_realises = cls._count_occupied_days(realise, year_start, today)
            jours_secures = cls._count_occupied_days(securise, year_start, year_end)
            mensuel_jours = [
                cls._count_occupied_days(securise, start, end)
                for start, end in month_ranges
            ]

            if face.etat == Support.ETAT_PANNE:
                statut = 'panne'
            elif fid in faces_occupees_ids:
                statut = 'occupee'
            elif fid in faces_reservees_ids:
                statut = 'reservee'
            else:
                statut = 'libre'

            libre_depuis = None
            derniere = None
            if statut in ('libre', 'reservee'):
                last = last_end.get(fid)
                if last:
                    derniere = min(last, today)
                    libre_depuis = max((today - last).days, 0)
            libre_60 = statut == 'libre' and (libre_depuis is None or libre_depuis > 60)

            fmt = formats.get(support.format)
            lieu = (
                support.emplacement.marche.nom
                if support.emplacement_id
                else (support.quartier or support.ville or '')
            )
            row = {
                'id': fid,
                'support_id': support.pk,
                'code': support.code,
                'label': face.label,
                'nom': support.nom,
                'lieu': lieu,
                'format_code': support.format,
                'format': (fmt.dimensions if fmt else '') or support.format or '',
                'statut': statut,
                'jours': jours_realises,
                'jours_secures': jours_secures,
                'taux': round(jours_realises / elapsed_days * 100, 1),
                'secure': round(jours_secures / year_days * 100, 1),
                'libre_depuis': libre_depuis,
                'derniere': derniere.isoformat() if derniere else None,
                'libre_60': libre_60,
                'mois': [
                    round(jours / month_lengths[i] * 100)
                    for i, jours in enumerate(mensuel_jours)
                ],
                'mois_jours': mensuel_jours,
                'url': reverse('support_detail', args=[support.uuid]),
            }
            rows.append((row, mensuel_jours))

        def monthly_pct(subset):
            total = len(subset)
            if not total:
                return [0.0] * 12
            return [
                round(
                    sum(jours[i] for _, jours in subset)
                    / (total * month_lengths[i]) * 100,
                    1,
                )
                for i in range(12)
            ]

        result = []
        for stat in format_category_stats:
            codes = format_categories_codes.get(stat['categorie'], [])
            cat_rows = [(r, j) for r, j in rows if r['format_code'] in codes]
            n = len(cat_rows)
            if not n:
                continue

            nb_occ = sum(1 for r, _ in cat_rows if r['statut'] == 'occupee')
            nb_res = sum(1 for r, _ in cat_rows if r['statut'] == 'reservee')
            nb_lib = sum(1 for r, _ in cat_rows if r['statut'] == 'libre')
            nb_pan = sum(1 for r, _ in cat_rows if r['statut'] == 'panne')
            support_ids = {r['support_id'] for r, _ in cat_rows}
            support_ids_occupe = {
                r['support_id']
                for r, _ in cat_rows
                if r['statut'] == 'occupee'
            }
            jours_realises = sum(r['jours'] for r, _ in cat_rows)
            jours_secures = sum(r['jours_secures'] for r, _ in cat_rows)
            capacite = n * year_days

            formats_cat = []
            for code in codes:
                subset = [(r, j) for r, j in cat_rows if r['format_code'] == code]
                if subset:
                    fmt = formats.get(code)
                    formats_cat.append({
                        'code': code,
                        'label': (fmt.dimensions if fmt else '') or code,
                        'nb_faces': len(subset),
                        'mensuel': monthly_pct(subset),
                    })

            result.append({
                **stat,
                'nb_faces': n,
                'nb_supports': len(support_ids),
                'nb_occupe': nb_occ,
                'nb_supports_occupe': len(support_ids_occupe),
                'nb_reserve': nb_res,
                'nb_libre': nb_lib,
                'nb_panne': nb_pan,
                'nb_disponibles': n - nb_occ,
                'capacite': capacite,
                'jours_realises': jours_realises,
                'jours_secures': jours_secures,
                'taux_jour': round(nb_occ / n * 100, 1),
                'taux_cumule': round(jours_realises / (n * elapsed_days) * 100, 1),
                'annuel_realise': round(jours_realises / capacite * 100, 1),
                'annuel_securise': round(jours_secures / capacite * 100, 1),
                'libres_60': sum(1 for r, _ in cat_rows if r['libre_60']),
                'mensuel': monthly_pct(cat_rows),
                'formats': formats_cat,
                'faces': [r for r, _ in cat_rows],
            })
        category_order = {
            '12m²': 0,
            'Sucette': 1,
            'Géant': 2,
            'Marché': 3,
        }
        result.sort(
            key=lambda category: (
                category_order.get(category['categorie'], len(category_order)),
                category['categorie'].casefold(),
            )
        )
        return result, meta

    @classmethod
    def _build_annual_face_occupancy(
        cls, format_codes, ville_active, year_start, year_end, today,
        support_ids=None, include_face_rates=False,
    ):
        """Calcule l'occupation depuis janvier et la prévision annuelle par format."""
        faces = FacePanneau.objects.filter(support__format__in=format_codes)
        lines = LigneCampagne.objects.filter(
            face__support__format__in=format_codes,
            campagne__date_debut__lte=year_end,
            campagne__date_fin__gte=year_start,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_A_VENIR,
                Campagne.STATUT_EN_PAUSE,
                Campagne.STATUT_TERMINEE,
            ],
        )
        if support_ids is not None:
            faces = faces.filter(support_id__in=support_ids)
            lines = lines.filter(face__support_id__in=support_ids)
        if ville_active:
            faces = faces.filter(support__ville__iexact=ville_active)
            lines = lines.filter(face__support__ville__iexact=ville_active)

        totals_by_format = {}
        for format_code, total in faces.values('support__format').annotate(
            total=Count('id')
        ).values_list('support__format', 'total'):
            totals_by_format[format_code] = {
                'nb_faces': total,
                'jours_occupees_cumul': 0,
                'jours_occupees_annuel': 0,
            }
        if include_face_rates:
            for face_id, format_code in faces.values_list(
                'id', 'support__format'
            ):
                totals_by_format[format_code].setdefault(
                    'taux_par_face', {}
                )[face_id] = {
                    'taux_actuel': 0.0,
                    'taux_annuel': 0.0,
                }

        annual_intervals_by_face = {}
        current_intervals_by_face = {}
        for face_id, format_code, start, end in lines.values_list(
            'face_id',
            'face__support__format',
            'campagne__date_debut',
            'campagne__date_fin',
        ):
            if format_code not in totals_by_format:
                continue
            key = (face_id, format_code)
            annual_intervals_by_face.setdefault(key, []).append((start, end))
            if start <= today:
                current_intervals_by_face.setdefault(key, []).append(
                    (start, min(end, today))
                )

        reservations = ReservationLigne.objects.filter(
            face__support__format__in=format_codes,
            reservation__date_fin__gte=timezone.make_aware(datetime.combine(
                today + timedelta(days=1), time.min,
            )),
            reservation__date_debut__lt=timezone.make_aware(datetime.combine(
                year_end + timedelta(days=1), time.min,
            )),
            reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
        )
        if support_ids is not None:
            reservations = reservations.filter(face__support_id__in=support_ids)
        if ville_active:
            reservations = reservations.filter(face__support__ville__iexact=ville_active)
        for face_id, format_code, start, end in reservations.values_list(
            'face_id',
            'face__support__format',
            'reservation__date_debut',
            'reservation__date_fin',
        ):
            if format_code not in totals_by_format:
                continue
            start = timezone.localtime(start).date() if timezone.is_aware(start) else start.date()
            end = timezone.localtime(end).date() if timezone.is_aware(end) else end.date()
            key = (face_id, format_code)
            annual_intervals_by_face.setdefault(key, []).append(
                (max(start, today + timedelta(days=1)), end)
            )

        for face_id, format_code in set(annual_intervals_by_face) | set(current_intervals_by_face):
            totals = totals_by_format[format_code]
            key = (face_id, format_code)
            jours_cumules = cls._count_occupied_days(
                current_intervals_by_face.get(key, []), year_start, today
            )
            jours_annuels = cls._count_occupied_days(
                annual_intervals_by_face.get(key, []), year_start, year_end
            )
            totals['jours_occupees_cumul'] += jours_cumules
            totals['jours_occupees_annuel'] += jours_annuels
            if include_face_rates:
                elapsed_days = (today - year_start).days + 1
                year_days = (year_end - year_start).days + 1
                totals['taux_par_face'][face_id] = {
                    'taux_actuel': round(jours_cumules / elapsed_days * 100, 1),
                    'taux_annuel': round(jours_annuels / year_days * 100, 1),
                }
        return totals_by_format

    @classmethod
    def _build_screen_spot_stats(cls, ville_active, today):
        """Calcule les taux quotidien et cumulé d'occupation des écrans."""
        screens = EcranNumerique.objects.all()
        if ville_active:
            screens = screens.filter(support__ville__iexact=ville_active)

        year_start = date(today.year, 1, 1)
        year_end = date(today.year, 12, 31)
        screen_rows = list(
            screens.select_related('support').prefetch_related('programmes__plages')
        )
        screen_ids = [screen.support_id for screen in screen_rows]
        targets = {
            screen.support_id: screen.calculer_objectif_spots_annuel(
                today.year, today
            )
            for screen in screen_rows
        }
        campaign_lines = LigneCampagne.objects.filter(
            support_id__in=screen_ids,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_EN_PAUSE,
                Campagne.STATUT_A_VENIR,
                Campagne.STATUT_TERMINEE,
            ],
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=year_start,
        ).select_related('campagne', 'support')
        realized_spots = {}
        for line in campaign_lines:
            realized_spots[line.support_id] = (
                realized_spots.get(line.support_id, 0)
                + line.calculer_spots_sur_periode(year_start, today)
            )

        todays_campaign_lines = LigneCampagne.objects.filter(
            support_id__in=screen_ids,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_A_VENIR,
            ],
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
        ).select_related('campagne', 'support')
        today_spots = {}
        for line in todays_campaign_lines:
            today_spots[line.support_id] = (
                today_spots.get(line.support_id, 0)
                + line.calculer_spots_sur_periode(today, today)
            )

        tomorrow = today + timedelta(days=1)
        future_lines = LigneCampagne.objects.filter(
            support_id__in=screen_ids,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_A_VENIR,
            ],
        ).select_related('campagne', 'support')
        future_spots = {}
        for line in future_lines:
            future_spots[line.support_id] = future_spots.get(line.support_id, 0) + (
                line.calculer_spots_sur_periode(tomorrow, year_end)
            )

        counts_by_resolution = {
            row['resolution']: row
            for row in screens.values('resolution').annotate(
                nb_ecrans=Count('support_id', distinct=True),
                nb_occupees=Count(
                    'support_id',
                    filter=Q(
                        support__lignes_campagne__campagne__statut__in=[
                            Campagne.STATUT_EN_COURS,
                            Campagne.STATUT_EN_PAUSE,
                        ],
                        support__lignes_campagne__campagne__date_debut__lte=today,
                        support__lignes_campagne__campagne__date_fin__gte=today,
                    ),
                    distinct=True,
                ),
            )
        }
        stats_by_screen = {}
        screens_by_id = {screen.support_id: screen for screen in screen_rows}
        for screen_id in screen_ids:
            target = targets.get(screen_id)
            realized = realized_spots.get(screen_id, 0)
            screen = screens_by_id[screen_id]
            spots_today = today_spots.get(screen_id, 0)
            available_seconds_today = screen.secondes_disponibles_jour(today)
            reserved_seconds_today = (
                spots_today * EcranNumerique.DUREE_SPOT_SECONDES
            )
            available_seconds_year_to_date = (
                (target or 0) * EcranNumerique.DUREE_SPOT_SECONDES
            )
            reserved_seconds_year_to_date = (
                realized * EcranNumerique.DUREE_SPOT_SECONDES
            )
            forecast = realized + future_spots.get(screen_id, 0)
            stats_by_screen[screen_id] = {
                'objectif_spots_annuel': target,
                'spots_realises': realized,
                'spots_aujourdhui': spots_today,
                'spots_previsionnels': forecast,
                'secondes_reservees_aujourdhui': reserved_seconds_today,
                'secondes_disponibles_aujourdhui': available_seconds_today,
                'secondes_reservees_cumulees': reserved_seconds_year_to_date,
                'secondes_disponibles_cumulees': available_seconds_year_to_date,
                'taux_actuel': round(
                    reserved_seconds_today / available_seconds_today * 100, 1
                ) if available_seconds_today else None,
                'taux_annuel': round(
                    reserved_seconds_year_to_date
                    / available_seconds_year_to_date * 100,
                    1,
                ) if available_seconds_year_to_date else None,
            }
        return stats_by_screen

    @classmethod
    def _build_screen_resolution_stats(cls, ville_active, today, stats_by_screen=None):
        """Agrège les objectifs et taux des écrans par résolution."""
        screens = EcranNumerique.objects.all()
        if ville_active:
            screens = screens.filter(support__ville__iexact=ville_active)
        screen_rows = list(screens.values_list('support_id', 'resolution'))
        if stats_by_screen is None:
            stats_by_screen = cls._build_screen_spot_stats(ville_active, today)

        counts_by_resolution = {
            row['resolution']: row
            for row in screens.values('resolution').annotate(
                nb_ecrans=Count('support_id', distinct=True),
                nb_occupees=Count(
                    'support_id',
                    filter=Q(
                        support__lignes_campagne__campagne__statut__in=[
                            Campagne.STATUT_EN_COURS,
                            Campagne.STATUT_EN_PAUSE,
                        ],
                        support__lignes_campagne__campagne__date_debut__lte=today,
                        support__lignes_campagne__campagne__date_fin__gte=today,
                    ),
                    distinct=True,
                ),
            )
        }
        grouped = {}
        for screen_id, resolution in screen_rows:
            group = grouped.setdefault(resolution, {
                'objectif': 0,
                'realises': 0,
                'previsionnels': 0,
                'secondes_reservees_aujourdhui': 0,
                'secondes_disponibles_aujourdhui': 0,
                'secondes_reservees_cumulees': 0,
                'secondes_disponibles_cumulees': 0,
            })
            metrics = stats_by_screen.get(screen_id, {})
            objectif = metrics.get('objectif_spots_annuel')
            if objectif is not None:
                group['objectif'] += objectif
                group['realises'] += metrics.get('spots_realises', 0)
                group['previsionnels'] += metrics.get('spots_previsionnels', 0)
            for key in (
                'secondes_reservees_aujourdhui',
                'secondes_disponibles_aujourdhui',
                'secondes_reservees_cumulees',
                'secondes_disponibles_cumulees',
            ):
                group[key] += metrics.get(key, 0)

        return [
            {
                'resolution': label,
                'nb_ecrans': counts_by_resolution[code]['nb_ecrans'],
                'nb_occupees': counts_by_resolution[code]['nb_occupees'],
                'nb_libres': (
                    counts_by_resolution[code]['nb_ecrans']
                    - counts_by_resolution[code]['nb_occupees']
                ),
                'objectif_spots_annuel': grouped.get(code, {}).get('objectif', 0),
                'spots_realises': grouped.get(code, {}).get('realises', 0),
                'spots_previsionnels': grouped.get(code, {}).get('previsionnels', 0),
                'taux_actuel': round(
                    grouped[code]['secondes_reservees_aujourdhui']
                    / grouped[code]['secondes_disponibles_aujourdhui'] * 100,
                    1,
                ) if grouped.get(code, {}).get('secondes_disponibles_aujourdhui') else None,
                'taux_annuel': round(
                    grouped[code]['secondes_reservees_cumulees']
                    / grouped[code]['secondes_disponibles_cumulees'] * 100,
                    1,
                ) if grouped.get(code, {}).get('secondes_disponibles_cumulees') else None,
            }
            for code, label in EcranNumerique.RESOLUTION_CHOICES
            if code in counts_by_resolution
        ]

    @classmethod
    def _build_led_dashboard_data(cls, ville_active, reference_date, stats_by_screen):
        """Prépare les vues LED à partir des campagnes, contrats et logs enregistrés."""
        year_start = date(reference_date.year, 1, 1)
        year_end = date(reference_date.year, 12, 31)
        screens = EcranNumerique.objects.select_related('support').prefetch_related(
            'programmes__plages', 'parametres_exploitation'
        )
        if ville_active:
            screens = screens.filter(support__ville__iexact=ville_active)
        screen_rows = list(screens.order_by('support__code'))
        screen_ids = [screen.support_id for screen in screen_rows]
        screen_objects_by_id = {
            screen.support_id: screen for screen in screen_rows
        }
        bilan_rows = EcranBilanMensuel.objects.filter(
            ecran_id__in=[screen.pk for screen in screen_rows],
            annee=reference_date.year,
        )
        bilans_by_screen_month = {
            (bilan.ecran_id, bilan.mois): bilan for bilan in bilan_rows
        }
        screen_data = []
        monthly_by_screen = {}
        monthly_capacity_by_screen = {}
        monthly_names = ['Jan', 'Fév', 'Mar', 'Avr', 'Mai', 'Juin',
                         'Juil', 'Aoû', 'Sep', 'Oct', 'Nov', 'Déc']
        for screen in screen_rows:
            support = screen.support
            exploitation = getattr(screen, 'parametres_exploitation', None)
            metrics = stats_by_screen.get(screen.support_id, {})
            monthly_by_screen[screen.support_id] = [0] * 12
            monthly_capacity = []
            debut_journee = datetime.combine(
                date(reference_date.year, 1, 1), screen.heure_allumage
            )
            fin_journee = datetime.combine(
                date(reference_date.year, 1, 1), screen.heure_extinction
            )
            duree_allumage = fin_journee - debut_journee
            if duree_allumage.total_seconds() < 0:
                duree_allumage += timedelta(days=1)
            spots_par_jour = (
                duree_allumage.total_seconds() / 3600
                * 60
                * EcranNumerique.SPOTS_PAR_MINUTE
            )
            for month in range(1, 13):
                nombre_jours = calendar.monthrange(
                    reference_date.year, month
                )[1]
                monthly_capacity.append(
                    int(nombre_jours * spots_par_jour)
                )
            monthly_capacity_by_screen[screen.support_id] = monthly_capacity
            available_seconds_today = screen.secondes_disponibles_jour(
                reference_date
            )
            daily_spot_capacity = int(
                available_seconds_today
                * EcranNumerique.SPOTS_PAR_MINUTE
                // 60
            )
            screen_data.append({
                'id': screen.support_id,
                'code': support.code,
                'nom': support.nom,
                'uuid': str(support.uuid),
                'url': reverse('support_detail', args=[support.uuid]),
                'ville': support.ville,
                'quartier': support.quartier,
                'etat': support.get_etat_display(),
                'resolution': screen.get_resolution_display(),
                'cellule': screen.get_cellule_display(),
                'type_ecran': screen.get_type_ecran_display(),
                'duree_spot_secondes': EcranNumerique.DUREE_SPOT_SECONDES,
                'horaires': screen.horaires_actifs_display(reference_date),
                'heure_allumage': screen.heure_allumage.strftime('%H:%M'),
                'heure_extinction': screen.heure_extinction.strftime('%H:%M'),
                'objectif_annuel': metrics.get('objectif_spots_annuel') or 0,
                'spots_programmes_cumules': metrics.get('spots_realises', 0),
                'spots_programmes_annee': 0,
                'spots_programmes_jour': 0,
                'taux_jour': None,
                'capacite_spots_jour': daily_spot_capacity,
                'taux_annuel': metrics.get('taux_annuel'),
                'secondes_reservees': metrics.get(
                    'secondes_reservees_aujourdhui', 0
                ),
                'secondes_disponibles': metrics.get(
                    'secondes_disponibles_aujourdhui', 0
                ),
                'spots_diffuses': 0,
                'valeur_programmee': 0.0,
                'valeur_diffusee_estimee': 0.0,
                'mensuel_diffuse': [0] * 12,
                'mensuel_duree_diffusee': [0] * 12,
                'mensuel_valeur_diffusee': [0.0] * 12,
                'mensuel_programme': [0] * 12,
                'mensuel_capacite': monthly_capacity,
                'mensuel_taux_diffusion': [None] * 12,
                'mensuel_valeur_programmee': [0.0] * 12,
                'programmation_horaire': [],
                'puissance_kw': (
                    float(exploitation.puissance_kw)
                    if exploitation and exploitation.puissance_kw is not None
                    else None
                ),
                'mode_commande': (
                    exploitation.get_mode_commande_display()
                    if exploitation and exploitation.mode_commande
                    else None
                ),
                'cout_intervention_fcfa': (
                    float(exploitation.cout_intervention_fcfa)
                    if exploitation and exploitation.cout_intervention_fcfa is not None
                    else None
                ),
                'seuil_extinction_heures': (
                    float(exploitation.seuil_extinction_heures)
                    if exploitation and exploitation.seuil_extinction_heures is not None
                    else None
                ),
                'mensuel_bilans': [
                    cls._serialize_led_bilan(
                        bilans_by_screen_month.get((screen.pk, month))
                    )
                    for month in range(1, 13)
                ],
            })

        if not screen_ids:
            return {
                'ecran_dashboard_screens': screen_data,
                'ecran_dashboard_monthly': [
                    {
                        'mois': month,
                        'spots': 0,
                        'capacite': 0,
                        'taux_programme': None,
                        'diffusions': 0,
                    }
                    for month in monthly_names
                ],
                'ecran_dashboard_campaigns': [],
                'ecran_dashboard_contracts': [],
                'ecran_dashboard_has_logs': False,
            }

        lines = list(
            LigneCampagne.objects.filter(
                support_id__in=screen_ids,
                campagne__date_debut__lte=year_end,
                campagne__date_fin__gte=year_start,
            ).exclude(
                campagne__statut__in=[
                    Campagne.STATUT_BROUILLON,
                    Campagne.STATUT_ANNULEE,
                ]
            ).select_related(
                'support',
                'campagne',
                'campagne__client',
                'campagne__contrat__package_ecran',
            ).prefetch_related('campagne__pauses').order_by(
                'campagne__date_debut', 'campagne__nom', 'support__code'
            )
        )

        campaigns = []
        contracts = {}
        value_by_screen = {}
        unit_price_by_campaign_screen = {}
        screen_data_index = {
            screen['id']: index for index, screen in enumerate(screen_data)
        }
        for line in lines:
            campaign = line.campagne
            actual_line_start = max(
                line.date_debut or campaign.date_debut, campaign.date_debut
            )
            actual_line_end = min(
                line.date_fin or campaign.date_fin, campaign.date_fin
            )
            line_start = max(actual_line_start, year_start)
            line_end = min(actual_line_end, year_end)
            if line_start > line_end:
                continue
            planned_spots = line.calculer_spots_sur_periode(line_start, line_end)
            planned_today = line.calculer_spots_sur_periode(
                reference_date, reference_date
            )
            screen_id = line.support_id
            screen_index = screen_data_index[screen_id]
            screen_data[screen_index]['spots_programmes_jour'] += planned_today
            unit_price = campaign.prix or 0
            campaign_value = float(unit_price * planned_spots)
            value_by_screen[screen_id] = value_by_screen.get(screen_id, 0) + campaign_value
            unit_price_by_campaign_screen[(screen_id, campaign.pk)] = (
                float(unit_price) if unit_price else None
            )
            screen_data[screen_index]['spots_programmes_annee'] += planned_spots
            if (
                actual_line_start <= reference_date <= actual_line_end
                and planned_today
            ):
                screen = screen_objects_by_id[screen_id]
                active_ranges = screen.plages_actives(reference_date)
                frequency = line.get_frequence_effective()
                visual_count = (
                    campaign.get_nombre_visuels_effective() or 1
                )
                if frequency:
                    for hour in range(24):
                        hour_start = time(hour, 0)
                        hour_end = time(hour + 1, 0) if hour < 23 else time(23, 59, 59)
                        clipped_ranges = [
                            (max(start, hour_start), min(end, hour_end))
                            for start, end in active_ranges
                            if max(start, hour_start) < min(end, hour_end)
                        ]
                        overlap_hours = calculer_heures_chevauchement(
                            clipped_ranges,
                            line.get_tranches_effective(),
                        )
                        if not overlap_hours:
                            continue
                        hourly_spots = (
                            overlap_hours * (3600 / frequency) * visual_count
                        )
                        schedule = screen_data[screen_index][
                            'programmation_horaire'
                        ]
                        hour_row = next(
                            (
                                row for row in schedule
                                if row['heure'] == f'{hour:02d}:00'
                            ),
                            None,
                        )
                        if hour_row is None:
                            capacity = sum(
                                (
                                    datetime.combine(
                                        reference_date,
                                        min(end, hour_end),
                                    )
                                    - datetime.combine(
                                        reference_date,
                                        max(start, hour_start),
                                    )
                                ).total_seconds() / 60
                                * EcranNumerique.SPOTS_PAR_MINUTE
                                for start, end in active_ranges
                                if max(start, hour_start) < min(end, hour_end)
                            )
                            hour_row = {
                                'heure': f'{hour:02d}:00',
                                'spots_bruts': 0.0,
                                'capacite': round(capacity),
                                'campagnes': [],
                            }
                            schedule.append(hour_row)
                        hour_row['spots_bruts'] += hourly_spots
                        hour_row['campagnes'].append({
                            'nom': campaign.nom,
                            'client': campaign.client.nom,
                            'spots': round(hourly_spots),
                        })
            selected_month_spots = 0
            for month in range(1, 13):
                month_start = date(reference_date.year, month, 1)
                month_end = date(
                    reference_date.year,
                    month,
                    calendar.monthrange(reference_date.year, month)[1],
                )
                overlap_start = max(actual_line_start, month_start)
                overlap_end = min(actual_line_end, month_end)
                month_spots = (
                    line.calculer_spots_sur_periode(overlap_start, overlap_end)
                    if overlap_start <= overlap_end else 0
                )
                screen_data[screen_index]['mensuel_programme'][month - 1] += (
                    month_spots
                )
                screen_data[screen_index]['mensuel_valeur_programmee'][month - 1] += (
                    float(unit_price * month_spots)
                )
                if month == reference_date.month:
                    selected_month_spots = month_spots
            contract = campaign.contrat
            contract_reference = ''
            if contract:
                contract_reference = contract.reference or contract.get_type_document_display()
                package = getattr(contract, 'package_ecran', None)
                contract_row = contracts.setdefault(contract.pk, {
                    'id': contract.pk,
                    'reference': contract_reference,
                    'nom': contract.nom or contract_reference,
                    'client': contract.client.nom,
                    'type': contract.get_type_document_display(),
                    'type_contrat': contract.get_type_contrat_display(),
                    'date_debut': contract.date_debut.isoformat(),
                    'date_fin': contract.date_fin.isoformat(),
                    'actif': contract.actif,
                    'nb_spots': contract.nb_spots,
                    'campagnes': [],
                    'screen_ids': [],
                    'campagne_ids': [],
                    'spots_programmes': 0,
                    'valeur_programmee_estimee': 0.0,
                    'agence': package.agence if package else '',
                    'annonceur': package.annonceur if package else '',
                    'prix_unitaire_package': (
                        float(package.prix_unitaire_fcfa)
                        if package and package.prix_unitaire_fcfa is not None
                        else None
                    ),
                })
                campaign_label = campaign.nom
                if campaign_label not in contract_row['campagnes']:
                    contract_row['campagnes'].append(campaign_label)
                if screen_id not in contract_row['screen_ids']:
                    contract_row['screen_ids'].append(screen_id)
                if campaign.pk not in contract_row['campagne_ids']:
                    contract_row['campagne_ids'].append(campaign.pk)
                contract_row['spots_programmes'] += planned_spots
                contract_row['valeur_programmee_estimee'] += campaign_value

            campaigns.append({
                'campagne_id': campaign.pk,
                'ligne_id': line.pk,
                'screen_id': screen_id,
                'screen_code': line.support.code,
                'screen_nom': line.support.nom,
                'nom': campaign.nom,
                'client': campaign.client.nom,
                'statut': campaign.get_statut_display(),
                'date_reference': reference_date.isoformat(),
                'active_a_date': (
                    actual_line_start <= reference_date <= actual_line_end
                ),
                'date_debut': actual_line_start.isoformat(),
                'date_fin': actual_line_end.isoformat(),
                'spots_programmes': planned_spots,
                'spots_programmes_mois_selectionne': selected_month_spots,
                'mois_selectionne': reference_date.month,
                'spots_programmes_jour': planned_today,
                'mensuel_diffuse': [0] * 12,
                'duree': line.get_duree_passage_effective() or 0,
                'frequence': line.get_frequence_effective() or 0,
                'tranches': line.get_tranches_effective() or '',
                'contrat': contract_reference,
                'valeur_programmee': campaign_value,
                'prix_unitaire': float(unit_price),
                'prix_renseigne': bool(unit_price),
                'spots_diffuses': 0,
            })

        logs = list(
            LogDiffusion.objects.filter(
                ecran__support_id__in=screen_ids,
                timestamp__date__gte=year_start,
                timestamp__date__lte=year_end,
            ).values(
                'ecran__support_id',
                'campagne_id',
                'ligne_id',
                'timestamp__month',
            ).annotate(
                nombre=Count('pk'),
                duree_totale=Sum('duree_sec'),
            )
        )
        screen_data_by_id = {screen['id']: screen for screen in screen_data}
        logs_by_campaign_screen = {}
        logs_by_line = {}
        logs_by_line_month = {}
        for log in logs:
            screen_id = log['ecran__support_id']
            month_index = log['timestamp__month'] - 1
            monthly_by_screen[screen_id][month_index] += log['nombre']
            screen_data_by_id[screen_id]['mensuel_diffuse'][month_index] += log['nombre']
            screen_data_by_id[screen_id]['mensuel_duree_diffusee'][month_index] += (
                log['duree_totale'] or 0
            )
            screen_data_by_id[screen_id]['spots_diffuses'] += log['nombre']
            unit_price = unit_price_by_campaign_screen.get(
                (screen_id, log['campagne_id'])
            )
            if unit_price is not None:
                screen_data_by_id[screen_id]['mensuel_valeur_diffusee'][month_index] += (
                    unit_price * log['nombre']
                )
            if log['campagne_id']:
                key = (screen_id, log['campagne_id'])
                logs_by_campaign_screen[key] = (
                    logs_by_campaign_screen.get(key, 0) + log['nombre']
                )
            if log['ligne_id']:
                logs_by_line[log['ligne_id']] = (
                    logs_by_line.get(log['ligne_id'], 0) + log['nombre']
                )
                line_month_key = (log['ligne_id'], month_index)
                logs_by_line_month[line_month_key] = (
                    logs_by_line_month.get(line_month_key, 0) + log['nombre']
                )

        for screen in screen_data:
            capacity = screen['capacite_spots_jour']
            screen['taux_jour'] = (
                round(
                    screen['spots_programmes_jour'] / capacity * 100,
                    1,
                )
                if capacity else None
            )
            for hour_row in screen['programmation_horaire']:
                hour_row['spots'] = round(hour_row.pop('spots_bruts'))
            screen['valeur_programmee'] = round(value_by_screen.get(screen['id'], 0), 2)
            screen['valeur_diffusee_estimee'] = round(sum(
                price * logs_by_campaign_screen.get((screen['id'], campaign_id), 0)
                for (campaign_screen_id, campaign_id), price
                in unit_price_by_campaign_screen.items()
                if campaign_screen_id == screen['id'] and price is not None
            ), 2)
            for month, planned in enumerate(screen['mensuel_programme']):
                capacity = monthly_capacity_by_screen[screen['id']][month]
                screen['mensuel_taux_diffusion'][month] = (
                    round(
                        screen['mensuel_duree_diffusee'][month]
                        / (
                            capacity
                            * EcranNumerique.DUREE_SPOT_SECONDES
                        )
                        * 100,
                        1,
                    )
                    if capacity else None
                )
        for campaign in campaigns:
            campaign['spots_diffuses'] = logs_by_line.get(campaign['ligne_id'], 0)
            campaign['mensuel_diffuse'] = [
                logs_by_line_month.get((campaign['ligne_id'], month), 0)
                for month in range(12)
            ]
        for contract in contracts.values():
            contract['spots_diffuses_journalises'] = sum(
                count
                for (screen_id, campaign_id), count in logs_by_campaign_screen.items()
                if campaign_id in contract['campagne_ids']
            )

        return {
            'ecran_dashboard_screens': screen_data,
            'ecran_dashboard_monthly': [
                {
                    'mois': month,
                    'spots': sum(values[index] for values in monthly_by_screen.values()),
                    'capacite': sum(
                        values[index]
                        for values in monthly_capacity_by_screen.values()
                    ),
                    'taux_programme': (
                        round(
                            sum(
                                screen['mensuel_programme'][index]
                                for screen in screen_data
                            )
                            / sum(
                                values[index]
                                for values in monthly_capacity_by_screen.values()
                            )
                            * 100,
                            1,
                        )
                        if sum(
                            values[index]
                            for values in monthly_capacity_by_screen.values()
                        )
                        else None
                    ),
                    'taux_diffusion_journalisee': (
                        round(
                            sum(
                                screen['mensuel_duree_diffusee'][index]
                                for screen in screen_data
                            )
                            / (
                                sum(
                                    values[index]
                                    for values in monthly_capacity_by_screen.values()
                                )
                                * EcranNumerique.DUREE_SPOT_SECONDES
                            )
                            * 100,
                            1,
                        )
                        if sum(
                            values[index]
                            for values in monthly_capacity_by_screen.values()
                        )
                        else None
                    ),
                    'spots_programmes': sum(
                        screen['mensuel_programme'][index]
                        for screen in screen_data
                    ),
                }
                for index, month in enumerate(monthly_names)
            ],
            'ecran_dashboard_campaigns': campaigns,
            'ecran_dashboard_contracts': list(contracts.values()),
            'ecran_dashboard_has_logs': bool(logs),
        }

    @staticmethod
    def _serialize_led_bilan(bilan):
        if not bilan:
            return None
        return {
            'consommation_kwh': (
                float(bilan.consommation_kwh)
                if bilan.consommation_kwh is not None else None
            ),
            'tarif_energie_fcfa_kwh': (
                float(bilan.tarif_energie_fcfa_kwh)
                if bilan.tarif_energie_fcfa_kwh is not None else None
            ),
            'cout_electricite_fcfa': (
                float(bilan.cout_electricite_fcfa)
                if bilan.cout_electricite_fcfa is not None else None
            ),
            'total_charges_fcfa': (
                float(bilan.total_charges_fcfa)
                if bilan.total_charges_fcfa is not None else None
            ),
            'encaissements_fcfa': (
                float(bilan.encaissements_fcfa)
                if bilan.encaissements_fcfa is not None else None
            ),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        current_date = timezone.localdate()
        try:
            selected_screen_year = int(
                self.request.GET.get('ecran_annee', current_date.year)
            )
            if not 2000 <= selected_screen_year <= 2100:
                raise ValueError
        except (TypeError, ValueError):
            selected_screen_year = current_date.year
        raw_screen_date = self.request.GET.get('ecran_date', '')
        try:
            selected_screen_date = (
                date.fromisoformat(raw_screen_date)
                if raw_screen_date
                else current_date
            )
        except ValueError:
            selected_screen_date = current_date
        if not raw_screen_date and selected_screen_year != current_date.year:
            selected_screen_date = date(selected_screen_year, 1, 1)
        elif selected_screen_date.year != selected_screen_year:
            selected_screen_date = date(selected_screen_year, 1, 1)
        ville_active = self.request.GET.get("ville", "").strip()
        supports_classes = Support.objects.filter(
            Q(type_support=Support.TYPE_ECRAN)
            | Q(emplacement__isnull=False)
            | Q(format__in=FormatSupport.objects.exclude(categorie='').values('code'))
        )
        villes = [
            row['ville']
            for row in supports_classes.exclude(ville='')
            .values('ville')
            .annotate(nb_supports=Count('pk', distinct=True))
            .order_by('-nb_supports', 'ville')
        ]
        ville_active = next(
            (
                ville for ville in villes
                if ville.casefold() == ville_active.casefold()
            ),
            villes[0] if villes else '',
        )

        # ── Mise à jour des statuts en masse : 2 requêtes au lieu d'une
        # écriture par campagne (auto_update_statut() en boucle coûtait
        # jusqu'à N requêtes + N écritures à chaque chargement du dashboard) ──
        Campagne.objects.filter(
            statut__in=[Campagne.STATUT_A_VENIR, Campagne.STATUT_EN_COURS],
            date_fin__lt=today,
        ).exclude(statut=Campagne.STATUT_ANNULEE).update(statut=Campagne.STATUT_TERMINEE)

        Campagne.objects.filter(
            statut=Campagne.STATUT_A_VENIR,
            date_debut__lte=today,
            date_fin__gte=today,
        ).update(statut=Campagne.STATUT_EN_COURS)

        # ── Formats groupés par catégorie (1 requête) ──
        format_categories_codes = {}
        for format_support in formats_croissants(
            FormatSupport.objects.exclude(categorie='')
        ):
            format_categories_codes.setdefault(
                format_support.categorie,
                [],
            ).append(format_support.code)

        # ── Querysets de base ──
        supports = Support.objects.all()
        campagnes_qs = Campagne.objects.select_related('client').order_by('-created_at')
        clients_qs = Client.objects.filter(
            actif=False,
            campagnes__date_debut__lte=today,
            campagnes__date_fin__gte=today,
        ).exclude(
            campagnes__statut__in=[Campagne.STATUT_BROUILLON, Campagne.STATUT_ANNULEE]
        ).distinct()
        campagnes_actives_qs = Campagne.objects.filter(statut='en_cours', est_mere=False).select_related('client')
        campagnes_a_venir_qs = Campagne.objects.filter(statut='a_venir').select_related('client')

        if ville_active:
            supports = supports.filter(ville__iexact=ville_active)
            campagne_ville = (
                Q(lignes__support__ville__iexact=ville_active)
                | Q(lignes__face__support__ville__iexact=ville_active)
                | Q(lignes__emplacement__marche__ville__iexact=ville_active)
            )
            campagnes_qs = campagnes_qs.filter(campagne_ville).distinct()
            clients_qs = clients_qs.filter(
                Q(campagnes__lignes__support__ville__iexact=ville_active)
                | Q(campagnes__lignes__face__support__ville__iexact=ville_active)
                | Q(campagnes__lignes__emplacement__marche__ville__iexact=ville_active)
            ).distinct()
            campagnes_actives_qs = campagnes_actives_qs.filter(campagne_ville).distinct()
            campagnes_a_venir_qs = campagnes_a_venir_qs.filter(campagne_ville).distinct()

        # Prefetch pour limiter les requêtes N+1 déclenchées par montant_total()
        campagnes_a_venir_list = list(
            campagnes_a_venir_qs.prefetch_related(
                'lignes__face__support', 'lignes__support',
                'lignes__support__emplacement__marche', 'lignes__support__ecran_info',
                'lignes__emplacement__marche',
            )
        )
        campagnes_actives_list = list(
            campagnes_actives_qs.prefetch_related(
                'lignes__face__support', 'lignes__support',
                'lignes__support__emplacement__marche', 'lignes__support__ecran_info',
                'lignes__emplacement__marche',
            )
        )

        revenu_actif = sum(c.montant_total() for c in campagnes_actives_list)
        revenu_a_venir = sum(c.montant_total() for c in campagnes_a_venir_list)

        campagnes_meres_qs = Campagne.objects.filter(
            est_mere=True, statut__in=['en_cours', 'a_venir', 'en_pause']
        ).prefetch_related('sous_campagnes__lignes__face__support')
        campagnes_meres_count = campagnes_meres_qs.count()
        revenu_meres = sum(c.montant_total() for c in campagnes_meres_qs)

        # ── Top campagnes : on ne calcule montant_total() que sur les
        # campagnes actives/à venir (déjà chargées ci-dessus), au lieu
        # de tout l'historique via campagnes_qs. ──
        candidats = campagnes_actives_list + campagnes_a_venir_list
        top_campaignes_prix = sorted(candidats, key=lambda c: c.montant_total(), reverse=True)[:6]
        top_campaigns_json = json.dumps([
            {
                'label': c.nom[:30],
                'client': c.client.nom,
                'revenu': float(c.montant_total()),
                'type': c.type_support or 'Mère',
                'pk': c.pk,
            }
            for c in top_campaignes_prix
        ])

        # ── Faces les plus productives ──
        top_faces_qs = FacePanneau.objects.select_related('support').annotate(
            usage_count=Count('lignes_campagne', filter=Q(lignes_campagne__campagne__statut__in=['en_cours', 'a_venir', 'en_pause'])),
            revenue=Coalesce(
                Sum('lignes_campagne__campagne__prix', filter=Q(lignes_campagne__campagne__statut__in=['en_cours', 'a_venir', 'en_pause'])),
                Value(0, output_field=DecimalField(max_digits=12, decimal_places=2)),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ).filter(usage_count__gt=0)

        if ville_active:
            top_faces_qs = top_faces_qs.filter(support__ville__iexact=ville_active)

        top_faces = top_faces_qs.order_by('-revenue', '-usage_count')[:6]
        top_faces_json = json.dumps([
            {
                'label': f'{f.support.code}-{f.label}',
                'revenue': float(f.revenue or 0),
                'usage': f.usage_count,
                'url': reverse('support_detail', args=[f.support.uuid]),
            }
            for f in top_faces
        ])

        # ── Comptages du parc : 1 seule requête agrégée au lieu de 7 ──
        parc = supports.aggregate(
            total=Count('id'),
            nb_panneaux=Count('id', filter=Q(type_support=Support.TYPE_PANNEAU)),
            nb_panneaux_bon=Count(
                'id',
                filter=Q(
                    type_support=Support.TYPE_PANNEAU,
                    etat__in=Support.ETATS_OPERATIONNELS,
                ),
            ),
            nb_ecrans=Count('id', filter=Q(type_support=Support.TYPE_ECRAN)),
            nb_ecrans_bon=Count(
                'id',
                filter=Q(
                    type_support=Support.TYPE_ECRAN,
                    etat__in=Support.ETATS_OPERATIONNELS,
                ),
            ),
            nb_ecrans_panne=Count('id', filter=Q(type_support=Support.TYPE_ECRAN, etat=Support.ETAT_PANNE)),
            nb_supports_panne=Count('id', filter=Q(etat=Support.ETAT_PANNE)),
            nb_supports_bon=Count('id', filter=Q(etat__in=Support.ETATS_OPERATIONNELS)),
        )

        # ── Faces occupées / réservées (déjà optimal) ──
        faces_occupees_ids = set(
            LigneCampagne.objects.filter(
                Q(
                    campagne__statut__in=['en_cours', 'a_venir'],
                    campagne__date_debut__lte=today,
                    campagne__date_fin__gte=today,
                )
                | Q(
                    campagne__statut='en_pause',
                    campagne__date_debut__lte=today,
                    campagne__date_mise_en_pause__lte=today,
                )
            ).values_list('face_id', flat=True)
        )
        faces_reservees_ids = set(
            ReservationLigne.objects.filter(
                reservation__date_fin__gte=timezone.make_aware(
                    datetime.combine(today, time.min)
                ),
                reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
            ).values_list('face_id', flat=True)
        )

        format_category_stats = self._build_format_category_stats(
            format_categories_codes, ville_active, faces_occupees_ids, faces_reservees_ids
        )
        formats_stats = self._build_format_stats(
            ville_active, faces_occupees_ids, faces_reservees_ids
        )
        parc_categories, parc_meta = self._build_parc_analytics(
            format_category_stats, format_categories_codes, ville_active, today,
            faces_occupees_ids, faces_reservees_ids,
        )
        parc_meta['lecture_seule'] = bool(
            getattr(self.request.user, 'is_technicien', False)
        )
        year_start = date(today.year, 1, 1)
        year_end = date(today.year, 12, 31)
        year_days = (year_end - year_start).days + 1
        elapsed_days = (today - year_start).days + 1
        annual_face_occupancy = self._build_annual_face_occupancy(
            [format_support.code for format_support in formats_croissants(
                FormatSupport.objects.exclude(categorie__iexact='Ecran')
            )],
            ville_active,
            year_start,
            year_end,
            today,
        )
        for category in format_category_stats:
            category_formats = [
                format_stat for format_stat in formats_stats
                if format_stat['categorie'] == category['categorie']
            ]
            category['taux_actuel'] = round(
                sum(
                    annual_face_occupancy.get(format_stat['code'], {}).get(
                        'jours_occupees_cumul', 0
                    )
                    for format_stat in category_formats
                ) / (category['nb_faces'] * elapsed_days) * 100,
                1,
            ) if category['nb_faces'] else 0
            category['taux_annuel'] = round(
                sum(
                    annual_face_occupancy.get(format_stat['code'], {}).get(
                        'jours_occupees_annuel', 0
                    )
                    for format_stat in category_formats
                )
                / (category['nb_faces'] * year_days) * 100,
                1,
            ) if category['nb_faces'] else 0
        for format_stat in formats_stats:
            annual_stats = annual_face_occupancy.get(format_stat['code'], {})
            format_stat['taux_actuel'] = round(
                annual_stats.get('jours_occupees_cumul', 0)
                / (format_stat['nb_faces'] * elapsed_days) * 100,
                1,
            ) if format_stat['nb_faces'] else 0
            format_stat['taux_annuel'] = round(
                annual_stats.get('jours_occupees_annuel', 0)
                / (format_stat['nb_faces'] * year_days) * 100,
                1,
            ) if format_stat['nb_faces'] else 0
        support_groups, campaign_category_groups = self._build_support_and_campaign_groups(
            ville_active, campagnes_actives_list, campagnes_a_venir_list,
            faces_occupees_ids, faces_reservees_ids,
        )
        marches_stats = self._build_market_stats(
            ville_active, faces_occupees_ids, faces_reservees_ids
        )
        screen_spot_stats = self._build_screen_spot_stats(
            ville_active, selected_screen_date
        )
        ecrans_stats_par_resolution = self._build_screen_resolution_stats(
            ville_active, selected_screen_date, screen_spot_stats
        )
        ecran_dashboard_data = self._build_led_dashboard_data(
            ville_active, selected_screen_date, screen_spot_stats
        )
        ecrans_occupees = sum(
            item['nb_occupees'] for item in ecrans_stats_par_resolution
        )
        ecrans_libres = sum(
            item['nb_libres'] for item in ecrans_stats_par_resolution
        )
        ecran_objectifs_total = sum(
            item['objectif_spots_annuel'] for item in ecrans_stats_par_resolution
        )
        ecran_spots_realises_total = sum(
            item['spots_realises'] for item in ecrans_stats_par_resolution
        )
        ecran_secondes_reservees_aujourdhui = sum(
            item['secondes_reservees_aujourdhui']
            for item in screen_spot_stats.values()
        )
        ecran_secondes_disponibles_aujourdhui = sum(
            item['secondes_disponibles_aujourdhui']
            for item in screen_spot_stats.values()
        )
        ecran_secondes_reservees_cumulees = sum(
            item['secondes_reservees_cumulees']
            for item in screen_spot_stats.values()
        )
        ecran_secondes_disponibles_cumulees = sum(
            item['secondes_disponibles_cumulees']
            for item in screen_spot_stats.values()
        )
        ecrans_taux_actuel = round(
            ecran_secondes_reservees_aujourdhui
            / ecran_secondes_disponibles_aujourdhui * 100,
            1,
        ) if ecran_secondes_disponibles_aujourdhui else None
        ecrans_taux_annuel = round(
            ecran_secondes_reservees_cumulees
            / ecran_secondes_disponibles_cumulees * 100,
            1,
        ) if ecran_secondes_disponibles_cumulees else None
        campaign_category_groups_present = [
            group for group in campaign_category_groups if group['nb_supports'] > 0
        ]
        support_groups_present = [
            group for group in support_groups if group['nb_supports'] > 0
        ]
        support_totals_by_kind = {
            kind: {
                'total': sum(
                    group['nb_supports'] for group in support_groups
                    if group['kind'] == kind
                ),
                'bon': sum(
                    group['nb_bon'] for group in support_groups
                    if group['kind'] == kind
                ),
                'maintenance': sum(
                    group['nb_maintenance'] for group in support_groups
                    if group['kind'] == kind
                ),
                'panne': sum(
                    group['nb_panne'] for group in support_groups
                    if group['kind'] == kind
                ),
            }
            for kind in ('statique', 'ecrans', 'marches')
        }
        campaign_ids_by_kind_and_status = {
            kind: {
                Campagne.STATUT_EN_COURS: set(),
                Campagne.STATUT_A_VENIR: set(),
            }
            for kind in ('statique', 'ecrans', 'marches')
        }
        for group in campaign_category_groups:
            kind = group['kind']
            if kind not in campaign_ids_by_kind_and_status:
                continue
            for campaign in group['campagnes']:
                if campaign.statut in campaign_ids_by_kind_and_status[kind]:
                    campaign_ids_by_kind_and_status[kind][campaign.statut].add(campaign.pk)
        campaign_totals_by_kind = {
            kind: {
                'en_cours': len(statuses[Campagne.STATUT_EN_COURS]),
                'a_venir': len(statuses[Campagne.STATUT_A_VENIR]),
            }
            for kind, statuses in campaign_ids_by_kind_and_status.items()
        }

        total_faces = sum(cat['nb_faces'] for cat in parc_categories)
        total_faces_occupe = sum(cat['nb_occupe'] for cat in parc_categories)
        total_faces_reserve = sum(cat['nb_reserve'] for cat in parc_categories)
        total_faces_libre = sum(cat['nb_libre'] for cat in parc_categories)
        total_faces_panne = sum(cat['nb_panne'] for cat in parc_categories)
        parc_global_faces = [
            face for category in parc_categories for face in category['faces']
        ]
        total_faces = len(parc_global_faces)
        total_faces_occupe = sum(
            face['statut'] == 'occupee' for face in parc_global_faces
        )
        total_supports = len({
            face['support_id'] for face in parc_global_faces
        })
        total_supports_occupe = len({
            face['support_id'] for face in parc_global_faces
            if face['statut'] == 'occupee'
        })
        total_faces_reserve = sum(
            face['statut'] == 'reservee' for face in parc_global_faces
        )
        total_faces_libre = sum(
            face['statut'] == 'libre' for face in parc_global_faces
        )
        total_faces_panne = sum(
            face['statut'] == 'panne' for face in parc_global_faces
        )
        jours_realises_total = sum(face['jours'] for face in parc_global_faces)
        jours_secures_total = sum(face['jours_secures'] for face in parc_global_faces)
        month_lengths = [
            calendar.monthrange(today.year, month)[1] for month in range(1, 13)
        ]
        global_formats = {}
        for face in parc_global_faces:
            global_formats.setdefault(face['format_code'], []).append(face)
        parc_global = {
            'categorie': 'Toutes les catégories',
            'nb_faces': total_faces,
            'nb_occupe': total_faces_occupe,
            'nb_reserve': total_faces_reserve,
            'nb_libre': total_faces_libre,
            'nb_panne': total_faces_panne,
            'nb_disponibles': max(total_faces - total_faces_occupe, 0),
            'capacite': total_faces * parc_meta['year_days'],
            'jours_realises': jours_realises_total,
            'jours_secures': jours_secures_total,
            'taux_jour': round(total_faces_occupe / total_faces * 100, 1) if total_faces else 0,
            'taux_cumule': round(
                jours_realises_total / (total_faces * parc_meta['elapsed_days']) * 100,
                1,
            ) if total_faces else 0,
            'annuel_realise': round(
                jours_realises_total / (total_faces * parc_meta['year_days']) * 100,
                1,
            ) if total_faces else 0,
            'annuel_securise': round(
                jours_secures_total / (total_faces * parc_meta['year_days']) * 100,
                1,
            ) if total_faces else 0,
            'libres_60': sum(face['libre_60'] for face in parc_global_faces),
            'faces': parc_global_faces,
            'mensuel': [
                round(
                    sum(face['mois_jours'][index] for face in parc_global_faces)
                    / (total_faces * month_lengths[index]) * 100,
                    1,
                ) if total_faces else 0
                for index in range(12)
            ],
            'formats': [
                {
                    'code': code,
                    'label': face_rows[0]['format'],
                    'nb_faces': len(face_rows),
                    'mensuel': [
                        round(
                            sum(face['mois_jours'][index] for face in face_rows)
                            / (len(face_rows) * month_lengths[index]) * 100,
                            1,
                        )
                        for index in range(12)
                    ],
                }
                for code, face_rows in sorted(
                    global_formats.items(),
                    key=lambda item: (item[1][0]['format'].casefold(), item[0]),
                )
            ],
        }
        total_faces_disponibles = max(total_faces - total_faces_occupe, 0)
        capacite_annuelle_totale = parc_global['capacite']
        taux_jour_global = round(total_faces_occupe / total_faces * 100, 1) if total_faces else 0
        taux_cumule_global = parc_global['taux_cumule']
        annuel_realise_global = parc_global['annuel_realise']
        annuel_securise_global = parc_global['annuel_securise']
        total_faces_libres_60 = sum(cat['libres_60'] for cat in parc_categories)

        category_index = {cat['categorie']: cat for cat in format_category_stats}
        standard = category_index.get('12m²', {})
        geant = category_index.get('Géant', {})
        sucette = category_index.get('Sucette', {})
        marche = category_index.get('Marché', {})

        def pct(o, t):
            return round(o / t * 100) if t else 0

        nb_standard, nbt_std, nbo_std, nbr_std, nbl_std, npt_std = (
            standard.get('nb_supports', 0), standard.get('nb_faces', 0), standard.get('nb_occupe', 0),
            standard.get('nb_reserve', 0), standard.get('nb_libre', 0), standard.get('nb_panne', 0),
        )
        nb_geants, nbt_geo, nbo_geo, nbr_geo, nbl_geo, npt_geo = (
            geant.get('nb_supports', 0), geant.get('nb_faces', 0), geant.get('nb_occupe', 0),
            geant.get('nb_reserve', 0), geant.get('nb_libre', 0), geant.get('nb_panne', 0),
        )
        nb_sucettes, nbt_suc, nbo_suc, nbr_suc, nbl_suc, npt_suc = (
            sucette.get('nb_supports', 0), sucette.get('nb_faces', 0), sucette.get('nb_occupe', 0),
            sucette.get('nb_reserve', 0), sucette.get('nb_libre', 0), sucette.get('nb_panne', 0),
        )
        nb_gm, nbt_gm, nbo_gm, nbr_gm, nbl_gm, npt_gm = (
            marche.get('nb_supports', 0), marche.get('nb_faces', 0), marche.get('nb_occupe', 0),
            marche.get('nb_reserve', 0), marche.get('nb_libre', 0), marche.get('nb_panne', 0),
        )

        format_stats_json = json.dumps(format_category_stats)

        types_stats = [
            {'label': cat['label'], 'count': cat['nb_supports'], 'color': cat['color']}
            for cat in format_category_stats if cat['nb_supports'] > 0
        ]
        if parc['nb_ecrans_bon']:
            types_stats.append({'label': 'Écrans', 'count': parc['nb_ecrans_bon'], 'color': self.COLOR_ECRANS})
        if parc['nb_supports_panne']:
            types_stats.append({'label': 'En panne', 'count': parc['nb_supports_panne'], 'color': self.COLOR_PANNE})
        types_stats_json = json.dumps(types_stats)

        # ── Liste des écrans, un par ligne (occupé / libre aujourd'hui) ──
        screens_qs = EcranNumerique.objects.select_related('support')
        if ville_active:
            screens_qs = screens_qs.filter(support__ville__iexact=ville_active)
        screens_qs = screens_qs.order_by('support__ville', 'support__quartier', 'support__code')

        # Même règle que _build_screen_resolution_stats : campagne en cours à la date du jour
        ecrans_occupes_ids = set(
            LigneCampagne.objects.filter(
                support_id__isnull=False,
            ).filter(
                Q(
                    campagne__statut=Campagne.STATUT_EN_COURS,
                    campagne__date_debut__lte=selected_screen_date,
                    campagne__date_fin__gte=selected_screen_date,
                )
                | Q(
                    campagne__statut=Campagne.STATUT_EN_PAUSE,
                    campagne__date_debut__lte=selected_screen_date,
                    campagne__date_mise_en_pause__lte=selected_screen_date,
                )
            ).values_list('support_id', flat=True)
        )

        ecrans_liste = [
            {
                'nom': e.support.nom,
                'code': e.support.code,
                'uuid': e.support.uuid,
                'ville': e.support.ville,
                'quartier': e.support.quartier,
                'resolution': e.get_resolution_display(),
                'occupe': e.support_id in ecrans_occupes_ids,
                **screen_spot_stats.get(e.support_id, {}),
            }
            for e in screens_qs
        ]
        dashboard_screen_ids = {
            str(screen['id'])
            for screen in ecran_dashboard_data['ecran_dashboard_screens']
        }
        selected_screen_id = self.request.GET.get('ecran_id', '')
        if selected_screen_id not in dashboard_screen_ids:
            selected_screen_id = ''
        selected_screen = next(
            (
                screen for screen in screens_qs
                if str(screen.support_id) == selected_screen_id
            ),
            None,
        )
        selected_bilan = None
        exploitation_form = None
        bilan_form = None
        if selected_screen:
            selected_bilan = EcranBilanMensuel.objects.filter(
                ecran=selected_screen,
                annee=selected_screen_year,
                mois=selected_screen_date.month,
            ).first()
            exploitation_form = EcranParametresExploitationForm(
                instance=(
                    EcranParametresExploitation.objects.filter(
                        ecran=selected_screen
                    ).first()
                )
            )
            bilan_form = EcranBilanMensuelForm(instance=selected_bilan)

        package_contrat_id = self.request.GET.get('package_contrat_id', '')
        package_instance = None
        if package_contrat_id.isdecimal():
            package_instance = PackageEcran.objects.filter(
                contrat_id=int(package_contrat_id)
            ).first()
        package_form = PackageEcranForm(instance=package_instance)
        if package_instance is None and package_contrat_id.isdecimal():
            package_form = PackageEcranForm(
                initial={'contrat': int(package_contrat_id)}
            )

        context.update({
            'ville_active': ville_active,
            'villes': villes,
            'ecrans_liste': ecrans_liste,
            'ecran_dashboard_data': ecran_dashboard_data,
            'ecran_annee_selectionnee': selected_screen_year,
            'ecran_date_selectionnee': selected_screen_date,
            'ecran_id_selectionne': selected_screen_id,
            'ecran_form_exploitation': exploitation_form,
            'ecran_form_bilan': bilan_form,
            'ecran_form_package': package_form,
            'ecran_can_edit': bool(
                getattr(self.request.user, 'is_staff_regie_role', False)
            ),
            'ecran_annee_options': sorted(
                set(range(current_date.year - 2, current_date.year + 3))
                | {selected_screen_year}
            ),

            'total_supports': parc['total'],
            'total_campagnes': campagnes_qs.count(),
            'total_villes': len(villes),
            'total_formats': len(formats_stats),
            'support_groups': support_groups,
            'support_groups_present': support_groups_present,
            'panneaux_total': support_totals_by_kind['statique']['total'],
            'panneaux_bon_etat': support_totals_by_kind['statique']['bon'],
            'ecrans_total': support_totals_by_kind['ecrans']['total'],
            'ecrans_bon_etat': support_totals_by_kind['ecrans']['bon'],
            'ecrans_maintenance': support_totals_by_kind['ecrans']['maintenance'],
            'ecrans_panne': support_totals_by_kind['ecrans']['panne'],
            'ecrans_occupees': ecrans_occupees,
            'ecrans_libres': ecrans_libres,
            'ecrans_objectifs_total': ecran_objectifs_total,
            'ecrans_taux_actuel': ecrans_taux_actuel,
            'ecrans_taux_annuel': ecrans_taux_annuel,
            'ecrans_stats_par_resolution': ecrans_stats_par_resolution,
            'marches_total': len(marches_stats),
            'marches_bon_etat': support_totals_by_kind['marches']['bon'],
            'marches_stats': marches_stats,
            'panneaux_campagnes_en_cours': campaign_totals_by_kind['statique']['en_cours'],
            'panneaux_campagnes_a_venir': campaign_totals_by_kind['statique']['a_venir'],
            'ecrans_campagnes_en_cours': campaign_totals_by_kind['ecrans']['en_cours'],
            'ecrans_campagnes_a_venir': campaign_totals_by_kind['ecrans']['a_venir'],
            'marches_campagnes_en_cours': campaign_totals_by_kind['marches']['en_cours'],
            'marches_campagnes_a_venir': campaign_totals_by_kind['marches']['a_venir'],
            'has_static_support_groups': any(
                group['kind'] == 'statique' for group in support_groups_present
            ),
            'has_other_support_groups': any(
                group['kind'] != 'statique' for group in support_groups_present
            ),
            'campaign_category_groups': campaign_category_groups,
            'campaign_category_groups_present': campaign_category_groups_present,
            'supports_bon': parc['nb_supports_bon'],
            'supports_panne': parc['nb_supports_panne'],
            'total_clients': clients_qs.count(),
            'campagnes_actives': len(campagnes_actives_list),
            'campagnes_a_venir': len(campagnes_a_venir_list),
            'campagnes_recentes': campagnes_qs[:20],
            'alertes': get_cached_alertes(),

            'clients_actifs_list': clients_qs.order_by('nom')[:8],
            'campagnes_en_cours_list': sorted(campagnes_actives_list, key=lambda c: c.date_debut, reverse=True)[:6],
            'campagnes_a_venir_list': sorted(campagnes_a_venir_list, key=lambda c: c.date_debut)[:6],

            'nb_panneaux': parc['nb_panneaux'],
            'nb_panneaux_bon': parc['nb_panneaux_bon'],
            'nb_ecrans_bon': parc['nb_ecrans_bon'],
            'nb_ecrans_panne': parc['nb_ecrans_panne'],
            'nb_ecrans': parc['nb_ecrans'],
            'format_categories': format_category_stats,
            'formats_stats': formats_stats,
            'parc_categories': parc_categories,
            'parc_global': parc_global,
            'parc_meta': parc_meta,
            'annee_taux_occupation': today.year,
            'total_faces': total_faces,
            'total_supports': total_supports,
            'total_faces_occupe': total_faces_occupe,
            'total_supports_occupe': total_supports_occupe,
            'total_faces_reserve': total_faces_reserve,
            'total_faces_libre': total_faces_libre,
            'total_faces_panne': total_faces_panne,
            'total_faces_disponibles': total_faces_disponibles,
            'taux_jour_global': taux_jour_global,
            'taux_cumule_global': taux_cumule_global,
            'annuel_realise_global': annuel_realise_global,
            'annuel_securise_global': annuel_securise_global,
            'capacite_annuelle_totale': capacite_annuelle_totale,
            'total_faces_libres_60': total_faces_libres_60,

            'revenu_actif': revenu_actif,
            'revenu_a_venir': revenu_a_venir,
            'revenu_meres': revenu_meres,
            'campagnes_meres_count': campagnes_meres_count,
            'top_campaigns_json': top_campaigns_json,
            'top_faces_json': top_faces_json,
            'nb_standard':nb_standard, 'nb_geants':nb_geants, 'nb_sucettes':nb_sucettes, 'nb_gm':nb_gm,

            'nbl_std': nbl_std, 'nbo_std': nbo_std, 'nbr_std': nbr_std, 'npt_std': npt_std, 'nbt_std': nbt_std, 'o_std': pct(nbo_std, nbt_std),
            'nbl_geo': nbl_geo, 'nbo_geo': nbo_geo, 'nbr_geo': nbr_geo, 'npt_geo': npt_geo, 'nbt_geo': nbt_geo, 'o_geo': pct(nbo_geo, nbt_geo),
            'nbl_suc': nbl_suc, 'nbo_suc': nbo_suc, 'nbr_suc': nbr_suc, 'npt_suc': npt_suc, 'nbt_suc': nbt_suc, 'o_suc': pct(nbo_suc, nbt_suc),
            'nbl_gm': nbl_gm, 'nbo_gm': nbo_gm, 'nbr_gm': nbr_gm, 'npt_gm': npt_gm, 'nbt_gm': nbt_gm, 'o_gm': pct(nbo_gm, nbt_gm),

            'format_stats_json': format_stats_json,
            'types_stats_json': types_stats_json,
        })
        return context

    def _build_format_stats(self, ville_active, faces_occupees_ids, faces_reservees_ids):
        """Construit les indicateurs pour chaque format individuel du parc."""
        formats = formats_croissants(
            FormatSupport.objects.exclude(categorie__iexact='Ecran')
        )
        if not formats:
            return []

        format_codes = [format_support.code for format_support in formats]
        supports_qs = Support.objects.filter(
            type_support=Support.TYPE_PANNEAU,
            format__in=format_codes,
        )
        faces_qs = FacePanneau.objects.filter(support__format__in=format_codes)
        if ville_active:
            supports_qs = supports_qs.filter(ville__iexact=ville_active)
            faces_qs = faces_qs.filter(support__ville__iexact=ville_active)

        supports_by_format = {
            row['format']: row
            for row in supports_qs.values('format').annotate(
                total=Count('id'),
                bon=Count('id', filter=Q(etat__in=Support.ETATS_OPERATIONNELS)),
                maintenance=Count('id', filter=Q(etat=Support.ETAT_A_MAINTENIR)),
                panne=Count('id', filter=Q(etat=Support.ETAT_PANNE)),
            )
        }

        # Une photo représentative par format (1 requête)
        photo_by_format = {}
        photo_rows = (
            supports_qs
            .exclude(Q(photo_principale='') | Q(photo_principale__isnull=True))
            .order_by('code')
            .values_list('format', 'photo_principale')
        )
        for fmt, photo_name in photo_rows:
            if fmt not in photo_by_format and photo_name:
                photo_by_format[fmt] = default_storage.url(photo_name)

        faces_by_format = {}
        for face_id, format_code, etat in faces_qs.values_list('id', 'support__format', 'etat'):
            counts = faces_by_format.setdefault(format_code, {
                'total': 0, 'bon': 0, 'panne': 0, 'occupe': 0, 'reserve': 0,
            })
            counts['total'] += 1
            if etat == Support.ETAT_PANNE:
                counts['panne'] += 1
            elif etat in Support.ETATS_OPERATIONNELS:
                counts['bon'] += 1
                if face_id in faces_occupees_ids:
                    counts['occupe'] += 1
                elif face_id in faces_reservees_ids:
                    counts['reserve'] += 1

        stats = []
        for format_support in formats:
            support_counts = supports_by_format.get(format_support.code, {})
            if not support_counts.get('total'):
                continue
            face_counts = faces_by_format.get(format_support.code, {})
            nb_faces = face_counts.get('total', 0)
            nb_faces_bon = face_counts.get('bon', 0)
            nb_occupe = face_counts.get('occupe', 0)
            stats.append({
                'code': format_support.code,
                'categorie': format_support.categorie or 'Sans catégorie',
                'dimensions': format_support.dimensions,
                'superficie': format_support.superficie,
                'photo': photo_by_format.get(format_support.code, ''),
                'nb_supports': support_counts.get('total', 0),
                'nb_supports_bon': support_counts.get('bon', 0),
                'nb_supports_maintenance': support_counts.get('maintenance', 0),
                'nb_supports_panne': support_counts.get('panne', 0),
                'nb_faces': nb_faces,
                'nb_faces_libres': max(
                    0,
                    nb_faces_bon - nb_occupe - face_counts.get('reserve', 0),
                ),
                'nb_faces_occupees': nb_occupe,
                'nb_faces_reservees': face_counts.get('reserve', 0),
                'nb_faces_panne': face_counts.get('panne', 0),
                'taux': round(nb_occupe / nb_faces * 100) if nb_faces else 0,
            })
        formats_by_code = {format_support.code: format_support for format_support in formats}
        stats.sort(key=lambda stat: (
            stat['categorie'].casefold(),
            format_sort_key(formats_by_code.get(stat['code']) or stat['code']),
        ))
        return stats

    def _build_format_category_stats(self, format_categories_codes, ville_active, faces_occupees_ids, faces_reservees_ids):
        """Calcule les stats par catégorie de format en 3 requêtes fixes,
        quel que soit le nombre de catégories (au lieu de 5 requêtes x N catégories)."""
        all_codes = [code for codes in format_categories_codes.values() for code in codes]
        if not all_codes:
            return []

        faces_qs = FacePanneau.objects.filter(support__format__in=all_codes)
        if ville_active:
            faces_qs = faces_qs.filter(support__ville__iexact=ville_active)

        # 1) total / panne par format
        par_format = faces_qs.values('support__format').annotate(
            total=Count('id'),
            panne=Count('id', filter=Q(etat=Support.ETAT_PANNE)),
        )
        stats_par_format = {row['support__format']: row for row in par_format}

        # 2) faces en bon état + leur format (pour dériver occupé/réservé/libre en mémoire)
        faces_bon = faces_qs.filter(
            etat__in=Support.ETATS_OPERATIONNELS,
        ).values_list('id', 'support__format')
        bon_par_format = {}
        for face_id, fmt in faces_bon:
            bon_par_format.setdefault(fmt, []).append(face_id)

        # 3) nb de supports en bon état par format
        supports_qs = Support.objects.filter(
            type_support=Support.TYPE_PANNEAU,
            format__in=all_codes,
            etat__in=Support.ETATS_OPERATIONNELS,
        )
        if ville_active:
            supports_qs = supports_qs.filter(ville__iexact=ville_active)
        supports_par_format = dict(supports_qs.values('format').annotate(n=Count('id')).values_list('format', 'n'))

        stats = []
        for categorie, codes in format_categories_codes.items():
            total_faces = sum(stats_par_format.get(code, {}).get('total', 0) for code in codes)
            if total_faces == 0:
                continue
            nb_panne = sum(stats_par_format.get(code, {}).get('panne', 0) for code in codes)
            nb_supports = sum(supports_par_format.get(code, 0) for code in codes)

            faces_ids_bon = [fid for code in codes for fid in bon_par_format.get(code, [])]
            nb_bon = len(faces_ids_bon)
            nb_occupe = sum(1 for fid in faces_ids_bon if fid in faces_occupees_ids)
            nb_reserve = sum(1 for fid in faces_ids_bon if fid in faces_reservees_ids and fid not in faces_occupees_ids)
            nb_libre = nb_bon - nb_occupe - nb_reserve

            stats.append({
                'categorie': categorie,
                'label': categorie,
                'detail_code': next(
                    (
                        code for code in codes
                        if stats_par_format.get(code, {}).get('total', 0)
                    ),
                    '',
                ),
                'slug': slugify(categorie),
                'nb_supports': nb_supports,
                'nb_faces': total_faces,
                'nb_occupe': nb_occupe,
                'nb_reserve': nb_reserve,
                'nb_libre': nb_libre,
                'nb_panne': nb_panne,
                'panne_pct': round(nb_panne / total_faces * 100) if total_faces else 0,
                'color': self.CATEGORY_COLORS.get(categorie, self.DEFAULT_CATEGORY_COLOR),
            })
        return stats

def _faces_occupees_et_reservees(today):
    """Mêmes règles que DashboardView.get_context_data."""
    occupees = set(
        LigneCampagne.objects.filter(
            Q(
                campagne__statut__in=['en_cours', 'a_venir'],
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
            )
            | Q(
                campagne__statut='en_pause',
                campagne__date_debut__lte=today,
                campagne__date_mise_en_pause__lte=today,
            )
        ).values_list('face_id', flat=True)
    )
    reservees = set(
        ReservationLigne.objects.filter(
            reservation__date_fin__gte=today,
            reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
        ).values_list('face_id', flat=True)
    )
    return occupees, reservees


class FormatDetailView(LoginRequiredMixin, TemplateView):
    """Détail d'un format : carte, liste des panneaux et statistiques."""
    template_name = 'campaigns/format_detail.html'

    STATUT_LABELS = {
        'libre': 'Libre',
        'partiel': 'Partiel',
        'occupe': 'Occupé',
        'panne': 'Hors service',
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        code = self.kwargs['code']
        ville_active = self.request.GET.get('ville', '').strip()
        if code == 'ecrans':
            return self._get_screen_context(context, ville_active, today)

        tous_geants = code == 'geant'
        marche_mode = code == 'marche'
        marches = []
        if marche_mode:
            format_support = FormatSupport.objects.filter(code=code).first() or {
                'categorie': 'Marché',
                'dimensions': 'Marché',
                'code': code,
            }
            marches_qs = Marche.objects.order_by('nom')
            if ville_active:
                marches_qs = marches_qs.filter(ville__iexact=ville_active)
            marche_objects = list(marches_qs)
            panneaux_par_marche = dict(
                Support.objects.filter(
                    type_support=Support.TYPE_PANNEAU,
                    emplacement__marche_id__in=[
                        marche.pk for marche in marche_objects
                    ],
                ).values('emplacement__marche_id')
                .annotate(nombre=Count('pk'))
                .values_list('emplacement__marche_id', 'nombre')
            )
            marches = [
                {
                    'nom': marche.nom,
                    'ville': marche.ville or '',
                    'quartier': marche.quartier or '',
                    'adresse': marche.adresse or '',
                    'lat': float(marche.latitude) if marche.latitude is not None else None,
                    'lng': float(marche.longitude) if marche.longitude is not None else None,
                    'rayon_metres': marche.rayon_metres,
                    'nb_panneaux': panneaux_par_marche.get(marche.pk, 0),
                    'url': reverse('marche_detail', args=[marche.pk]),
                }
                for marche in marche_objects
            ]
            marche_ids = [marche.pk for marche in marche_objects]
            supports_qs = Support.objects.filter(
                type_support=Support.TYPE_PANNEAU,
                emplacement__marche_id__in=marche_ids,
            ).select_related('emplacement__marche')
            format_by_code = {
                format_obj.code: format_obj
                for format_obj in FormatSupport.objects.filter(
                    code__in=supports_qs.values_list('format', flat=True)
                )
            }
        elif tous_geants:
            formats = formats_croissants(
                FormatSupport.objects.filter(categorie='Géant')
            )
            format_support = {'categorie': 'Géant', 'dimensions': '', 'code': 'geant'}
            supports_qs = Support.objects.filter(
                type_support=Support.TYPE_PANNEAU,
                format__in=[format_obj.code for format_obj in formats],
            )
            format_by_code = {format_obj.code: format_obj for format_obj in formats}
        else:
            format_support = get_object_or_404(FormatSupport, code=code)
            supports_qs = Support.objects.filter(
                type_support=Support.TYPE_PANNEAU, format=code,
            )
            format_by_code = {format_support.code: format_support}
        if ville_active and not marche_mode:
            supports_qs = supports_qs.filter(ville__iexact=ville_active)
        supports_list = list(supports_qs.order_by('code'))
        pige_images_by_face = {}
        current_pige_files = PigeFichier.objects.filter(
            ligne__face__support_id__in=[support.pk for support in supports_list],
            ligne__campagne__statut=Campagne.STATUT_EN_COURS,
            ligne__campagne__date_debut__lte=today,
            ligne__campagne__date_fin__gte=today,
        ).order_by(
            'ligne__face_id', '-date_prise', '-ajoute_le', '-pk',
        ).values_list('ligne__face_id', 'drive_file_id')
        for face_id, drive_file_id in current_pige_files:
            pige_images_by_face.setdefault(
                face_id,
                f'https://drive.google.com/thumbnail?id={drive_file_id}&sz=w1000',
            )

        faces_by_support = {}
        for face in FacePanneau.objects.filter(support__in=supports_list):
            faces_by_support.setdefault(face.support_id, []).append(face)

        occupees, reservees = _faces_occupees_et_reservees(today)

        panneaux = []
        quartiers = {}
        support_ids_by_quartier = {}
        tot_faces = tot_occ = tot_libre = 0

        for numero, support in enumerate(supports_list, start=1):
            faces = faces_by_support.get(support.pk, [])
            bonnes = [f for f in faces if f.etat in Support.ETATS_OPERATIONNELS]
            nb_occ = sum(1 for f in bonnes if f.pk in occupees)
            nb_res = sum(1 for f in bonnes if f.pk in reservees and f.pk not in occupees)
            nb_libre = len(bonnes) - nb_occ - nb_res

            if support.etat == Support.ETAT_PANNE or (faces and not bonnes):
                statut = 'panne'
            elif bonnes and nb_libre == 0:
                statut = 'occupe'
            elif nb_occ + nb_res > 0:
                statut = 'partiel'
            else:
                statut = 'libre'

            photo = ''
            if support.photo_principale:
                photo = support.photo_principale.url
            else:
                for f in faces:
                    if f.photo:
                        photo = f.photo.url
                        break

            lat = float(support.latitude) if support.latitude else None
            lng = float(support.longitude) if support.longitude else None
            support_format = format_by_code.get(support.format)
            emplacement_marche = (
                support.emplacement
                if marche_mode and support.emplacement_id
                else None
            )
            face_cards = []
            for face in faces:
                if support.etat == Support.ETAT_PANNE or face.etat == Support.ETAT_PANNE:
                    face_statut = 'panne'
                elif face.etat == Support.ETAT_A_MAINTENIR:
                    face_statut = 'a_maintenir'
                elif face.pk in occupees:
                    face_statut = 'occupe'
                elif face.pk in reservees:
                    face_statut = 'reserve'
                else:
                    face_statut = 'libre'
                face_cards.append({
                    'id': face.pk,
                    'label': face.label,
                    'image': pige_images_by_face.get(face.pk, ''),
                    'statut': face_statut,
                    'statut_label': {
                        'libre': 'Libre',
                        'reserve': 'Réservée',
                        'occupe': 'Occupée',
                        'a_maintenir': 'À maintenir',
                        'panne': 'En panne',
                    }[face_statut],
                    'taux_actuel': 0.0,
                    'taux_annuel': 0.0,
                })

            panneaux.append({
                'numero': numero,
                'code': support.code,
                'nom': support.nom,
                'format': support_format.dimensions if support_format else support.format,
                'ville': support.ville or '',
                'quartier': support.quartier or '',
                'adresse': support.adresse or support.quartier or support.ville or '',
                'photo': photo,
                'pige_images': [
                    {
                        'url': face_card['image'],
                        'face_label': face_card['label'],
                    }
                    for face_card in face_cards
                    if face_card['image']
                ],
                'nb_faces': len(faces),
                'nb_occupees': nb_occ,
                'nb_libres': nb_libre,
                'faces': face_cards,
                'statut': statut,
                'statut_label': self.STATUT_LABELS[statut],
                'lat': lat,
                'lng': lng,
                'url': reverse('support_detail', args=[support.uuid]),
                'marche_nom': emplacement_marche.marche.nom if emplacement_marche else '',
                'emplacement_code': emplacement_marche.code if emplacement_marche else '',
            })

            tot_faces += len(faces)
            tot_occ += nb_occ
            tot_libre += nb_libre

            quartier_nom = support.quartier or 'Non renseigné'
            q = quartiers.setdefault(quartier_nom, {
                'nom': quartier_nom,
                'nb_panneaux': 0, 'nb_faces': 0, 'nb_occupees': 0, 'nb_libres': 0,
            })
            support_ids_by_quartier.setdefault(quartier_nom, []).append(support.pk)
            q['nb_panneaux'] += 1
            q['nb_faces'] += len(faces)
            q['nb_occupees'] += nb_occ
            q['nb_libres'] += nb_libre

        stats_quartiers = sorted(
            quartiers.values(), key=lambda q: (-q['nb_panneaux'], q['nom'])
        )
        year_start = date(today.year, 1, 1)
        year_end = date(today.year, 12, 31)
        year_days = (year_end - year_start).days + 1
        elapsed_days = (today - year_start).days + 1
        occupancy_by_format = DashboardView._build_annual_face_occupancy(
            list({support.format for support in supports_list if support.format}),
            ville_active,
            year_start,
            year_end,
            today,
            support_ids=[support.pk for support in supports_list],
            include_face_rates=True,
        )
        format_by_support_code = {
            support.code: support.format for support in supports_list
        }
        for panneau in panneaux:
            format_stats = occupancy_by_format.get(
                format_by_support_code[panneau['code']],
                {},
            )
            rates_by_face = format_stats.get('taux_par_face', {})
            for face in panneau['faces']:
                rates = rates_by_face.get(face['id'], {})
                face['taux_actuel'] = rates.get('taux_actuel', 0.0)
                face['taux_annuel'] = rates.get('taux_annuel', 0.0)
        occupied_days_current = sum(
            stats['jours_occupees_cumul']
            for stats in occupancy_by_format.values()
        )
        occupied_days_annual = sum(
            stats['jours_occupees_annuel']
            for stats in occupancy_by_format.values()
        )
        for q in stats_quartiers:
            quartier_support_ids = support_ids_by_quartier[q['nom']]
            quartier_formats = list({
                support.format
                for support in supports_list
                if support.pk in quartier_support_ids and support.format
            })
            quartier_occupancy = DashboardView._build_annual_face_occupancy(
                quartier_formats,
                ville_active,
                year_start,
                year_end,
                today,
                support_ids=quartier_support_ids,
            )
            q['taux_actuel'] = round(
                sum(
                    stats['jours_occupees_cumul']
                    for stats in quartier_occupancy.values()
                ) / (q['nb_faces'] * elapsed_days) * 100,
                1,
            ) if q['nb_faces'] else 0
            q['taux_annuel'] = round(
                sum(
                    stats['jours_occupees_annuel']
                    for stats in quartier_occupancy.values()
                ) / (q['nb_faces'] * year_days) * 100,
                1,
            ) if q['nb_faces'] else 0

        context.update({
            'format': format_support,
            'tous_geants': tous_geants,
            'marche_mode': marche_mode,
            'marches': marches,
            'ville_active': ville_active,
            'panneaux': panneaux,
            'nb_panneaux': len(panneaux),
            'nb_faces': tot_faces,
            'nb_occupees': tot_occ,
            'nb_libres': tot_libre,
            'taux': round(tot_occ / tot_faces * 100) if tot_faces else 0,
            'taux_actuel': round(
                occupied_days_current / (tot_faces * elapsed_days) * 100, 1
            ) if tot_faces else 0,
            'taux_annuel': round(
                occupied_days_annual / (tot_faces * year_days) * 100, 1
            ) if tot_faces else 0,
            'annee_taux_occupation': today.year,
            'stats_quartiers': stats_quartiers,
            'nb_libre_panneaux': sum(1 for p in panneaux if p['statut'] == 'libre'),
            'nb_partiel_panneaux': sum(1 for p in panneaux if p['statut'] == 'partiel'),
            'nb_occupe_panneaux': sum(1 for p in panneaux if p['statut'] == 'occupe'),
            'nb_panne_panneaux': sum(1 for p in panneaux if p['statut'] == 'panne'),
        })
        return context

    def _get_screen_context(self, context, ville_active, today):
        screens_qs = Support.objects.filter(
            type_support=Support.TYPE_ECRAN
        ).select_related('ecran_info').order_by('ville', 'quartier', 'code')
        if ville_active:
            screens_qs = screens_qs.filter(ville__iexact=ville_active)

        screen_supports = list(screens_qs)
        occupied_ids = set(
            LigneCampagne.objects.filter(
                support_id__in=[support.pk for support in screen_supports],
                campagne__statut=Campagne.STATUT_EN_COURS,
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
            ).values_list('support_id', flat=True)
        )
        screens = []
        resolutions = {}
        for number, support in enumerate(screen_supports, start=1):
            try:
                resolution = support.ecran_info.get_resolution_display()
            except EcranNumerique.DoesNotExist:
                resolution = 'Non renseignée'
            occupied = support.pk in occupied_ids
            resolutions[resolution] = resolutions.get(resolution, 0) + 1
            screens.append({
                'numero': number,
                'nom': support.nom,
                'code': support.code,
                'ville': support.ville or '',
                'quartier': support.quartier or '',
                'resolution': resolution,
                'etat': support.get_etat_display(),
                'occupe': occupied,
                'lat': float(support.latitude) if support.latitude is not None else None,
                'lng': float(support.longitude) if support.longitude is not None else None,
                'url': reverse('support_detail', args=[support.uuid]),
            })

        context.update({
            'ecrans_mode': True,
            'ville_active': ville_active,
            'ecrans': screens,
            'nb_ecrans': len(screens),
            'nb_ecrans_occupees': len(occupied_ids),
            'nb_ecrans_libres': len(screens) - len(occupied_ids),
            'resolutions': [
                {'nom': name, 'nombre': count}
                for name, count in sorted(resolutions.items())
            ],
        })
        return context


def _build_alertes():
    alertes = []
    today = timezone.now().date()
    fin_dans_7j = today + timezone.timedelta(days=2)

    supports_panne = Support.objects.filter(etat='panne').only('code', 'pk')
    for s in supports_panne:
        alertes.append({
            'id': f'support-panne-{s.pk}',
            'type': 'danger',
            'msg': f'Support {s.code} en panne',
            'url': reverse('support_detail', args=[s.uuid])
        })

    deletion_requests = SupportDeletionRequest.objects.filter(
        status=SupportDeletionRequest.STATUS_PENDING,
        support__isnull=False,
    ).select_related('support')
    for deletion_request in deletion_requests:
        if not deletion_request.support:
            continue
        alertes.append({
            'id': f'support-deletion-{deletion_request.pk}',
            'type': 'warning',
            'msg': (
                f'Demande de suppression du support {deletion_request.support_code} '
                f'par {deletion_request.requester_name} — approbation requise'
            ),
            'url': reverse('support_deletion_requests'),
            'staff_only': True,
            'exclude_user_id': deletion_request.requested_by_id,
        })

    bientot = Campagne.objects.filter(
        statut='en_cours',
        date_fin__range=(today, fin_dans_7j)
    ).only('nom', 'date_fin', 'pk')

    for c in bientot:
        jours = (c.date_fin - today).days
        msg = f'Campagne "{c.nom}" se termine ' + (f"dans {jours}j" if jours > 0 else "aujourd'hui")
        alertes.append({
            'id': f'campagne-fin-{c.pk}',
            'type': 'warning',
            'msg': msg,
            'url': reverse('campagne_detail', args=[c.pk])
        })

    campagnes_commerciales = Campagne.objects.filter(
        statut__in=[Campagne.STATUT_EN_COURS, Campagne.STATUT_A_VENIR],
        est_mere=False,
    ).select_related('contrat', 'campagne_parente__contrat').only(
        'pk',
        'nom',
        'contrat_id',
        'bon_commande_en_attente',
        'campagne_parente_id',
        'contrat__id',
        'contrat__type_document',
        'contrat__document',
        'campagne_parente__id',
        'campagne_parente__contrat_id',
        'campagne_parente__contrat__id',
        'campagne_parente__contrat__type_document',
        'campagne_parente__contrat__document',
    )
    for campagne in campagnes_commerciales:
        if campagne.bon_commande_en_attente:
            alertes.append({
                'id': f'bon-commande-attente-{campagne.pk}',
                'type': 'warning',
                'msg': f'Bon de commande en attente pour la campagne « {campagne.nom} »',
                'url': reverse('campagne_detail', args=[campagne.pk]),
            })
            continue

        contrat = campagne.contrat or (
            campagne.campagne_parente.contrat if campagne.campagne_parente_id else None
        )
        if contrat is None:
            alertes.append({
                'id': f'campagne-sans-document-{campagne.pk}',
                'type': 'danger',
                'msg': f'Aucun contrat ni bon de commande associé à la campagne « {campagne.nom} »',
                'url': reverse('campagne_detail', args=[campagne.pk]),
            })
        elif contrat.type_document == 'bon_commande' and not contrat.document:
            alertes.append({
                'id': f'bon-commande-pdf-manquant-{contrat.pk}-{campagne.pk}',
                'type': 'warning',
                'msg': f'Le PDF du bon de commande {contrat.reference} manque pour « {campagne.nom} »',
                'url': reverse('contrat_detail', args=[contrat.pk]),
            })
    return alertes


def get_cached_alertes():
    res = cache.get('dashboard_alertes')
    if not res:
        res = _build_alertes()
        cache.set('dashboard_alertes', res, 60)
    return res

# ── Clients ──────────────────────────────────────────────────────────────────

class ClientListView(StaffRequiredMixin, SortableListMixin, ListView):
    model = Client
    template_name = 'campaigns/client_list.html'
    context_object_name = 'clients'
    paginate_by = 10
    SORT_FIELDS = {
        'reference': 'reference',
        'nom': 'nom',
        'contact': 'contact_nom',
        'statut': 'actif',
        'campagnes': 'cp_total',
    }
    DEFAULT_SORT = 'nom'
    DEFAULT_DIR = 'asc'

    def get_queryset(self):
        qs = Client.objects.annotate(
            cp_panneau=Count('campagnes', filter=Q(campagnes__statut='en_cours', campagnes__type_support='panneau', campagnes__actif=True)),
            cp_ecran=Count('campagnes', filter=Q(campagnes__statut='en_cours', campagnes__type_support='ecran', campagnes__actif=True)),
            cp_total=Count('campagnes', filter=Q(campagnes__statut='en_cours', campagnes__actif=True)),
        )
        
        # Restriction rôle non-admin
        if not self.request.user.is_admin:
            qs = qs.filter(actif=True)

        # Recherche textuelle
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(nom__icontains=q) |
                Q(contact_nom__icontains=q) |
                Q(reference__icontains=q) |
                Q(telephone__icontains=q) |
                Q(email__icontains=q)
            )

        # Filtre statut
        status = self.request.GET.get('status', '').strip()
        if status == 'actif':
            qs = qs.filter(actif=True)
        elif status == 'inactif':
            qs = qs.filter(actif=False)

        qs = self.apply_sort(qs)
        return qs.prefetch_related('campagnes')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Calcul des stats par client
        clients_list = list(context['clients'])
        total_spots_led_actifs = 0
        total_faces_statiques_actives = 0
        total_campagnes_actives = 0

        for client in clients_list:
            campagnes_actives = [c for c in client.campagnes.all() if c.statut == 'en_cours' and c.actif]
            client.campagnes_stats = {
                'panneau': client.cp_panneau,
                'ecran': client.cp_ecran,
                'total': client.cp_total,
            }
            # Spots LED pour écrans, et faces pour panneaux (unités distinctes non additionnables)
            client.spots_stats = {
                'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support and c.type_support != 'ecran'),
                'ecran': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'ecran'),
            }
            total_spots_led_actifs += client.spots_stats['ecran']
            total_faces_statiques_actives += client.spots_stats['panneau']
            total_campagnes_actives += client.campagnes_stats['total']

        context['clients'] = clients_list

        # Métriques globales portefeuille
        base_clients = Client.objects.all()
        if not self.request.user.is_admin:
            base_clients = base_clients.filter(actif=True)

        context['kpi_total_clients'] = base_clients.count()
        context['kpi_actifs_count'] = base_clients.filter(actif=True).count()
        context['kpi_inactifs_count'] = base_clients.filter(actif=False).count()
        context['kpi_campagnes_actives'] = total_campagnes_actives
        context['kpi_spots_led_actifs'] = total_spots_led_actifs
        context['kpi_faces_statiques_actives'] = total_faces_statiques_actives

        context['q'] = self.request.GET.get('q', '').strip()
        context['status_filter'] = self.request.GET.get('status', '').strip()
        context['title'] = 'Portefeuille Clients'
        return context


class ClientCreateView(StaffRequiredMixin, CreateView):
    model = Client
    form_class = ClientForm
    template_name = 'campaigns/client_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request, AuditLog.ACTION_CREATE, 'commercial', obj=self.object, detail=f"Création client: {self.object.nom}")
        messages.success(self.request, f'Client "{self.object.nom}" créé.')
        return response

    def get_success_url(self):
        return reverse('client_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Nouveau client'
        return context


class ClientDetailView(ClientStaffRequiredMixin, DetailView):
    model = Client
    template_name = 'campaigns/client_detail.html'
    context_object_name = 'client'

    def get_object(self, queryset=None):
        return super().get_object(queryset=queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object
        today = timezone.now().date()

        # Tous les contrats et campagnes du client
        if self.request.user.is_admin:
            contrats_qs = client.contrats.all().order_by('-date_debut')
            campagnes_all_qs = client.campagnes.all().select_related('contrat', 'campagne_parente').order_by('-date_debut')
        else:
            contrats_qs = client.contrats.filter(archive=False).order_by('-date_debut')
            if not contrats_qs.exists():
                contrats_qs = client.contrats.all().order_by('-date_debut')
            campagnes_all_qs = client.campagnes.filter(actif=True).select_related('contrat', 'campagne_parente').order_by('-date_debut')

        # Calculer les métriques globales actives
        campagnes_actives = [c for c in campagnes_all_qs if c.statut == 'en_cours' and (self.request.user.is_admin or c.actif)]
        client.campagnes_stats = {
            'panneau': len([c for c in campagnes_actives if c.type_support and c.type_support != 'ecran']),
            'ecran': len([c for c in campagnes_actives if c.type_support == 'ecran']),
            'marche': len([c for c in campagnes_actives if c.type_support == 'marche']),
            'total': len(campagnes_actives),
        }
        client.spots_stats = {
            'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support and c.type_support != 'ecran'),
            'ecran': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'ecran'),
            'marche': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'marche'),
            'total': sum(c.calculer_nombre_spots() for c in campagnes_actives),
        }

        # Enrichissement individuel des contrats pour affichage multi-contrats rigoureux
        contrats_enrichis = []
        for c in contrats_qs:
            spots_diff = c.spots_utilises()
            spots_rest = c.spots_restants()
            pct_reel = round((spots_diff / c.nb_spots * 100), 1) if c.nb_spots > 0 else 0
            pct_width = min(max(int(round(spots_diff / c.nb_spots * 100)), 0), 100) if c.nb_spots > 0 else 0
            is_active_now = c.is_actif()
            camps = c.campagnes.all()
            contrats_enrichis.append({
                'contrat': c,
                'spots_diffuses': spots_diff,
                'spots_restants': spots_rest,
                'pct_width': pct_width,
                'pct_consommation': min(pct_reel, 100),
                'pct_consommation_reel': pct_reel,
                'nb_campagnes': camps.count(),
                'is_active': is_active_now,
            })

        campagnes_sans_contrat_count = campagnes_all_qs.filter(contrat__isnull=True).count()

        # ── FILTRAGE DYNAMIQUE DES CAMPAGNES ──
        req = self.request.GET
        contrat_filter = req.get('contrat', '').strip()
        statut_filter = req.get('statut', '').strip()
        type_filter = req.get('type_support', '').strip()
        search_query = req.get('q', '').strip()
        d1_str = req.get('date_debut', '').strip()
        d2_str = req.get('date_fin', '').strip()

        campagnes_filtered = campagnes_all_qs

        selected_contrat_obj = None
        if contrat_filter:
            if contrat_filter in ['hors_contrat', 'sans_contrat', 'none']:
                campagnes_filtered = campagnes_filtered.filter(contrat__isnull=True)
            else:
                try:
                    c_id = int(contrat_filter)
                    campagnes_filtered = campagnes_filtered.filter(contrat_id=c_id)
                    selected_contrat_obj = contrats_qs.filter(pk=c_id).first()
                except ValueError:
                    pass

        if statut_filter:
            campagnes_filtered = campagnes_filtered.filter(statut=statut_filter)

        if type_filter:
            if type_filter == 'mere':
                campagnes_filtered = campagnes_filtered.filter(est_mere=True)
            else:
                campagnes_filtered = campagnes_filtered.filter(type_support=type_filter, est_mere=False)

        if search_query:
            campagnes_filtered = campagnes_filtered.filter(
                Q(nom__icontains=search_query) | Q(reference__icontains=search_query)
            )

        if d1_str:
            try:
                d1 = datetime.strptime(d1_str, '%Y-%m-%d').date()
                campagnes_filtered = campagnes_filtered.filter(date_fin__gte=d1)
            except ValueError:
                pass
        if d2_str:
            try:
                d2 = datetime.strptime(d2_str, '%Y-%m-%d').date()
                campagnes_filtered = campagnes_filtered.filter(date_debut__lte=d2)
            except ValueError:
                pass

        reservations = (
            client.reservations_globales
            .filter(statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE], date_fin__gte=timezone.now())
            .prefetch_related('lignes__face', 'lignes__support')
            .order_by('date_debut')
        )

        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        page_num = self.request.GET.get('page', 1)
        paginator = Paginator(campagnes_filtered, 10)
        try:
            campagnes_page = paginator.page(page_num)
        except (EmptyPage, PageNotAnInteger):
            campagnes_page = paginator.page(1)

        has_active_filters = bool(contrat_filter or statut_filter or type_filter or search_query or d1_str or d2_str)

        context.update({
            'campagnes': campagnes_page,
            'page_obj': campagnes_page,
            'paginator': paginator,
            'is_paginated': campagnes_page.has_other_pages(),
            'total_campagnes_count': campagnes_filtered.count(),
            'total_campagnes_all': campagnes_all_qs.count(),
            'contrats': contrats_qs,
            'contrats_enrichis': contrats_enrichis,
            'campagnes_sans_contrat_count': campagnes_sans_contrat_count,
            'reservations': reservations,
            'filters': {
                'contrat': contrat_filter,
                'statut': statut_filter,
                'type_support': type_filter,
                'q': search_query,
                'date_debut': d1_str,
                'date_fin': d2_str,
            },
            'has_active_filters': has_active_filters,
            'selected_contrat_obj': selected_contrat_obj,
        })
        return context


class ClientUpdateView(StaffRequiredMixin, UpdateView):
    model = Client
    form_class = ClientForm
    template_name = 'campaigns/client_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request, AuditLog.ACTION_UPDATE, 'commercial', obj=self.object, detail=f"Modification client: {self.object.nom}")
        messages.success(self.request, 'Client mis à jour.')
        return response

    def get_success_url(self):
        return reverse('client_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Modifier — {self.object.nom}'
        context['obj'] = self.object
        return context


class ClientDeleteView(StaffRequiredMixin, DeleteView):
    model = Client
    template_name = 'partials/confirm_delete.html'

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        force_delete = request.POST.get('force_delete') == '1'
        client_name = str(self.object)
        if self.request.user.is_admin:
            try:
                if force_delete:
                    campagnes = self.object.campagnes.all()
                    if campagnes.exists():
                        campagnes.delete()
                self.object.delete()
            except ProtectedError:
                campagnes_associees = list(self.object.campagnes.all())
                messages.warning(
                    request,
                    'Ce client a des campagnes associées. Confirmez la suppression complète pour supprimer le client et toutes ses campagnes.'
                )
                return self.render_to_response(
                    self.get_context_data(
                        show_force_delete=True,
                        protected_count=len(campagnes_associees),
                        associated_campaigns=campagnes_associees,
                    )
                )
            log_action(
                request, AuditLog.ACTION_DELETE, 'commercial', obj=self.object,
                detail=f"Suppression client: {client_name}",
            )
            messages.success(request, f'Client "{client_name}" supprimé.')
            return redirect(self.get_success_url())
        # Sinon on Archive le client (actif=False) et on archive aussi les campagnes associées
        else:
            self.object.actif = False
            self.object.save()
            campagnes = self.object.campagnes.filter(actif=True)
            campagnes.update(actif=False)
            log_action(
                request, AuditLog.ACTION_UPDATE, 'commercial', obj=self.object,
                detail=f"Client {client_name} archivé (actif=False) avec {campagnes.count()} campagnes associées archivées.",
            )
            messages.success(request, f'Client "{client_name}" archivé avec ses campagnes associées.')
            return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('client_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_admin:
            context.update({
                'title': 'Supprimer le client',
                'header': 'Suppression de client',
                'message_title': 'Supprimer le client ?',
                'message_body': 'Vous êtes sur le point de supprimer le client',
                'hint': 'Toutes les données liées au client seront supprimées.',
                'confirm_label': 'Supprimer le client',
                'cancel_url': reverse('client_detail', kwargs={'pk': self.object.pk}),
            })
        else:
            context.update({
                'title': 'Archiver le client',
                'header': 'Archiver le client',
                'message_title': 'Archiver le client ?',
                'message_body': 'Vous êtes sur le point d\'archiver le client',
                'hint': 'Le client et toutes les campagnes associées seront archivés (actif=False) mais resteront dans la base de données.',
                'confirm_label': 'Archiver le client',
                'cancel_url': reverse('client_detail', kwargs={'pk': self.object.pk}),
            })
        context.update(kwargs)
        return context


class ContratCreateView(StaffRequiredMixin, CreateView):
    model = Contrat
    form_class = ContratForm
    template_name = 'campaigns/contrat_form.html'

    def get_initial(self):
        initial = super().get_initial()
        if client_pk := self.kwargs.get('client_pk'):
            initial['client'] = client_pk
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client_pk = self.kwargs.get('client_pk')
        context['client'] = get_object_or_404(Client, pk=client_pk) if client_pk else None
        context['title'] = 'Nouveau contrat / bon de commande'
        return context

    def form_valid(self, form):
        if client_pk := self.kwargs.get('client_pk'):
            form.instance.client_id = client_pk
        response = super().form_valid(form)
        transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
        log_action(
            self.request,
            AuditLog.ACTION_CREATE,
            'commercial',
            obj=self.object,
            detail=f"Création de {self.object.get_type_document_display()} {self.object.reference} pour {self.object.client.nom}",
        )
        messages.success(self.request, f'{self.object.get_type_document_display()} {self.object.reference} créé pour {self.object.client.nom}.')
        return response

    def form_invalid(self, form):
        messages.error(self.request, f'Erreurs dans le formulaire: {form.errors}')
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse('contrat_detail', kwargs={'pk': self.object.pk})


class ContratUpdateView(StaffRequiredMixin, UpdateView):
    model = Contrat
    form_class = ContratForm
    template_name = 'campaigns/contrat_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
        log_action(self.request, AuditLog.ACTION_UPDATE, 'commercial', obj=self.object, detail=f"Modification contrat: {self.object}")
        messages.success(self.request, f'{self.object.get_type_document_display()} {self.object.reference} mis à jour.')
        return response

    def get_success_url(self):
        return reverse('contrat_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Modifier Contrat — {self.object}'
        context['obj'] = self.object
        context['client'] = self.object.client
        return context


class ContratDetailView(StaffRequiredMixin, DetailView):
    model = Contrat
    template_name = 'campaigns/contrat_detail.html'
    context_object_name = 'contrat'

    def get_queryset(self):
        return Contrat.objects.select_related('client').prefetch_related('campagnes')


class ContratDeleteView(StaffRequiredMixin, DeleteView):
    model = Contrat
    template_name = 'partials/confirm_delete.html'

    def get_success_url(self):
        return reverse('client_detail', kwargs={'pk': self.object.client.pk})

    def delete(self, request, *args, **kwargs):
        if request.user.is_admin:
            contrat = self.get_object()
            contrat_str = str(contrat)
            response = super().delete(request, *args, **kwargs)
            transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
            log_action(
                request, AuditLog.ACTION_DELETE, 'commercial', obj=contrat,
                detail=f"Suppression contrat: {contrat_str}",
            )
            messages.success(request, 'Contrat supprimé.')
            return response
        # Sinon on Archive le client (actif=False) et on archive aussi les campagnes associées
        else:
            contrat = self.get_object()
            contrat_str = str(contrat)
            contrat.archive = False
            contrat.save()
            transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
            log_action(
                request, AuditLog.ACTION_UPDATE, 'commercial', obj=contrat,
                detail=f"Archivage contrat: {contrat_str}",
            )
            messages.success(request, 'Contrat archivé.')
            return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_admin:
            context.update({
                'title': 'Supprimer le contrat',
                'header': 'Suppression de contrat',
                'message_title': 'Supprimer ce contrat ?',
                'message_body': 'Vous êtes sur le point de supprimer le contrat',
                'hint': 'Cette opération supprimera définitivement le contrat associé.',
                'confirm_label': 'Supprimer le contrat',
                'cancel_url': reverse('client_detail', kwargs={'pk': self.object.client.pk}),
            })
        else:
            context.update({
                'title': 'Archiver le contrat',
                'header': 'Archiver le contrat',
                'message_title': 'Archiver ce contrat ?',
                'message_body': 'Vous êtes sur le point d\'archiver le contrat',
                'hint': 'Cette opération archivera le contrat (archive=False) mais ne le supprimera pas de la base de données.',
                'confirm_label': 'Archiver le contrat',
                'cancel_url': reverse('client_detail', kwargs={'pk': self.object.client.pk}),
            })
        return context


# ── Campagnes ─────────────────────────────────────────────────────────────────

# Les Classe des vues de campagne : list, create, detail, edit, lancer, delete

class CampagneListView(ClientStaffRequiredMixin, SortableListMixin, ListView):
    model = Campagne
    template_name = 'campaigns/campagne_list.html'
    context_object_name = 'campagnes'

    SORT_FIELDS = {
        'reference':    'reference',
        'client':       'client__nom',
        'nom':          'nom',
        'statut':       'statut',
        'date_debut':   'date_debut',
        'date_fin':     'date_fin',
        'nb_supports':  'nb_supports_annotate',
        'type_support': 'type_support',
        'actif':        'actif',
    }
    DEFAULT_SORT = 'statut'   # nom de colonne, sans signe
    DEFAULT_DIR  = 'asc'     # 'asc' ou 'desc'

    @staticmethod
    def _category_for_campaign(
        campagne,
        formats_par_code,
        categories_existantes,
    ):
        if campagne.est_mere:
            return 'Campagnes mères'
        type_support = normaliser_categorie_format(campagne.type_support)
        if type_support.casefold() in ('ecran', 'écran'):
            return 'Écran'
        if type_support.casefold() in ('marche', 'marché'):
            return 'Marché'
        if type_support.casefold() == 'panneau':
            return 'Panneau'
        categorie = normaliser_categorie_format(
            formats_par_code.get(campagne.type_support)
        )
        if categorie:
            return categorie
        return categories_existantes.get(type_support.casefold())

    def get_queryset(self):
        qs = super().get_queryset().select_related('client')

        if self.request.user.is_client:
            qs = qs.filter(client=self.request.user.client_profile)

        # ── Restriction non-admin : uniquement les campagnes actives ──
        if not self.request.user.is_admin:
            qs = qs.filter(actif=True)

        self.category_options_queryset = qs
        q            = self.request.GET.get('q', '')
        statut       = self.request.GET.get('statut', '')
        type_support = self.request.GET.get('type_support', '')
        actif        = self.request.GET.get('actif', '')

        if q:
            qs = qs.filter(Q(nom__icontains=q) | Q(client__nom__icontains=q) | Q(reference__icontains=q))
        if statut:
            qs = qs.filter(statut=statut)
        if actif:
            qs = qs.filter(actif=actif)

        self.category_base_queryset = qs
        categorie = self.request.GET.get('categorie', '').strip()
        if type_support and not categorie:
            qs = qs.filter(type_support=type_support)
        if categorie == 'Campagnes mères':
            qs = qs.filter(est_mere=True)
        elif categorie:
            category_codes = FormatSupport.hors_ecran().filter(
                categorie__iexact=categorie,
            ).values_list('code', flat=True)
            category_filter = Q(type_support=categorie)
            if categorie.casefold() in ('écran', 'ecran'):
                category_filter |= Q(type_support='ecran')
            elif categorie.casefold() == 'marché':
                category_filter |= Q(type_support__in=['marche', 'Marché'])
            elif categorie.casefold() == 'panneau':
                category_filter |= Q(type_support__in=['panneau', 'Panneau'])
            else:
                category_filter |= Q(type_support__in=category_codes)
            qs = qs.filter(category_filter)

        qs = qs.annotate(nb_supports_annotate=Count('lignes__support', distinct=True))
        qs = self.apply_sort(qs)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)  # ne touche plus à context['campagnes']

        base_qs = Campagne.objects.all()
        if self.request.user.is_client:
            base_qs = base_qs.filter(client=self.request.user.client_profile)
        if not self.request.user.is_admin:
            base_qs = base_qs.filter(actif=True)

        categorie_active = self.request.GET.get('categorie', '').strip()
        if categorie_active == 'Campagnes mères':
            base_qs = base_qs.filter(est_mere=True)
        elif categorie_active:
            category_codes = FormatSupport.hors_ecran().filter(
                categorie__iexact=categorie_active,
            ).values_list('code', flat=True)
            category_filter = Q(type_support=categorie_active)
            if categorie_active.casefold() in ('écran', 'ecran'):
                category_filter |= Q(type_support='ecran')
            elif categorie_active.casefold() == 'marché':
                category_filter |= Q(type_support__in=['marche', 'Marché'])
            elif categorie_active.casefold() == 'panneau':
                category_filter |= Q(type_support__in=['panneau', 'Panneau'])
            else:
                category_filter |= Q(type_support__in=category_codes)
            base_qs = base_qs.filter(category_filter)

        context['kpi_total'] = base_qs.count()
        context['kpi_en_cours'] = base_qs.filter(statut='en_cours').count()
        context['kpi_en_pause'] = base_qs.filter(statut='en_pause').count()
        context['kpi_a_venir'] = base_qs.filter(statut='a_venir').count()
        context['kpi_terminee'] = base_qs.filter(statut='terminee').count()
        context['kpi_brouillon'] = base_qs.filter(statut='brouillon').count()

        context['q']                    = self.request.GET.get('q', '')
        context['statut']               = self.request.GET.get('statut', '')
        context['statut_choices']       = Campagne.STATUT_CHOICES
        context['type_support']         = self.request.GET.get('type_support', '')
        context['type_support_choices'] = get_type_support_choices()
        context['actif']                = self.request.GET.get('actif', '')

        formats_par_code = dict(
            FormatSupport.objects.filter(
                code__in={
                    campagne.type_support
                    for campagne in self.category_options_queryset
                    if campagne.type_support
                }
            ).values_list('code', 'categorie')
        )
        categories_existantes = {
            normaliser_categorie_format(categorie).casefold():
            normaliser_categorie_format(categorie)
            for categorie in FormatSupport.hors_ecran().values_list(
                'categorie', flat=True
            )
            if normaliser_categorie_format(categorie)
        }
        categories = {
            categorie
            for campagne in self.category_options_queryset
            if (categorie := self._category_for_campaign(
                campagne,
                formats_par_code,
                categories_existantes,
            ))
        }
        current_params = self.request.GET.copy()
        current_params.pop('page', None)
        current_params.pop('type_support', None)
        category_filters = []
        all_count = self.category_base_queryset.count()
        all_params = current_params.copy()
        all_params.pop('categorie', None)
        category_filters.append({
            'label': 'Toutes',
            'value': '',
            'count': all_count,
            'url': f"{reverse('campagne_list')}?{all_params.urlencode()}" if all_params else reverse('campagne_list'),
        })
        for categorie in sorted(categories, key=str.casefold):
            params = current_params.copy()
            params['categorie'] = categorie
            category_query = (
                Q(est_mere=True)
                if categorie == 'Campagnes mères'
                else self._category_query(categorie, formats_par_code)
            )
            category_filters.append({
                'label': categorie,
                'value': categorie,
                'count': self.category_base_queryset.filter(category_query).count(),
                'url': f"{reverse('campagne_list')}?{params.urlencode()}",
            })
        context['campaign_category_filters'] = category_filters
        context['categorie_active'] = categorie_active

        return context

    @staticmethod
    def _category_query(categorie, formats_par_code):
        if categorie.casefold() in ('écran', 'ecran'):
            return Q(type_support='ecran')
        if categorie.casefold() == 'marché':
            return Q(type_support__in=['marche', 'Marché'])
        if categorie.casefold() == 'panneau':
            return Q(type_support__in=['panneau', 'Panneau'])
        codes = [
            code for code, label in formats_par_code.items()
            if (label or '').strip().casefold() == categorie.casefold()
        ]
        return Q(type_support=categorie) | Q(type_support__in=codes)
    def post(self, request, *args, **kwargs):
        if not request.user.is_staff_regie_role:
            messages.error(request, "Permission refusée.")
            return redirect('campagne_list')

        action       = request.POST.get('action', '')
        selected_ids = request.POST.getlist('selected_ids')

        if not selected_ids:
            messages.warning(request, "Aucune campagne sélectionnée.")
            return redirect('campagne_list')
        qs = Campagne.objects.filter(pk__in=selected_ids)
        if action == 'supprimer':
            request.session['selected_campagne_ids'] = selected_ids
            return redirect('campagne_selected_delete')
        elif action == 'archiver':
            count = qs.filter(actif=True).update(actif=False)
            messages.success(request, f"{count} campagne(s) archivée(s).")
        elif action == 'desarchiver':
            count = qs.filter(actif=False).update(actif=True)
            messages.success(request, f"{count} campagne(s) désarchivée(s).")
        elif action in {
            'mettre_en_pause',
            'annuler',
            'reprendre_prolonger',
            'reprendre_garder',
        }:
            resultats = [
                _transition_campagne(campagne, action)
                for campagne in qs
            ]
            nombre_ok = sum(1 for succes, _ in resultats if succes)
            details = [message for succes, message in resultats if not succes]
            if nombre_ok:
                messages.success(
                    request, f"{nombre_ok} campagne(s) mise(s) à jour."
                )
            if details:
                messages.warning(request, " ".join(details[:3]))
        else:
            messages.error(request, f"Action inconnue : {action}")
        return redirect('campagne_list')


@transaction.atomic
def _transition_campagne(campagne, action):
    aujourd_hui = timezone.localdate()
    statuts_modifiables = {
        Campagne.STATUT_EN_COURS,
        Campagne.STATUT_A_VENIR,
        Campagne.STATUT_EN_PAUSE,
    }

    if action == 'mettre_en_pause':
        if campagne.statut not in {
            Campagne.STATUT_EN_COURS,
            Campagne.STATUT_A_VENIR,
        }:
            return False, f"{campagne.nom} ne peut pas être mise en pause."
        campagne.statut = Campagne.STATUT_EN_PAUSE
        campagne.date_mise_en_pause = aujourd_hui
        campagne.save(update_fields=['statut', 'date_mise_en_pause', 'updated_at'])
        CampagnePause.objects.create(campagne=campagne, date_debut=aujourd_hui)
        return True, ''

    if action == 'annuler':
        if campagne.statut not in statuts_modifiables:
            return False, f"{campagne.nom} ne peut pas être annulée."
        if campagne.statut == Campagne.STATUT_EN_PAUSE:
            campagne.pauses.filter(date_fin__isnull=True).update(
                date_fin=aujourd_hui - timedelta(days=1)
            )
        campagne.statut = Campagne.STATUT_ANNULEE
        campagne.date_annulation = aujourd_hui
        campagne.date_fin = min(
            campagne.date_fin, aujourd_hui - timedelta(days=1)
        )
        campagne.date_mise_en_pause = None
        campagne.save(update_fields=[
            'statut', 'date_annulation', 'date_fin', 'date_mise_en_pause',
            'updated_at',
        ])
        return True, ''

    if action in {'reprendre_prolonger', 'reprendre_garder'}:
        if campagne.statut != Campagne.STATUT_EN_PAUSE:
            return False, f"{campagne.nom} n'est pas en pause."
        date_pause = campagne.date_mise_en_pause or aujourd_hui
        jours_pause = max((aujourd_hui - date_pause).days, 0)
        if action == 'reprendre_prolonger':
            campagne.date_fin += timedelta(days=jours_pause)
        campagne.pauses.filter(date_fin__isnull=True).update(
            date_fin=aujourd_hui - timedelta(days=1)
        )
        campagne.date_mise_en_pause = None
        if campagne.date_debut > aujourd_hui:
            campagne.statut = Campagne.STATUT_A_VENIR
        elif campagne.date_fin >= aujourd_hui:
            campagne.statut = Campagne.STATUT_EN_COURS
        else:
            campagne.statut = Campagne.STATUT_TERMINEE
        campagne.save(update_fields=[
            'statut', 'date_fin', 'date_mise_en_pause', 'updated_at',
        ])
        return True, ''

    return False, f"Action inconnue pour {campagne.nom}."


class CampagneStateChangeView(StaffRequiredMixin, View):
    def post(self, request, pk):
        campagne = get_object_or_404(Campagne, pk=pk)
        action = request.POST.get('action', '')
        succes, message = _transition_campagne(campagne, action)
        if succes:
            messages.success(request, "État de la campagne mis à jour.")
        else:
            messages.error(request, message)
        return redirect('campagne_detail', pk=campagne.pk)


class CampagneArchiveToggleView(StaffRequiredMixin, View):
    """Archive or restore a campaign from its detail page."""

    def post(self, request, pk):
        campagne = get_object_or_404(Campagne, pk=pk)
        action = request.POST.get('action')
        if action not in {'archiver', 'desarchiver'}:
            messages.error(request, "Action d'archivage inconnue.")
            return redirect('campagne_detail', pk=campagne.pk)
        if action == 'archiver' and campagne.actif:
            campagne.actif = False
            campagne.save(update_fields=['actif'])
            messages.success(request, 'Campagne archivée.')
        elif action == 'desarchiver' and not campagne.actif:
            campagne.actif = True
            campagne.save(update_fields=['actif'])
            messages.success(request, 'Campagne désarchivée.')
        else:
            messages.info(request, 'La campagne est déjà dans cet état.')
        return redirect('campagne_detail', pk=campagne.pk)


class CampagneSelectedDeleteView(StaffRequiredMixin, TemplateView):
    # On cible ton fichier HTML générique de confirmation
    template_name = 'partials/confirm_delete.html'

    def dispatch(self, request, *args, **kwargs):
        # Sécurité : Seul le staff de la régie peut supprimer
        if not request.user.is_staff_regie_role:
            messages.error(request, "Permission refusée.")
            return redirect('campagne_list')
            
        # Récupération des IDs sélectionnés depuis la session
        self.selected_ids = request.session.get('selected_campagne_ids', [])
        if not self.selected_ids:
            messages.warning(request, "Aucune campagne sélectionnée pour la suppression.")
            return redirect('campagne_list')
            
        self.campagnes_to_delete = Campagne.objects.filter(pk__in=self.selected_ids)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        count = self.campagnes_to_delete.count()
        
        # On alimente les variables attendues par ton template générique
        context['title'] = 'Suppression groupée'
        context['header'] = 'Action groupée irréversible'
        context['message_title'] = f"Supprimer définitivement {count} campagne(s) ?"
        context['message_body'] = "Vous êtes sur le point de supprimer de l'inventaire ces "
        context['obj'] = f"{count} campagne(s) sélectionnée(s)"
        context['hint'] = "Cette opération libérera immédiatement les supports réservés pour ces périodes."
        context['cancel_url'] = reverse_lazy('campagne_list')
        
        # Optionnel : si tu veux lister les noms des campagnes dans la zone de texte, 
        # on peut détourner proprement la variable associée
        context['show_force_delete'] = True
        context['protected_count'] = count
        context['associated_campaigns'] = self.campagnes_to_delete
        
        return context

    def post(self, request, *args, **kwargs):
        count = self.campagnes_to_delete.count()
        
        # Suppression effective des éléments
        self.campagnes_to_delete.delete()
        
        # Nettoyage de la session après traitement
        if 'selected_campagne_ids' in request.session:
            del request.session['selected_campagne_ids']
            
        messages.success(request, f"{count} campagne(s) supprimée(s) avec succès.")
        return redirect('campagne_list')


# @login_required
# def campagne_list(request):
#     campagnes = Campagne.objects.select_related('client').all()
#     # Filtre client en lecture seule
#     if request.user.is_client_role and request.user.client_profile:
#         campagnes = campagnes.filter(client=request.user.client_profile)
#     q = request.GET.get('q', '')
#     statut = request.GET.get('statut', '')
#     if q:
#         campagnes = campagnes.filter(Q(nom__icontains=q) | Q(client__nom__icontains=q) | Q(reference__icontains=q))
#     if statut:
#         campagnes = campagnes.filter(statut=statut)
#     return render(request, 'campaigns/campagne_list.html', {
#         'campagnes': campagnes, 'q': q, 'statut': statut,
#         'statut_choices': Campagne.STATUT_CHOICES,
#     })

from decimal import Decimal, InvalidOperation


def _to_decimal(val):
    """Convertit int/float/Decimal en Decimal proprement (arrondi 2 décimales)."""
    if val is None:
        return Decimal('0.00')
    try:
        return Decimal(str(round(float(val), 2)))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def build_support_rows(campagne, lignes):
    """
    Regroupe les lignes par support (ou par MARCHÉ si campagne.type_support == 'marche')
    et calcule, pour chaque groupe :
    - quantite (spots pour écran, nb de faces pour panneau, nb d'emplacements pour marché)
    - prix / affichage / impression unitaires et totaux
    - total_support = prix_total + affichage_total + impression_total
    Retourne (rows, totaux_campagne)
    """
    grouped = {}
    order = []

    if campagne.type_support == 'marche':
        for ligne in lignes:
            if not ligne.emplacement_id:
                continue
            marche = ligne.emplacement.marche
            if marche.pk not in grouped:
                grouped[marche.pk] = {'support': marche, 'lignes': []}
                order.append(marche.pk)
            grouped[marche.pk]['lignes'].append(ligne)
    else:
        for ligne in lignes:
            support = ligne.face.support if ligne.face else ligne.support
            if support is None:
                continue
            if support.pk not in grouped:
                grouped[support.pk] = {'support': support, 'lignes': []}
                order.append(support.pk)
            grouped[support.pk]['lignes'].append(ligne)

    prix_u = campagne.prix or Decimal('0.00')
    aff_u = campagne.prix_affichage or Decimal('0.00')
    imp_u = campagne.prix_impression or Decimal('0.00')

    rows = []
    totaux = {
        'quantite': Decimal('0.00'),
        'prix': Decimal('0.00'),
        'affichage': Decimal('0.00'),
        'impression': Decimal('0.00'),
        'total': Decimal('0.00'),
    }

    for pk in order:
        support = grouped[pk]['support']
        group_lignes = grouped[pk]['lignes']

        if campagne.type_support == 'ecran':
            quantite = _to_decimal(sum(getattr(l, 'spots_calcules', 0) for l in group_lignes))
            faces_labels = None
        else:
            quantite = Decimal(len(group_lignes))
            faces_labels = [l.face.label for l in group_lignes if l.face]

        prix_total = (prix_u * quantite).quantize(Decimal('0.01'))
        aff_total = (aff_u * quantite).quantize(Decimal('0.01'))
        imp_total = (imp_u * quantite).quantize(Decimal('0.01'))
        total_support = prix_total + aff_total + imp_total

        rows.append({
            'support': support,
            'quantite': quantite,
            'faces_labels': faces_labels,
            'prix_unitaire': prix_u,
            'prix_total': prix_total,
            'affichage_unitaire': aff_u,
            'affichage_total': aff_total,
            'impression_unitaire': imp_u,
            'impression_total': imp_total,
            'total_support': total_support,
            'lignes': group_lignes,
            'faces': [l.face for l in group_lignes if l.face],
        })

        totaux['quantite'] += quantite
        totaux['prix'] += prix_total
        totaux['affichage'] += aff_total
        totaux['impression'] += imp_total
        totaux['total'] += total_support

    return rows, totaux


def serialize_supports_for_map(lignes):
    """Génère la liste structurée des supports/marchés pour la carte interactive Leaflet."""
    data = []
    seen = set()
    for ligne in lignes:
        if ligne.emplacement_id and ligne.emplacement and ligne.emplacement.marche:
            marche = ligne.emplacement.marche
            key = f"marche_{marche.pk}"
            if key not in seen:
                seen.add(key)
                if marche.latitude and marche.longitude:
                    empls = [l.emplacement for l in lignes if l.emplacement_id and l.emplacement and l.emplacement.marche_id == marche.pk]
                    data.append({
                        'id': marche.pk,
                        'type': 'marche',
                        'code': marche.nom,
                        'nom': marche.nom,
                        'lat': float(marche.latitude),
                        'lng': float(marche.longitude),
                        'ville': marche.ville or '',
                        'quartier': marche.quartier or '',
                        'adresse': marche.adresse or '',
                        'nb_emplacements': len(empls),
                        'emplacements': [{'code': e.code, 'is_libre': e.is_libre()} for e in empls if e],
                        'url': f"/gestion/inventory/marches/{marche.pk}/",
                    })
        else:
            support = ligne.face.support if ligne.face else ligne.support
            if not support:
                continue
            key = f"support_{support.pk}"
            if key not in seen:
                seen.add(key)
                if support.latitude and support.longitude:
                    supp_lignes = [l for l in lignes if (l.face and l.face.support_id == support.pk) or l.support_id == support.pk]
                    faces_data = []
                    for l in supp_lignes:
                        if l.face:
                            faces_data.append({
                                'label': l.face.get_label_display(),
                                'eclairage': l.face.get_eclairage_display(),
                                'etat': l.face.get_etat_display(),
                                'photo': l.face.photo.url if l.face.photo else '',
                            })
                    
                    ecran_info = None
                    if support.type_support == 'ecran' and hasattr(support, 'ecran_info'):
                        e_info = support.ecran_info
                        plages_actives = e_info.plages_actives()
                        ecran_info = {
                            'type_ecran': e_info.get_type_ecran_display(),
                            'resolution': e_info.get_resolution_display(),
                            'cellule': e_info.get_cellule_display() if hasattr(e_info, 'get_cellule_display') else e_info.cellule,
                            'heure_allumage': plages_actives[0][0].strftime('%H:%M') if plages_actives else None,
                            'heure_extinction': plages_actives[-1][1].strftime('%H:%M') if plages_actives else None,
                            'plages_horaires': [
                                {
                                    'debut': debut.strftime('%H:%M'),
                                    'fin': fin.strftime('%H:%M'),
                                }
                                for debut, fin in plages_actives
                            ],
                            'spots': supp_lignes[0].calculer_spots() if supp_lignes else 0,
                            'frequence': supp_lignes[0].get_frequence_effective() if supp_lignes else '',
                            'duree': supp_lignes[0].get_duree_passage_effective() if supp_lignes else '',
                        }

                    data.append({
                        'id': support.pk,
                        'uuid': str(support.uuid) if support.uuid else '',
                        'code': support.code,
                        'nom': support.nom,
                        'type': support.type_support,
                        'lat': float(support.latitude),
                        'lng': float(support.longitude),
                        'ville': support.ville or '',
                        'quartier': support.quartier or '',
                        'adresse': support.adresse or '',
                        'format': support.get_format_display() if support.format else '',
                        'dimensions': support.dimensions,
                        'surface': support.surface_m2,
                        'photo': support.photo_principale.url if support.photo_principale else '',
                        'faces': faces_data,
                        'ecran_info': ecran_info,
                        'url': f"/gestion/inventory/{support.uuid}/" if support.uuid else f"/gestion/inventory/{support.pk}/",
                    })
    return data


class CampagneDetailView(ClientStaffRequiredMixin, DetailView):
    model = Campagne
    template_name = 'campaigns/campagne_detail.html'
    context_object_name = 'campagne'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        campagne = self.object

        lignes = campagne.lignes.select_related(
            'support__ecran_info', 'face__support', 'emplacement__marche'
        ).all()

        for ligne in lignes:
            if campagne.type_support == 'ecran' and ligne.support:
                ligne.spots_calcules = ligne.calculer_spots()
            elif campagne.type_support == 'marche' and ligne.emplacement:
                total_jours = campagne.duree_jours()
                jours_dispo = ligne.emplacement.jours_disponibles_sur_periode(campagne.date_debut, campagne.date_fin)
                ligne.spots_calcules = round(jours_dispo / total_jours, 2) if total_jours else 0
            elif campagne.type_support != 'ecran' and ligne.face:
                ligne.spots_calcules = ligne.face.calculer_nombre_spots_campagne(campagne)
            else:
                ligne.spots_calcules = 0

        context['lignes'] = lignes
        context['sous_campagnes'] = campagne.sous_campagnes.all()
        campagne_ids = [campagne.pk]
        if campagne.est_mere:
            campagne_ids.extend(context['sous_campagnes'].values_list('pk', flat=True))
        context['preuves_diffusion'] = PreuveDiffusion.objects.filter(
            campagne_id__in=campagne_ids
        ).select_related(
            'campagne', 'ligne__support', 'ligne__face__support',
            'ligne__emplacement__marche', 'cree_par',
        )
        context['pige_fichiers_drive'] = PigeFichier.objects.filter(
            campagne_id__in=campagne_ids
        ).select_related(
            'campagne', 'ligne__support', 'ligne__face__support',
            'ligne__emplacement__marche', 'support', 'ajoute_par',
        )

        if campagne.est_mere:
            enfants = campagne.sous_campagnes.all()
            enfants_data = []
            total_campagne = {
                'quantite': Decimal('0.00'),
                'prix': Decimal('0.00'),
                'affichage': Decimal('0.00'),
                'impression': Decimal('0.00'),
                'total': Decimal('0.00'),
            }

            all_child_lignes = []
            for enfant in enfants:
                enfant_lignes = enfant.lignes.select_related(
                    'support__ecran_info', 'face__support', 'emplacement__marche'
                ).all()
                for ligne in enfant_lignes:
                    if enfant.type_support == 'ecran' and ligne.support:
                        ligne.spots_calcules = ligne.calculer_spots()
                    elif enfant.type_support == 'marche' and ligne.emplacement:
                        total_jours = enfant.duree_jours()
                        jours_dispo = ligne.emplacement.jours_disponibles_sur_periode(
                            enfant.date_debut,
                            enfant.date_fin,
                            exclude_campagne_id=enfant.pk,
                        )
                        ligne.spots_calcules = round(jours_dispo / total_jours, 2) if total_jours else 0
                    elif enfant.type_support != 'ecran' and ligne.face:
                        ligne.spots_calcules = ligne.face.calculer_nombre_spots_campagne(enfant)
                    else:
                        ligne.spots_calcules = 0
                    all_child_lignes.append(ligne)

                rows, totaux = build_support_rows(enfant, enfant_lignes)
                enfants_data.append({
                    'campagne': enfant,
                    'rows': rows,
                    'totaux': totaux,
                })
                for key in total_campagne:
                    total_campagne[key] += totaux[key]

            context['enfants_data'] = enfants_data
            context['total_campagne'] = total_campagne
            context['nombre_sous_campagnes'] = enfants.count()
            context['total_supports'] = sum(child.lignes.count() for child in enfants)
            context['total_spots'] = sum(child.calculer_nombre_spots() for child in enfants)
            context['supports_map_json'] = json.dumps(serialize_supports_for_map(all_child_lignes))
        else:
            support_rows, totaux_campagne = build_support_rows(campagne, lignes)
            context['support_rows'] = support_rows
            context['totaux_campagne'] = totaux_campagne
            context['total_supports'] = len(support_rows)
            context['total_spots'] = campagne.calculer_nombre_spots()
            context['supports_map_json'] = json.dumps(serialize_supports_for_map(lignes))

        return context



def _importer_faces_reservation(campagne, reservation, formats_autorises):
    reservation_lignes = (
        reservation.lignes
        .filter(
            face__isnull=False,
            face__support__format__in=formats_autorises,
        )
        .select_related('face__support')
    )
    face_ids_existants = set(
        campagne.lignes
        .filter(face__isnull=False)
        .values_list('face_id', flat=True)
    )
    lignes_a_importer = [
        LigneCampagne(
            campagne=campagne,
            support=reservation_ligne.face.support,
            face=reservation_ligne.face,
        )
        for reservation_ligne in reservation_lignes
        if reservation_ligne.face_id not in face_ids_existants
    ]
    if lignes_a_importer:
        LigneCampagne.objects.bulk_create(lignes_a_importer)
    return len(lignes_a_importer)


def _importer_faces_demande(campagne, demande, formats_autorises):
    faces = demande.faces_souhaitees.filter(
        support__format__in=formats_autorises,
    ).select_related('support')
    face_ids_existants = set(
        campagne.lignes.filter(face__isnull=False).values_list('face_id', flat=True)
    )
    lignes_a_importer = [
        LigneCampagne(campagne=campagne, support=face.support, face=face)
        for face in faces
        if face.pk not in face_ids_existants
    ]
    if lignes_a_importer:
        LigneCampagne.objects.bulk_create(lignes_a_importer)
    return len(lignes_a_importer)


class CampagneCreateUpdateView(StaffRequiredMixin, UpdateView):
    model = Campagne
    form_class = CampagneForm
    template_name = 'campaigns/campagne_form.html'

    def get_source_demande(self):
        if not hasattr(self, '_source_demande'):
            demande_uuid = (
                self.request.GET.get('demande')
                or self.request.POST.get('demande_uuid')
            )
            self._source_demande = None
            if demande_uuid and not self.kwargs.get('pk'):
                self._source_demande = get_object_or_404(
                    DemandeReservation.objects.filter(
                        statut__in=(
                            DemandeReservation.STATUT_NOUVELLE,
                            DemandeReservation.STATUT_EN_COURS,
                        ),
                    ),
                    uuid=demande_uuid,
                )
            elif not self.kwargs.get('pk'):
                reference = self.request.POST.get('reservation_reference', '').strip()
                if reference:
                    self._source_demande = DemandeReservation.objects.filter(
                        statut__in=(
                            DemandeReservation.STATUT_NOUVELLE,
                            DemandeReservation.STATUT_EN_COURS,
                        ),
                        reference__iexact=reference,
                    ).first()
        return self._source_demande

    def get_object(self, queryset=None):
        # Si 'pk' est présent dans l'URL, c'est une modification, sinon une création
        pk = self.kwargs.get('pk')
        return get_object_or_404(Campagne, pk=pk) if pk else None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        demande = self.get_source_demande()
        if demande:
            kwargs['demande'] = demande
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        demande = self.get_source_demande()
        if demande:
            client = demande.client or (
                Client.objects.filter(email__iexact=demande.email)
                .order_by('pk')
                .first()
            )
            initial.update({
                'client': client.pk if client else '',
                'annonceur': demande.societe or demande.nom_contact,
                'nom': demande.nom_campagne or f"Campagne {demande.reference}",
                'date_debut': demande.date_debut_souhaitee,
                'date_fin': demande.date_fin_souhaitee,
                'lieu': self._demande_lieu(demande),
                'reservation_reference': demande.reference,
                'type_support': self._demande_categorie(demande),
            })
        # Utilisation de .get() pour éviter les erreurs si la clé n'existe pas
        if client_id := self.request.GET.get('client'):
            if not demande:
                initial['client'] = client_id
        if campagne_parente_id := self.request.GET.get('campagne_parente'):
            initial['campagne_parente'] = campagne_parente_id
            if not initial.get('client'):
                try:
                    mere = Campagne.objects.select_related('client').get(pk=campagne_parente_id)
                    initial['client'] = mere.client_id
                except Campagne.DoesNotExist:
                    pass

        # Préremplissage depuis le simulateur écran.
        for field in (
            'nom', 'date_debut', 'date_fin', 'type_support',
            'duree_passage', 'frequence', 'nombre_visuels',
            'tranches_horaires',
        ):
            value = self.request.GET.get(field)
            if value not in (None, ''):
                if field in ('date_debut', 'date_fin'):
                    try:
                        value = datetime.strptime(value, '%Y-%m-%d').date()
                    except ValueError:
                        continue
                initial[field] = value
        return initial

    @staticmethod
    def _demande_lieu(demande):
        villes = list(
            demande.faces_souhaitees
            .exclude(support__ville='')
            .values_list('support__ville', flat=True)
            .distinct()
        )
        return villes[0] if len(villes) == 1 else ''

    @staticmethod
    def _demande_categorie(demande):
        formats = FormatSupport.objects.in_bulk(field_name='code')
        categories = {
            normaliser_categorie_format(formats[face.support.format].categorie)
            for face in demande.faces_souhaitees.select_related('support')
            if face.support.format in formats
            and formats[face.support.format].categorie
        }
        return next(iter(categories)) if len(categories) == 1 else ''

    @transaction.atomic
    def form_valid(self, form):
        is_create = self.object is None
        demande = self.get_source_demande()
        faces_auto_selectionnees = []
        previous_values = {}
        if not is_create:
            previous_values = {
                field: getattr(self.object, field)
                for field in (
                    'date_debut', 'date_fin', 'duree_passage',
                    'frequence', 'tranches_horaires',
                )
            }

        nombre_faces_temporaire = form.cleaned_data.get('nombre_faces_temporaire')
        if nombre_faces_temporaire:
            faces_auto_selectionnees = list(
                form.faces_disponibles_pour_selection(
                    form.cleaned_data['type_support'],
                    form.cleaned_data['lieu'],
                    form.cleaned_data['date_debut'],
                    form.cleaned_data['date_fin'],
                )
                .select_for_update()
                .order_by('?')[:nombre_faces_temporaire]
            )
            if len(faces_auto_selectionnees) < nombre_faces_temporaire:
                form.add_error(
                    'nombre_faces_temporaire',
                    "Le nombre de faces disponibles a changé. Réduisez le nombre demandé ou modifiez les critères.",
                )
                return self.form_invalid(form)
        
        if is_create:
            form.instance.created_by = self.request.user

        if demande:
            client = form.demande_client
            if client is None:
                client = Client.objects.create(
                    email=demande.email,
                    nom=demande.societe or demande.nom_contact,
                    contact_nom=demande.nom_contact,
                    telephone=demande.telephone,
                )
            form.cleaned_data['client'] = client
            form.instance.client = client
        
        if form.cleaned_data.get('statut') == Campagne.STATUT_BROUILLON:
            form.instance.actif = False
        
        self.object = form.save()
        transaction.on_commit(lambda: cache.delete('dashboard_alertes'))

        faces_importees = 0
        if form.demande_reference_selected and demande:
            faces_importees = _importer_faces_demande(
                self.object,
                demande,
                form.demande_formats,
            )
        elif form.reservation_source:
            faces_importees = _importer_faces_reservation(
                self.object,
                form.reservation_source,
                form.reservation_formats,
            )
        if faces_auto_selectionnees:
            LigneCampagne.objects.bulk_create([
                LigneCampagne(
                    campagne=self.object,
                    support=face.support,
                    face=face,
                )
                for face in faces_auto_selectionnees
            ])

        if demande:
            demande.marquer_validee(
                client=self.object.client,
                reservation=None,
                campagne=self.object,
                user=self.request.user,
            )

        if not is_create:
            changed_line_fields = {
                field: getattr(self.object, field)
                for field, old_value in previous_values.items()
                if old_value != getattr(self.object, field)
            }
            for ligne in self.object.lignes.all():
                # Un changement de fréquence de campagne s'applique à tous les écrans.
                update_fields = {
                    field: new_value
                    for field, new_value in changed_line_fields.items()
                    if field == 'frequence'
                    or getattr(ligne, field) == previous_values[field]
                }
                if update_fields:
                    LigneCampagne.objects.filter(pk=ligne.pk).update(**update_fields)

        # Lorsqu'une campagne vient du simulateur, reprendre les écrans choisis.
        if is_create and self.object.type_support == 'ecran':
            support_ids = {
                int(value)
                for value in self.request.GET.get('supports', '').split(',')
                if value.isdigit()
            }
            support_ids_bloc2 = {
                int(value)
                for value in self.request.GET.get('supports_bloc2', '').split(',')
                if value.isdigit()
            }
            supports = Support.objects.filter(
                pk__in=support_ids,
                type_support='ecran',
                actif=True,
            )
            lignes_principal = [
                LigneCampagne(
                    campagne=self.object,
                    support=support,
                    date_debut=self.object.date_debut,
                    date_fin=(
                        datetime.strptime(self.request.GET['date_fin_principal'], '%Y-%m-%d').date()
                        if self.request.GET.get('date_fin_principal') else self.object.date_fin
                    ),
                    duree_passage=self.object.duree_passage,
                    frequence=self.object.frequence,
                    tranches_horaires=self.object.tranches_horaires,
                )
                for support in supports
            ]
            LigneCampagne.objects.bulk_create(lignes_principal)

            jours_ajout = int(self.request.GET.get('jours_ajout') or 0)
            frequence_ajout = self.request.GET.get('frequence_ajout')
            tranches_ajout = self.request.GET.get('tranches_ajout')
            if jours_ajout and frequence_ajout and tranches_ajout and support_ids_bloc2:
                date_fin_principal = (
                    datetime.strptime(self.request.GET['date_fin_principal'], '%Y-%m-%d').date()
                    if self.request.GET.get('date_fin_principal') else self.object.date_fin - timedelta(days=jours_ajout)
                )
                date_debut_bloc2 = date_fin_principal + timedelta(days=1)
                supports_bloc2 = Support.objects.filter(
                    pk__in=support_ids_bloc2,
                    type_support='ecran',
                    actif=True,
                )
                LigneCampagne.objects.bulk_create([
                    LigneCampagne(
                        campagne=self.object,
                        support=support,
                        date_debut=date_debut_bloc2,
                        date_fin=self.object.date_fin,
                        duree_passage=self.object.duree_passage,
                        frequence=int(frequence_ajout),
                        tranches_horaires=tranches_ajout,
                    )
                    for support in supports_bloc2
                ])
        
        # Fichiers nouvellement uploadés
        fichiers = form.cleaned_data.get('visuels_multiples', [])
        if fichiers:
            for f in fichiers:
                CampagneVisuel.objects.create(campagne=self.object, fichier=f)
        elif self.object.campagne_parente_id and not self.object.visuels.exists():
            # NOUVEAU : héritage des visuels de la campagne mère
            # (seulement si aucun visuel propre n'existe déjà, pour éviter les doublons aux modifications suivantes)
            for v in self.object.campagne_parente.visuels.all():
                CampagneVisuel.objects.create(campagne=self.object, fichier=v.fichier)

        self.object.auto_update_statut()
        
        action = AuditLog.ACTION_CREATE if is_create else AuditLog.ACTION_UPDATE
        msg_text = f"Campagne {'créée' if is_create else 'mise à jour'} avec succès."
        if faces_importees:
            source_reference = (
                form.reservation_source.reference
                if form.reservation_source
                else demande.reference
            )
            msg_text += (
                f" {faces_importees} face(s) importée(s) depuis la référence "
                f"{source_reference}."
            )
        if faces_auto_selectionnees:
            msg_text += (
                f" {len(faces_auto_selectionnees)} face(s) sélectionnée(s) automatiquement "
                "selon la disponibilité, la catégorie, la ville et la période."
            )
        if demande:
            msg_text += (
                f" La demande {demande.reference} est validée comme campagne; "
                "les réservations existantes sont conservées."
            )
        
        log_action(
            self.request,
            action,
            'campaign',
            obj=self.object,
            detail=f"{'Création' if is_create else 'Modification'} campagne: {self.object.nom}"
        )
        
        messages.success(self.request, msg_text)
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse('campagne_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['demande_source'] = self.get_source_demande()
        # Utilisation d'une logique ternaire pour plus de clarté
        context['title'] = f"Modifier — {self.object.nom}" if self.object else "Nouvelle Campagne"
        if self.object:
            context['obj'] = self.object

        # Regroupement des formats de panneaux par catégorie (Standard, Géant, Sucette, Marché...),
        # pour le sélecteur en cascade Catégorie → Format dans campagne_form.html.
        formats_par_categorie = {}
        for f in formats_croissants(FormatSupport.hors_ecran()):
            cat = normaliser_categorie_format(f.categorie) or 'Autre'
            formats_par_categorie.setdefault(cat, []).append({
                'code': f.code,
                'label': f"{f.dimensions} ({f.superficie:g}m²)" if f.superficie else f.dimensions,
            })
        context['formats_par_categorie'] = formats_par_categorie
        context['categories_format'] = sorted(
            formats_par_categorie,
            key=str.casefold,
        )
        return context
    # def get(self, request, *args, **kwargs):
    #     self.object = self.get_object()
    #     print("=== DEBUG EDIT ===")
    #     print("kwargs:", self.kwargs)
    #     print("self.object:", self.object)
    #     if self.object:
    #         print("self.object.pk:", self.object.pk)
    #         print("self.object.date_debut:", self.object.date_debut)
    #         print("self.object.prix:", self.object.prix)
    #     form = self.get_form()
    #     print("form.instance.pk:", form.instance.pk)
    #     print("form.initial:", form.initial)
    #     print("==================")
    #     return super().get(request, *args, **kwargs)



@login_required
def api_campagne_parente_info(request, campagne_id):
    """Retourne les dates et visuels d'une campagne mère, pour héritage côté sous-campagne."""
    try:
        parent = Campagne.objects.get(pk=campagne_id, est_mere=True)
    except Campagne.DoesNotExist:
        return JsonResponse({'error': 'Campagne mère introuvable'}, status=404)

    visuels = [
        {
            'id': v.pk,
            'url': v.fichier.url,
            'is_video': v.fichier.url.lower().endswith('.mp4'),
        }
        for v in parent.visuels.all()
    ]

    return JsonResponse({
        'date_debut': parent.date_debut.isoformat() if parent.date_debut else None,
        'date_fin': parent.date_fin.isoformat() if parent.date_fin else None,
        'visuels': visuels,
    })




class CampagneLancerView(StaffRequiredMixin, View):
    def post(self, request, pk):
        campagne = get_object_or_404(Campagne, pk=pk)
        campagne.auto_update_statut()
        if campagne.statut == Campagne.STATUT_BROUILLON:
            campagne.statut = Campagne.STATUT_A_VENIR
            campagne.save()
        log_action(request, AuditLog.ACTION_UPDATE, 'campaigns', obj=campagne, detail=f"Lancement campagne: {campagne.nom}")
        messages.success(request, f'Campagne "{campagne.nom}" lancée.')
        return redirect('campagne_detail', pk=pk)

    def get(self, request, pk):
        return redirect('campagne_detail', pk=pk)


class CampagneDeleteView(StaffRequiredMixin, DeleteView):
    model = Campagne
    template_name = 'partials/confirm_delete.html'

    def post(self, request, *args, **kwargs):
        campagne = Campagne.objects.filter(pk=kwargs.get('pk')).first()
        if campagne is None:
            messages.info(request, "Cette campagne a déjà été supprimée ou n'existe plus.")
            return redirect(self.get_success_url())

        self.object = campagne
        nom = str(campagne)
        deleted_count, _ = campagne.delete()
        if not deleted_count:
            messages.info(request, "Cette campagne a déjà été supprimée ou n'existe plus.")
            return redirect(self.get_success_url())

        log_action(
            request,
            AuditLog.ACTION_DELETE,
            'campaign',
            obj=campagne,
            detail=f"Suppression campagne: {nom}",
        )
        messages.success(request, f'Campagne "{nom}" supprimée.')
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('campagne_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': 'Supprimer la campagne',
            'header': 'Suppression de campagne',
            'message_title': 'Supprimer cette campagne ?',
            'message_body': 'Vous êtes sur le point de supprimer la campagne',
            'hint': 'Toutes les réservations et le planning liés à cette campagne seront supprimés.',
            'confirm_label': 'Supprimer la campagne',
            'cancel_url': reverse('campagne_detail', kwargs={'pk': self.object.pk}),
        })
        return context


class VisuelDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        visuel = get_object_or_404(CampagneVisuel, pk=pk)
        campagne_pk = visuel.campagne.pk
        visuel.fichier.delete(save=False)
        visuel.delete()
        messages.success(request, "Visuel supprimé avec succès.")
        return redirect('campagne_detail', pk=campagne_pk)

    def get(self, request, pk):
        return redirect('campagne_detail', pk=pk)


class PreuveDiffusionCreateView(StaffRequiredMixin, View):
    template_name = 'campaigns/pige_form.html'

    def get_campagne(self, campagne_pk):
        return get_object_or_404(Campagne, pk=campagne_pk)

    def get(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        form = PreuveDiffusionForm(campagne=campagne)
        preuves = campagne.preuves_diffusion.select_related(
            'ligne__support', 'ligne__face__support', 'ligne__emplacement__marche', 'cree_par'
        )
        return render(request, self.template_name, {
            'campagne': campagne,
            'form': form,
            'preuves': preuves,
        })

    def post(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        form = PreuveDiffusionForm(request.POST, request.FILES, campagne=campagne)
        if form.is_valid():
            preuve = form.save(commit=False)
            preuve.campagne = campagne
            preuve.cree_par = request.user
            preuve.save()
            messages.success(request, "La preuve de diffusion a été enregistrée.")
            return redirect('campagne_detail', pk=campagne.pk)

        preuves = campagne.preuves_diffusion.select_related(
            'ligne__support', 'ligne__face__support', 'ligne__emplacement__marche', 'cree_par'
        )
        return render(request, self.template_name, {
            'campagne': campagne,
            'form': form,
            'preuves': preuves,
        })


class PreuveDiffusionUpdateView(StaffRequiredMixin, View):
    template_name = 'campaigns/pige_form.html'

    def get_preuve(self, campagne_pk, preuve_pk):
        return get_object_or_404(
            PreuveDiffusion.objects.select_related('campagne', 'ligne'),
            pk=preuve_pk,
            campagne_id=campagne_pk,
        )

    def get(self, request, campagne_pk, preuve_pk):
        preuve = self.get_preuve(campagne_pk, preuve_pk)
        form = PreuveDiffusionForm(instance=preuve, campagne=preuve.campagne)
        return render(request, self.template_name, {
            'campagne': preuve.campagne,
            'form': form,
            'preuve_modifiee': preuve,
        })

    def post(self, request, campagne_pk, preuve_pk):
        preuve = self.get_preuve(campagne_pk, preuve_pk)
        ancienne_photo = preuve.photo
        form = PreuveDiffusionForm(
            request.POST,
            request.FILES,
            instance=preuve,
            campagne=preuve.campagne,
        )
        if form.is_valid():
            preuve = form.save()
            if ancienne_photo.name and ancienne_photo.name != preuve.photo.name:
                ancienne_photo.storage.delete(ancienne_photo.name)
            messages.success(request, "La preuve de diffusion a été modifiée.")
            return redirect('campagne_detail', pk=preuve.campagne_id)

        return render(request, self.template_name, {
            'campagne': preuve.campagne,
            'form': form,
            'preuve_modifiee': preuve,
        })


class PreuveDiffusionDeleteView(StaffRequiredMixin, View):
    template_name = 'partials/confirm_delete.html'

    def get_preuve(self, campagne_pk, preuve_pk):
        return get_object_or_404(
            PreuveDiffusion.objects.select_related(
                'campagne', 'ligne__support', 'ligne__face__support',
                'ligne__emplacement__marche',
            ),
            pk=preuve_pk,
            campagne_id=campagne_pk,
        )

    def get(self, request, campagne_pk, preuve_pk):
        preuve = self.get_preuve(campagne_pk, preuve_pk)
        return render(request, self.template_name, {
            'title': "Supprimer la preuve de diffusion",
            'header': "Suppression d'une pige",
            'message_title': "Supprimer cette preuve ?",
            'message_body': "Vous allez supprimer la preuve du support",
            'obj': preuve.ligne,
            'hint': "Le support redeviendra disponible dans la liste de pige. Cette action est définitive.",
            'confirm_label': "Supprimer la preuve",
            'cancel_url': reverse('campagne_detail', kwargs={'pk': preuve.campagne_id}) + '#piges',
        })

    def post(self, request, campagne_pk, preuve_pk):
        preuve = self.get_preuve(campagne_pk, preuve_pk)
        campagne_pk = preuve.campagne_id
        photo = preuve.photo
        preuve.delete()
        if photo:
            photo.delete(save=False)
        messages.success(request, "La preuve a été supprimée. Le support est de nouveau disponible pour une pige.")
        return redirect('campagne_detail', pk=campagne_pk)


class SupportBulkActionView(StaffRequiredMixin, View):
    template_name = 'campaigns/supports_add_bulk.html'

    def get_campagne(self, campagne_pk):
        return get_object_or_404(Campagne, pk=campagne_pk)

    def get(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = request.GET.get('type_support', campagne.type_support or '').strip()
        ville = request.GET.get('ville', campagne.lieu or '').strip()
        panel_search_query = request.GET.get('q', '').strip()
        panel_view = request.GET.get('view', 'list')
        if panel_view not in {'grid', 'list', 'map'}:
            panel_view = 'list'

        is_modification = campagne.lignes.exists()
        mode_title = "Modifier la sélection" if is_modification else "Ajouter des supports"
        campagne_categorie_geant = FormatSupport.hors_ecran().filter(
            Q(code=type_support) | Q(categorie__iexact=type_support),
            categorie__iexact='Géant',
        ).exists()
        supports_enrichis = []

        # Liste des villes disponibles, pour le select de filtre dans le template
        villes_disponibles = (
            Support.objects
            .exclude(ville__exact='')
            .values_list('ville', flat=True)
            .distinct()
            .order_by('ville')
        )

        if type_support == 'ecran':
            # Écrans : on filtre sur le type d'écran, éventuellement la ville.
            supports_qs = Support.objects.filter(
                type_support='ecran',
                etat__in=Support.ETATS_OPERATIONNELS
            )
            if ville:
                supports_qs = supports_qs.filter(ville=ville)
            supports_qs = supports_qs.order_by('ville', 'quartier')

            selectionnes = list(campagne.lignes.values_list('support_id', flat=True))

            # ✅ Récupérer les paramètres existants par écran pour pré-remplir le formulaire
            lignes_par_support = {
                str(ligne.support_id): ligne
                for ligne in campagne.lignes.filter(face__isnull=True)
            }

            for support in supports_qs:
                ligne_existante = lignes_par_support.get(str(support.pk))
                supports_enrichis.append({
                    'support': support,
                    'ligne': ligne_existante,
                })

        elif type_support == 'marche':
            # Marché : on sélectionne des EMPLACEMENTS (pas des supports physiques).
            # Un emplacement reste sélectionnable même sans panneau installé.
            emplacements_qs = Emplacement.objects.select_related('marche', 'support_installe').order_by('marche__nom', 'code')
            if ville:
                emplacements_qs = emplacements_qs.filter(marche__ville=ville)

            selectionnes = list(campagne.lignes.filter(emplacement__isnull=False).values_list('emplacement_id', flat=True))

            emplacements_enrichis = []
            for emplacement in emplacements_qs:
                emplacements_enrichis.append({
                    'emplacement': emplacement,
                    'marche': emplacement.marche,
                    'a_un_panneau': not emplacement.is_libre(),
                    'jours_dispo': emplacement.jours_disponibles_sur_periode(campagne.date_debut, campagne.date_fin),
                })

            return render(request, self.template_name, {
                'campagne': campagne,
                'mode_marche': True,
                'selectionnes': selectionnes,
                'mode_title': mode_title,
                'is_modification': is_modification,
                'emplacements_enrichis': emplacements_enrichis,
                'ville_filtree': ville,
                'villes_disponibles': (
                    Marche.objects.exclude(ville__exact='').values_list('ville', flat=True).distinct().order_by('ville')
                ),
            })

        else:
            # Les campagnes historiques peuvent encore porter le type générique
            # "panneau", tandis que les nouvelles campagnes stockent un code format.
            supports_qs = Support.objects.filter(
                type_support='panneau',
                format=type_support,
                etat__in=Support.ETATS_OPERATIONNELS
            )
            if type_support == Support.TYPE_PANNEAU:
                supports_qs = Support.objects.filter(
                    type_support=Support.TYPE_PANNEAU,
                    etat__in=Support.ETATS_OPERATIONNELS,
                )
            elif type_support == 'Autre':
                formats_categorie = FormatSupport.hors_ecran().filter(
                    Q(categorie__isnull=True) | Q(categorie=''),
                ).values('code')
                if formats_categorie.exists():
                    supports_qs = Support.objects.filter(
                        type_support=Support.TYPE_PANNEAU,
                        format__in=formats_categorie,
                        etat__in=Support.ETATS_OPERATIONNELS,
                    )
            elif FormatSupport.hors_ecran().filter(categorie__iexact=type_support).exists():
                formats_categorie = FormatSupport.hors_ecran().filter(
                    categorie__iexact=type_support,
                ).values('code')
                supports_qs = Support.objects.filter(
                    type_support=Support.TYPE_PANNEAU,
                    format__in=formats_categorie,
                    etat__in=Support.ETATS_OPERATIONNELS,
                )
            elif not FormatSupport.objects.filter(code=type_support).exists():
                formats_categorie = FormatSupport.objects.filter(
                    categorie__iexact=type_support,
                ).values('code')
                if formats_categorie.exists():
                    supports_qs = Support.objects.filter(
                        type_support=Support.TYPE_PANNEAU,
                        format__in=formats_categorie,
                        etat__in=Support.ETATS_OPERATIONNELS,
                    )
            villes_disponibles = (
                supports_qs.exclude(ville__exact='')
                .order_by()
                .values_list('ville', flat=True)
                .distinct()
                .order_by('ville')
            )
            if ville:
                supports_qs = supports_qs.filter(ville=ville)
            order_by = (
                ('ville', 'format', 'quartier', 'code')
                if campagne_categorie_geant
                else ('ville', 'quartier', 'format', 'code')
            )
            supports_qs = supports_qs.prefetch_related('faces').order_by(*order_by)
            formats_par_code = {
                format_support.code: format_support
                for format_support in FormatSupport.hors_ecran().filter(
                    code__in=supports_qs.values_list('format', flat=True)
                )
            }

            selectionnes = set(
                campagne.lignes.filter(face__isnull=False).values_list('face_id', flat=True)
            )
            pending_face_ids = request.GET.getlist('pending_faces')
            if pending_face_ids:
                selectionnes.update(
                    FacePanneau.objects.filter(pk__in=pending_face_ids)
                    .values_list('pk', flat=True)
                )

            for support in supports_qs:
                format_support = formats_par_code.get(support.format)
                support.format_display = (
                    f"{format_support.dimensions} — {format_support.code}"
                    if format_support and format_support.dimensions
                    else support.format
                )
                faces_enrichies = []
                for face in support.faces.all():
                    statut = face.get_statut(
                        date_debut=campagne.date_debut,
                        date_fin=campagne.date_fin,
                        client=campagne.client,
                    )
                    campagnes_occupees = []
                    if statut == 'occupe':
                        campagnes_occupees = list(
                            Campagne.objects
                            .filter(
                                lignes__face=face,
                                date_debut__lte=campagne.date_fin,
                                date_fin__gte=campagne.date_debut,
                                statut='en_cours',
                            )
                            .order_by('nom')
                            .distinct()
                        )
                    faces_enrichies.append({
                        'face': face,
                        'statut': statut,
                        'campagnes_occupees': campagnes_occupees,
                    })
                supports_enrichis.append({
                    'support': support,
                    'faces': faces_enrichies,
                })

        return render(request, self.template_name, {
            'campagne': campagne,
            'supports': supports_qs,
            'selectionnes': selectionnes,
            'mode_title': mode_title,
            'is_modification': is_modification,
            'supports_enrichis': supports_enrichis,
            'duree_choices': DUREE_CHOICES,
            'frequence_choices': FREQUENCE_CHOICES,
            # ✅ Nouveau : contexte pour le filtre ville
            'ville_filtree': ville,
            'villes_disponibles': villes_disponibles,
            'panel_search_query': panel_search_query,
            'panel_view': panel_view,
            'campagne_categorie_geant': campagne_categorie_geant,
        })

    def post(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = campagne.type_support

        if type_support == 'marche':
            emplacement_ids = request.POST.getlist('emplacements')

            campagne.lignes.filter(emplacement__isnull=False).exclude(emplacement_id__in=emplacement_ids).delete()

            for emplacement_id in emplacement_ids:
                emplacement = get_object_or_404(Emplacement, pk=emplacement_id)
                ligne, created = LigneCampagne.objects.get_or_create(
                    campagne=campagne,
                    emplacement=emplacement,
                )
                if created:
                    log_action(request, AuditLog.ACTION_UPDATE, 'campaign', obj=campagne,
                               detail=f"Ajout emplacement {emplacement.marche.nom} — {emplacement.code}")

            messages.success(request, "Mise à jour des emplacements effectuée.")
            return redirect('campagne_detail', pk=campagne_pk)

        if type_support != 'ecran':
            face_ids = set(request.POST.getlist('pending_faces'))
            face_ids.update(request.POST.getlist('faces'))
            ville = request.GET.get('ville', '').strip()
            lignes_faces = campagne.lignes.filter(face__isnull=False)
            if ville:
                face_ids.update(
                    lignes_faces.exclude(face__support__ville=ville)
                    .values_list('face_id', flat=True)
                )
            lignes_faces.exclude(face_id__in=face_ids).delete()

            for face_id in face_ids:
                face = get_object_or_404(FacePanneau, pk=face_id)
                ligne, created = LigneCampagne.objects.get_or_create(
                    campagne=campagne,
                    support=face.support,
                    face=face,
                )
                if created:
                    log_action(request, AuditLog.ACTION_UPDATE, 'campaign', obj=campagne,
                               detail=f"Ajout panneau {face.support.code} face {face.label}")

        else:
            support_ids = request.POST.getlist('supports')

            campagne.lignes.filter(face__isnull=True).exclude(support_id__in=support_ids).delete()

            for support_id in support_ids:
                support = get_object_or_404(Support, pk=support_id, type_support='ecran')

                date_debut_str      = request.POST.get(f'date_debut_{support_id}', '').strip()
                date_fin_str        = request.POST.get(f'date_fin_{support_id}', '').strip()
                duree_passage_str   = request.POST.get(f'duree_passage_{support_id}', '').strip()
                frequence_str       = request.POST.get(f'frequence_{support_id}', '').strip()
                tranches_horaires   = request.POST.get(f'tranches_horaires_{support_id}', '').strip()

                date_debut    = None
                date_fin      = None
                duree_passage = None
                frequence     = None

                try:
                    if date_debut_str:
                        date_debut = date.fromisoformat(date_debut_str)
                    if date_fin_str:
                        date_fin = date.fromisoformat(date_fin_str)
                    if duree_passage_str:
                        duree_passage = int(duree_passage_str)
                    if frequence_str:
                        frequence = int(frequence_str)
                except (ValueError, TypeError):
                    messages.warning(
                        request,
                        f"Paramètres invalides pour l'écran {support.code}, valeurs par défaut utilisées."
                    )

                ligne, created = LigneCampagne.objects.update_or_create(
                    campagne=campagne,
                    support=support,
                    defaults={
                        'ordre_dans_boucle': 0,
                        'date_debut':       date_debut,
                        'date_fin':         date_fin,
                        'duree_passage':    duree_passage,
                        'frequence':        frequence,
                        'tranches_horaires': tranches_horaires or '',
                    }
                )

                if created:
                    log_action(request, AuditLog.ACTION_UPDATE, 'campaign', obj=campagne,
                               detail=f"Ajout écran {support.code}")
                else:
                    log_action(request, AuditLog.ACTION_UPDATE, 'campaign', obj=campagne,
                               detail=f"Mise à jour écran {support.code}")

        messages.success(request, "Mise à jour des supports effectuée.")
        return redirect('campagne_detail', pk=campagne_pk)


# ── API JSON ──────────────────────────────────────────────────────────────────

class ApiCheckDisponibiliteView(LoginRequiredMixin, View):
    def get(self, request):
        face_pk = request.GET.get('face')
        ecran_pk = request.GET.get('ecran')
        date_debut = request.GET.get('date_debut')
        date_fin = request.GET.get('date_fin')
        duree = request.GET.get('duree', 10)

        import datetime
        try:
            d1 = datetime.date.fromisoformat(date_debut)
            d2 = datetime.date.fromisoformat(date_fin)
        except Exception:
            return JsonResponse({'error': 'Dates invalides'}, status=400)

        if face_pk:
            face = get_object_or_404(FacePanneau, pk=face_pk)
            dispo = face.is_disponible(d1, d2)
            dispos = face.is_disponibles(d1, d2)
            collisions = face.get_campagne_active()
            return JsonResponse({
                'disponible': dispo,
                'disponibles': dispos,
                'campagne_active': str(collisions) if collisions else None,
            })

        if ecran_pk:
            tranches = request.GET.get('tranches')
            ecran = get_object_or_404(EcranNumerique, pk=ecran_pk)
            peut = ecran.peut_accepter_spot(int(duree), d1, d2, tranches)
            return JsonResponse({
                'disponible': peut,
                'secondes_libres': ecran.secondes_disponibles(d1, d2, tranches),
                'taux_occupation': ecran.taux_occupation(d1, d2, tranches),
            })

        return JsonResponse({'error': 'Paramètres manquants'}, status=400)

# class ReservationCreateView(StaffRequiredMixin, View):
#     def post(self, request):
#         campagne_pk = request.POST.get('campagne')
#         support_pk = request.POST.get('support')
#         face_pk = request.POST.get('face')
#         date_debut = request.POST.get('date_debut')
#         date_fin = request.POST.get('date_fin')

#         campagne = get_object_or_404(Campagne, pk=campagne_pk)
#         support = get_object_or_404(Support, pk=support_pk)

#         if face_pk:
#             face = get_object_or_404(FacePanneau, pk=face_pk)
#             ligne, created = LigneCampagne.objects.get_or_create(
#                 campagne=campagne,
#                 support=support,
#                 face=face,
#             )
#             if created:
#                 log_action(request, AuditLog.ACTION_CREATE, 'reservation', obj=ligne, 
#                            detail=f"Réservation panneau {support.code} face {face.label} du {date_debut} au {date_fin}")
#             return JsonResponse({'success': True})
        
#         # Pour les écrans, on peut créer une réservation générique sans face
#         ligne, created = LigneCampagne.objects.get_or_create(
#             campagne=campagne,
#             support=support,
#             defaults={'ordre_dans_boucle': 0}
#         )
#         if created:
#             log_action(request, AuditLog.ACTION_CREATE, 'reservation', obj=ligne, 
#                        detail=f"Réservation écran {support.code} du {date_debut} au {date_fin}")
#         return JsonResponse({'success': True})
    
    



from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError





def _parse_aware_datetime(value: str):
    """
    Parse une chaîne ISO (ex: "2025-08-01T08:00") en datetime aware.
    Gère les cas naive et already-aware (avec offset).
    Lève ValueError si le format est invalide.
    """
    dt = timezone.datetime.fromisoformat(value)
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt)


def _parse_dates(date_debut_str: str, date_fin_str: str):
    """
    Valide et convertit les deux chaînes de date.
    Retourne (date_debut, date_fin) ou lève ValidationError.
    """
    if not date_debut_str or not date_fin_str:
        raise ValidationError("Les dates de début et de fin sont obligatoires.")
    try:
        date_debut = _parse_aware_datetime(date_debut_str)
        date_fin   = _parse_aware_datetime(date_fin_str)
    except ValueError:
        raise ValidationError("Format de date invalide. Attendu : YYYY-MM-DDTHH:MM.")
    if date_fin <= date_debut:
        raise ValidationError("La date de fin doit être strictement postérieure à la date de début.")
    if (date_fin - date_debut).days > 366:
        raise ValidationError("La période de réservation ne peut pas dépasser 1 an.")
    return date_debut, date_fin


def _get_supports():
    """Retourne les panneaux en bon état avec leurs faces."""
    return (
        Support.objects
        .filter(type_support='panneau', etat__in=Support.ETATS_OPERATIONNELS)
        .prefetch_related('faces')
        .order_by('code')
    )


# ── Vue Création ──────────────────────────────────────────────────────────────

class ReservationCreateView(StaffRequiredMixin, View):
    """
    Crée une nouvelle réservation (Reservation + ReservationLigne) pour un client.

    URL : /clients/<client_pk>/reservations/create/
    Name: reservation_create
    """
    template_name = 'campaigns/reservations_add_bulk_final.html'

    def get_client(self, client_pk):
        return get_object_or_404(Client, pk=client_pk)

    def get(self, request, client_pk):
        client   = self.get_client(client_pk)
        now      = timezone.now()
        supports = _get_supports()

        # Formats présents parmi les supports disponibles uniquement
        format_choices_map = dict(FormatSupport.objects.values_list('code', 'dimensions'))
        codes_uniques = (
            Support.objects
            .exclude(format__isnull=True)
            .exclude(format='')
            .values_list('format', flat=True)
            .distinct()
        )
        type_panneau_choices = list(dict.fromkeys(
            (code, format_choices_map[code])
            for code in codes_uniques
            if code in format_choices_map
        ))
        format_sort_map = {
            format_support.code: format_support
            for format_support in FormatSupport.objects.filter(
                code__in=[code for code, _ in type_panneau_choices]
            )
        }
        type_panneau_choices.sort(
            key=lambda choice: format_sort_key(
                format_sort_map.get(choice[0]) or choice[0]
            )
        )
        print("type_panneau_choices:", type_panneau_choices)

        return render(request, self.template_name, {
            'client':               client,
            'supports':             supports,
            'selectionnes':         [],
            'clt':                  Client.objects.none(),
            'mode_title':           'Créer une réservation',
            'is_modification':      False,
            'date_debut':           '',
            'date_fin':             '',
            'today_iso':            now.strftime('%Y-%m-%dT%H:%M'),
            'type_panneau_choices': type_panneau_choices,
        })

    def post(self, request, client_pk):
        client = self.get_client(client_pk)

        # ── Lecture des données formulaire ────────────────────────────────────
        face_ids        = [int(fid) for fid in request.POST.getlist('faces') if fid.isdigit()]
        date_debut_str  = request.POST.get('date_debut', '').strip()
        date_fin_str    = request.POST.get('date_fin',   '').strip()
        nom_reservation = request.POST.get('nom_reservation', '').strip() or \
                          f"Réservation {timezone.now():%d/%m/%Y}"

        # ── Validation ────────────────────────────────────────────────────────
        try:
            date_debut, date_fin = _parse_dates(date_debut_str, date_fin_str)
        except ValidationError as e:
            messages.error(request, e.message)
            return redirect(request.path)

        if not face_ids:
            messages.error(request, "Veuillez sélectionner au moins une face.")
            return redirect(request.path)

        # ── Validation de toutes les faces AVANT toute écriture ───────────────
        # NOTE : on ne peut pas appeler full_clean() sur ReservationLigne avec une
        # Reservation non sauvegardée (le clean() fait un .exclude(reservation=self.reservation)
        # qui échoue si l'objet n'a pas de PK). On duplique donc la logique ici.
        faces_objects = (
            FacePanneau.objects
            .filter(id__in=face_ids)
            .select_related('support')
        )
        nouvelles_lignes = []
        erreurs = []

        for face in faces_objects:
            # 1. Type de support
            if face.support.type_support != 'panneau':
                erreurs.append(
                    f"Panneau {face.support.code} — Face {face.label} : "
                    f"les réservations sont uniquement applicables aux panneaux statiques."
                )
                continue

            # 2. Cohérence face / support
            if face.support_id != face.support.pk:
                erreurs.append(
                    f"Panneau {face.support.code} — Face {face.label} : "
                    f"la face n'appartient pas au support choisi."
                )
                continue

            # 3. Chevauchement avec une réservation existante
            conflit_resa = (
                ReservationLigne.objects.filter(
                    face=face,
                    reservation__date_debut__lt=date_fin,
                    reservation__date_fin__gt=date_debut,
                    reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                )
                .select_related('reservation__client')
                .first()
            )
            if conflit_resa:
                r = conflit_resa.reservation
                erreurs.append(
                    f"Panneau {face.support.code} — Face {face.label} : "
                    f"déjà réservée du {r.date_debut:%d/%m/%Y} au {r.date_fin:%d/%m/%Y} "
                    f"(client : {r.client.nom})."
                )
                continue

            # 4. Chevauchement avec une campagne active
            from campaigns.models import LigneCampagne
            conflit_camp = (
                LigneCampagne.objects.filter(
                    face=face,
                    campagne__date_debut__lte=date_fin.date(),
                    campagne__date_fin__gte=date_debut.date(),
                )
                .select_related('campagne__client')
                .first()
            )
            if conflit_camp:
                c = conflit_camp.campagne
                erreurs.append(
                    f"Panneau {face.support.code} — Face {face.label} : "
                    f"occupée par la campagne « {c.nom} » "
                    f"(client : {c.client.nom})."
                )
                continue

            nouvelles_lignes.append(ReservationLigne(
                support = face.support,
                face    = face,
                # reservation sera assignée après création en BDD
            ))

        if erreurs:
            for err in erreurs:
                messages.error(request, err)
            return redirect(request.path)

        # ── Écriture atomique ─────────────────────────────────────────────────
        try:
            with transaction.atomic():
                reservation = Reservation.objects.create(
                    client     = client,
                    nom        = nom_reservation,
                    date_debut = date_debut,
                    date_fin   = date_fin,
                    statut     = STATUT_EN_ATTENTE,
                    created_by = request.user if request.user.is_authenticated else None,
                )
                for ligne in nouvelles_lignes:
                    ligne.reservation = reservation

                ReservationLigne.objects.bulk_create(nouvelles_lignes)

        except Exception as e:
            messages.error(request, f"Erreur lors de la création : {str(e)}")
            return redirect(request.path)

        messages.success(
            request,
            f"Réservation « {nom_reservation} » créée avec {len(nouvelles_lignes)} face(s)."
        )
        return redirect('client_detail', pk=client_pk)


# ── Vue Modification ──────────────────────────────────────────────────────────

class ReservationUpdateView(StaffRequiredMixin, View):
    """
    Modifie une réservation existante (dates + faces sélectionnées).

    URL : /clients/<client_pk>/reservations/<resa_pk>/update/
    Name: reservation_update
    """
    template_name = 'campaigns/reservations_add_bulk_final.html'

    def get_client(self, client_pk):
        return get_object_or_404(Client, pk=client_pk)

    def get_reservation(self, client, resa_pk):
        return get_object_or_404(
            Reservation,
            pk     = resa_pk,
            client = client,
            statut__in = [STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
        )

    def get(self, request, client_pk, resa_pk):
        client      = self.get_client(client_pk)
        reservation = self.get_reservation(client, resa_pk)
        now         = timezone.now()
        supports    = _get_supports()
        # Formats présents parmi les supports disponibles uniquement
        format_choices_map = dict(FormatSupport.objects.values_list('code', 'dimensions'))
        codes_uniques = (
            Support.objects
            .exclude(format__isnull=True)
            .exclude(format='')
            .values_list('format', flat=True)
            .distinct()
        )
        type_panneau_choices = list(dict.fromkeys(
            (code, format_choices_map[code])
            for code in codes_uniques
            if code in format_choices_map
        ))
        format_sort_map = {
            format_support.code: format_support
            for format_support in FormatSupport.objects.filter(
                code__in=[code for code, _ in type_panneau_choices]
            )
        }
        type_panneau_choices.sort(
            key=lambda choice: format_sort_key(
                format_sort_map.get(choice[0]) or choice[0]
            )
        )
        print("type_panneau_choices:", type_panneau_choices)
        # Faces déjà dans cette réservation
        selectionnes = list(
            reservation.lignes.values_list('face_id', flat=True)
        )

        # Clients avec des faces qui chevauchent
        clt = Client.objects.filter(
            reservations_globales__lignes__face_id__in=selectionnes,
            reservations_globales__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
        ).exclude(pk=client.pk).distinct()

        return render(request, self.template_name, {
            'client':          client,
            'reservation':     reservation,
            'supports':        supports,
            'selectionnes':    selectionnes,
            'clt':             clt,
            'mode_title':      f'Modifier — {reservation.nom}',
            'is_modification': True,
            'date_debut':      reservation.date_debut.strftime('%Y-%m-%dT%H:%M'),
            'date_fin':        reservation.date_fin.strftime('%Y-%m-%dT%H:%M'),
            'today_iso':       now.strftime('%Y-%m-%dT%H:%M'),
            'type_panneau_choices': type_panneau_choices,
        })

    def post(self, request, client_pk, resa_pk):
        client      = self.get_client(client_pk)
        reservation = self.get_reservation(client, resa_pk)

        # ── Lecture des données formulaire ────────────────────────────────────
        face_ids       = [int(fid) for fid in request.POST.getlist('faces') if fid.isdigit()]
        date_debut_str = request.POST.get('date_debut', '').strip()
        date_fin_str   = request.POST.get('date_fin',   '').strip()

        # ── Validation des dates ──────────────────────────────────────────────
        try:
            date_debut, date_fin = _parse_dates(date_debut_str, date_fin_str)
        except ValidationError as e:
            messages.error(request, e.message)
            return redirect(request.path)

        if not face_ids:
            messages.error(request, "Veuillez sélectionner au moins une face.")
            return redirect(request.path)

        # ── Calcul des différences ────────────────────────────────────────────
        faces_actuelles = set(reservation.lignes.values_list('face_id', flat=True))
        faces_selectionnees = set(face_ids)
        faces_a_ajouter  = faces_selectionnees - faces_actuelles
        faces_a_supprimer = faces_actuelles - faces_selectionnees

        # ── Validation des nouvelles faces AVANT toute écriture ───────────────
        nouvelles_lignes = []
        erreurs = []

        if faces_a_ajouter:
            faces_objects = (
                FacePanneau.objects
                .filter(id__in=faces_a_ajouter)
                .select_related('support')
            )
            for face in faces_objects:
                ligne = ReservationLigne(
                    reservation = reservation,
                    support     = face.support,
                    face        = face,
                )
                try:
                    ligne.full_clean()
                    nouvelles_lignes.append(ligne)
                except ValidationError as e:
                    erreurs.append(
                        f"Panneau {face.support.code} — Face {face.label} : {e.messages[0]}"
                    )

        if erreurs:
            for err in erreurs:
                messages.error(request, err)
            return redirect(request.path)

        # ── Écriture atomique ─────────────────────────────────────────────────
        try:
            with transaction.atomic():
                # Mise à jour des dates de la réservation
                reservation.date_debut = date_debut
                reservation.date_fin   = date_fin
                reservation.save(update_fields=['date_debut', 'date_fin', 'updated_at'])

                # Suppression des faces décochées
                nb_supprimees = 0
                if faces_a_supprimer:
                    deleted, _ = reservation.lignes.filter(
                        face_id__in=faces_a_supprimer
                    ).delete()
                    nb_supprimees = deleted

                # Ajout des nouvelles faces
                if nouvelles_lignes:
                    ReservationLigne.objects.bulk_create(nouvelles_lignes)

        except Exception as e:
            messages.error(request, f"Erreur lors de la mise à jour : {str(e)}")
            return redirect(request.path)

        # ── Message récapitulatif ─────────────────────────────────────────────
        parties = []
        if nouvelles_lignes:
            parties.append(f"{len(nouvelles_lignes)} ajoutée(s)")
        if nb_supprimees:
            parties.append(f"{nb_supprimees} supprimée(s)")
        if not parties:
            parties.append("dates mises à jour")

        messages.success(
            request,
            f"Réservation « {reservation.nom} » modifiée — {' · '.join(parties)}."
        )
        return redirect('client_detail', pk=client_pk)


class ReservationDeleteView(StaffRequiredMixin, DeleteView):
    model = Reservation
    pk_url_kwarg = 'resa_pk'
    template_name = 'partials/confirm_delete.html'
    context_object_name = 'reservation'

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(client_id=self.kwargs.get('client_pk'))

    def get_success_url(self):
        return reverse('client_detail', kwargs={'pk': self.object.client.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': f'Supprimer la réservation {self.object.reference}',
            'message_title': f'Supprimer la réservation « {self.object.nom} » ?', 
            'message_body': 'Cette action supprimera la réservation et toutes ses lignes.',
            'confirm_label': 'Supprimer la réservation',
            'cancel_url': reverse('client_detail', kwargs={'pk': self.object.client.pk}),
            'obj': self.object,
        })
        return context


class ReservationDetailView(ClientStaffRequiredMixin, DetailView):
    model = Reservation
    pk_url_kwarg = 'resa_pk'
    template_name = 'campaigns/reservation_detail.html'
    context_object_name = 'reservation'

    def get_queryset(self):
        qs = super().get_queryset().select_related('client', 'created_by').prefetch_related('lignes__support', 'lignes__face')
        return qs.filter(client_id=self.kwargs.get('client_pk'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reservation = self.object
        context['page_title'] = f'Détail réservation — {reservation.nom}'
        context['can_edit'] = self.request.user.is_staff_regie_role
        context['can_delete'] = self.request.user.is_staff_regie_role
        return context



# ══════════════════════════════════════════════════════════════════════════════
# Vue unique : Valider OU Annuler une réservation, sur le même écran.
# Remplace ReservationValiderView + ReservationAnnulerView.
# ══════════════════════════════════════════════════════════════════════════════

class ReservationTraiterView(ClientStaffRequiredMixin, View):
    """
    Écran unique pour traiter une réservation : Valider (EN_ATTENTE → CONFIRMEE)
    ou Annuler (EN_ATTENTE/CONFIRMEE → ANNULEE).

    Règles :
      - Valider : uniquement depuis EN_ATTENTE, et request.user doit être
        différent de reservation.created_by (double contrôle).
      - Annuler : depuis EN_ATTENTE ou CONFIRMEE, sans restriction d'auteur.

    GET  : affiche le récapitulatif + les deux actions possibles (grisées
           si non autorisées, avec le motif du blocage).
    POST : `action=valider` ou `action=annuler` dans le POST détermine
           quelle opération est exécutée.

    URL : /clients/<client_pk>/reservations/<resa_pk>/traiter/
    Name: reservation_traiter
    """
    template_name = 'campaigns/reservation_traiter.html'

    def get_reservation(self, client_pk, resa_pk):
        return get_object_or_404(
            Reservation.objects.select_related('client', 'created_by'),
            pk=resa_pk,
            client_id=client_pk,
        )

    def _check_valider(self, request, reservation):
        """Retourne un message d'erreur si la validation est refusée, sinon None."""
        if reservation.statut != STATUT_EN_ATTENTE:
            return (
                f"Cette réservation est au statut « {reservation.get_statut_display()} » "
                f"et ne peut plus être validée."
            )
        if reservation.created_by_id and reservation.created_by_id == request.user.id:
            return (
                "Vous êtes à l'origine de cette réservation. Un autre membre de "
                "l'équipe doit la valider (principe de double contrôle)."
            )
        return None

    def _check_annuler(self, reservation):
        """Retourne un message d'erreur si l'annulation est refusée, sinon None."""
        if reservation.statut not in (STATUT_EN_ATTENTE, STATUT_CONFIRMEE):
            return (
                f"Cette réservation est au statut « {reservation.get_statut_display()} » "
                f"et ne peut plus être annulée."
            )
        return None

    # ── GET ──────────────────────────────────────────────────────────────────
    def get(self, request, client_pk, resa_pk):
        reservation = self.get_reservation(client_pk, resa_pk)

        erreur_valider = self._check_valider(request, reservation)
        erreur_annuler = self._check_annuler(reservation)

        if erreur_valider and erreur_annuler:
            messages.error(request, "Cette réservation ne peut plus être ni validée ni annulée.")
            return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

        return render(request, self.template_name, {
            'reservation':     reservation,
            'client':          reservation.client,
            'peut_valider':    erreur_valider is None,
            'erreur_valider':  erreur_valider,
            'peut_annuler':    erreur_annuler is None,
            'erreur_annuler':  erreur_annuler,
        })

    # ── POST ─────────────────────────────────────────────────────────────────
    def post(self, request, client_pk, resa_pk):
        reservation = self.get_reservation(client_pk, resa_pk)
        action = request.POST.get('action')

        if action == 'valider':
            return self._traiter_validation(request, reservation, client_pk, resa_pk)
        elif action == 'annuler':
            return self._traiter_annulation(request, reservation, client_pk, resa_pk)

        messages.error(request, "Action inconnue.")
        return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

    # ── Validation ───────────────────────────────────────────────────────────
    def _traiter_validation(self, request, reservation, client_pk, resa_pk):
        erreur = self._check_valider(request, reservation)
        if erreur:
            messages.error(request, erreur)
            return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

        try:
            with transaction.atomic():
                # Verrou + re-vérification pour éviter toute course concurrente
                reservation = (
                    Reservation.objects
                    .select_for_update()
                    .select_related('created_by', 'client')
                    .get(pk=reservation.pk)
                )
                erreur = self._check_valider(request, reservation)
                if erreur:
                    messages.error(request, erreur)
                    return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

                reservation.statut = STATUT_CONFIRMEE
                reservation.save(update_fields=['statut', 'updated_at'])

        except Exception as e:
            messages.error(request, f"Erreur lors de la validation : {str(e)}")
            return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

        messages.success(
            request,
            f"Réservation « {reservation.nom} » validée avec succès par {request.user}."
        )

        self._send_emails_validation(reservation, request)

        return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

    # ── Annulation ───────────────────────────────────────────────────────────
    def _traiter_annulation(self, request, reservation, client_pk, resa_pk):
        erreur = self._check_annuler(reservation)
        if erreur:
            messages.error(request, erreur)
            return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

        motif = request.POST.get('motif', '').strip()

        try:
            with transaction.atomic():
                reservation = (
                    Reservation.objects
                    .select_for_update()
                    .select_related('client')
                    .get(pk=reservation.pk)
                )
                erreur = self._check_annuler(reservation)
                if erreur:
                    messages.error(request, erreur)
                    return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

                reservation.statut = STATUT_ANNULEE
                update_fields = ['statut', 'updated_at']

                if motif:
                    horodatage = timezone.now().strftime('%d/%m/%Y %H:%M')
                    note = f"[Annulation {horodatage} par {request.user}] {motif}"
                    reservation.notes = f"{reservation.notes}\n{note}".strip() if reservation.notes else note
                    update_fields.append('notes')

                reservation.save(update_fields=update_fields)

        except Exception as e:
            messages.error(request, f"Erreur lors de l'annulation : {str(e)}")
            return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

        messages.success(request, f"Réservation « {reservation.nom} » annulée.")

        self._send_emails_annulation(reservation, motif, request)

        return redirect('reservation_detail', client_pk=client_pk, resa_pk=resa_pk)

    # ── Emails : validation ─────────────────────────────────────────────────
    def _send_emails_validation(self, reservation, request):
        client = reservation.client

        # ── Client ──
        if client and client.email:
            try:
                send_mail(
                    subject=f"Votre réservation « {reservation.nom} » est confirmée",
                    message=(
                        f"Bonjour {client.nom},\n\n"
                        f"Nous avons le plaisir de vous confirmer que votre réservation "
                        f"« {reservation.nom} » a été validée.\n\n"
                        f"Référence  : {reservation.reference}\n"
                        f"Période    : {reservation.date_debut:%d/%m/%Y} → {reservation.date_fin:%d/%m/%Y}\n\n"
                        f"Notre équipe reste à votre disposition pour toute question.\n\n"
                        f"Cordialement,\nL'équipe régie publicitaire"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[client.email],
                    fail_silently=True,
                )
            except Exception as exc:
                logger.error("Email client (validation résa) échoué pour %s : %s", reservation.reference, exc)

        # ── Staff ──
        try:
            send_mail(
                subject=f"[GeoAd] Réservation validée — {reservation.reference}",
                message=(
                    f"Réservation validée : {reservation.reference}\n\n"
                    f"Nom       : {reservation.nom}\n"
                    f"Client    : {client.nom if client else '—'} ({client.email if client else '—'})\n"
                    f"Validée par : {request.user.get_full_name() or request.user.username}\n\n"
                    f"Période   : {reservation.date_debut:%d/%m/%Y} → {reservation.date_fin:%d/%m/%Y}\n\n"
                    f"Voir : {getattr(settings, 'SITE_URL', '')}/clients/{client.pk if client else ''}/reservations/{reservation.pk}/"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.CONTACT_EMAIL1, settings.CONTACT_EMAIL2],
                fail_silently=True,
            )
        except Exception as exc:
            logger.error("Email staff (validation résa) échoué pour %s : %s", reservation.reference, exc)

    # ── Emails : annulation ──────────────────────────────────────────────────
    def _send_emails_annulation(self, reservation, motif, request):
        client = reservation.client

        # ── Client ──
        if client and client.email:
            try:
                send_mail(
                    subject=f"Votre réservation « {reservation.nom} » a été annulée",
                    message=(
                        f"Bonjour {client.nom},\n\n"
                        f"Nous vous informons que votre réservation « {reservation.nom} » "
                        f"(référence {reservation.reference}) a été annulée.\n\n"
                        + (f"Motif : {motif}\n\n" if motif else "\n")
                        + f"N'hésitez pas à nous recontacter pour toute question.\n\n"
                        f"Cordialement,\nL'équipe régie publicitaire"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[client.email],
                    fail_silently=True,
                )
            except Exception as exc:
                logger.error("Email client (annulation résa) échoué pour %s : %s", reservation.reference, exc)

        # ── Staff ──
        try:
            send_mail(
                subject=f"[GeoAd] Réservation annulée — {reservation.reference}",
                message=(
                    f"Réservation annulée : {reservation.reference}\n\n"
                    f"Nom       : {reservation.nom}\n"
                    f"Client    : {client.nom if client else '—'} ({client.email if client else '—'})\n"
                    f"Annulée par : {request.user.get_full_name() or request.user.username}\n\n"
                    f"Motif : {motif or '—'}\n\n"
                    f"Voir : {getattr(settings, 'SITE_URL', '')}/clients/{client.pk if client else ''}/reservations/{reservation.pk}/"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.CONTACT_EMAIL1, settings.CONTACT_EMAIL2],
                fail_silently=True,
            )
        except Exception as exc:
            logger.error("Email staff (annulation résa) échoué pour %s : %s", reservation.reference, exc)
    
    
# ══════════════════════════════════════════════════════════════════════════════
# Câblage urls.py — remplace les deux routes reservation_valider/reservation_annuler
# ══════════════════════════════════════════════════════════════════════════════
#
# path(
#     'clients/<int:client_pk>/reservations/<int:resa_pk>/traiter/',
#     ReservationTraiterView.as_view(),
#     name='reservation_traiter',
# ),
#
# ══════════════════════════════════════════════════════════════════════════════
# Mise à jour suggérée de ReservationDetailView.get_context_data
# ══════════════════════════════════════════════════════════════════════════════
#
# def get_context_data(self, **kwargs):
#     context = super().get_context_data(**kwargs)
#     reservation = self.object
#     context['page_title']  = f'Détail réservation — {reservation.nom}'
#     context['can_edit']    = self.request.user.is_staff_regie_role
#     context['can_delete']  = self.request.user.is_staff_regie_role
#     context['can_traiter'] = (
#         self.request.user.is_staff_regie_role
#         and reservation.statut in (STATUT_EN_ATTENTE, STATUT_CONFIRMEE)
#     )
#     return context
#
# Dans reservation_detail.html, un seul bouton suffit alors :
# {% if can_traiter %}
#   <a href="{% url 'reservation_traiter' reservation.client.pk reservation.pk %}" class="btn btn-primary">
#     <i class="bi bi-check-circle me-1"></i>Valider / Annuler
#   </a>
# {% endif %}





from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.shortcuts import redirect, get_object_or_404

from .models import Reservation


class ReservationRedirectView(LoginRequiredMixin, View):
    """
    Raccourci d'accès à une réservation à partir de son seul ID.
    Redirige vers l'URL canonique 'reservation_detail' (imbriquée sous le
    client), sans avoir besoin de connaître client_pk à l'endroit d'où
    on linke (ex: depuis une DemandeReservation).
    """
    def get(self, request, pk):
        reservation = get_object_or_404(Reservation, pk=pk)
        return redirect('reservation_detail', client_pk=reservation.client_id, resa_pk=reservation.pk)
    
class ReservationListView(StaffRequiredMixin, SortableListMixin, ListView):
    model = Reservation
    template_name = 'campaigns/reservation_list.html'
    context_object_name = 'reservations'
    paginate_by = 20
    SORT_FIELDS = {
        'nom': 'nom',
        'client': 'client__nom',
        'date_debut': 'date_debut',
        'statut': 'statut',
        'created_by': 'created_by__username',
    }
    DEFAULT_SORT = 'date_debut'
    DEFAULT_DIR = 'desc'

    def get_queryset(self):
        qs = Reservation.objects.select_related('client', 'created_by').prefetch_related('lignes__face__support')

        q        = self.request.GET.get('q', '').strip()
        statut_f = self.request.GET.get('statut', 'a_suivre')
        client_f = self.request.GET.get('client', '')
        date_f   = self.request.GET.get('date', '')

        if q:
            qs = qs.filter(
                Q(nom__icontains=q) |
                Q(client__nom__icontains=q)
            )
        if statut_f == 'a_suivre':
            qs = qs.filter(statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE])
        elif statut_f:
            qs = qs.filter(statut=statut_f)
        if client_f:
            qs = qs.filter(client__pk=client_f)
        if date_f:
            qs = qs.filter(date_debut__date=date_f)

        return self.apply_sort(qs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        statut_tab_urls = {}
        for statut_tab in ('a_suivre', STATUT_EN_ATTENTE, STATUT_CONFIRMEE, STATUT_ANNULEE, STATUT_EXPIREE):
            tab_params = query_params.copy()
            tab_params['statut'] = statut_tab
            statut_tab_urls[statut_tab] = f"?{tab_params.urlencode()}"

        context.update({
            'q':        self.request.GET.get('q', ''),
            'statut_f': self.request.GET.get('statut', 'a_suivre'),
            'client_f': self.request.GET.get('client', ''),
            'date_f':   self.request.GET.get('date', ''),
            'statut_counts': {
                'a_suivre': Reservation.objects.filter(
                    statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE]
                ).count(),
                'en_attente': Reservation.objects.filter(statut=STATUT_EN_ATTENTE).count(),
                'confirmee': Reservation.objects.filter(statut=STATUT_CONFIRMEE).count(),
                'annulee': Reservation.objects.filter(statut=STATUT_ANNULEE).count(),
                'expiree': Reservation.objects.filter(statut=STATUT_EXPIREE).count(),
            },
            'statut_tab_urls': statut_tab_urls,
            'statut_choices': [
                ('a_suivre', 'À suivre'),
                (STATUT_EN_ATTENTE, 'En attente'),
                (STATUT_CONFIRMEE,  'Confirmée'),
                (STATUT_ANNULEE,    'Annulée'),
                (STATUT_EXPIREE,    'Expirée'),
            ],
            'clients': Client.objects.filter(
                reservations_globales__isnull=False
            ).distinct().order_by('nom'),
        })
        return context


class ReservationSelectClientView(StaffRequiredMixin, View):
    """
    Petite vue pour sélectionner un client (utilisée dans un modal).
    GET: rend un fragment contenant un formulaire de sélection de client.
    POST: redirige vers la création de réservation pour le client choisi.
    URL: /reservations/select-client/
    Name: reservation_select_client
    """
    template_name = 'campaigns/reservation_select_client.html'

    def get(self, request):
        clients = Client.objects.filter(actif=False).order_by('nom')
        print(clients)
        return render(request, self.template_name, {'clients': clients})

    def post(self, request):
        client_pk = request.POST.get('client_pk')
        if not client_pk:
            messages.error(request, 'Veuillez sélectionner un client.')
            return redirect('reservation_select_client')
        return redirect('reservation_create', client_pk=client_pk)





from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.utils import timezone

def __str__(self):
    return f"Réservation de {self.support} pour {self.client}"  # self.panneau → self.support

# 2. Passer client_pk à l'API de dispo
def verifier_dispo_faces_api(request, client_pk=None):
    date_debut_str = request.GET.get('date_debut')
    date_fin_str   = request.GET.get('date_fin')

    if not date_debut_str or not date_fin_str:
        return JsonResponse({'faces_occupees': [], 'message': 'Dates manquantes'}, status=200)

    try:
        date_debut = parse_datetime(date_debut_str)
        date_fin   = parse_datetime(date_fin_str)
        if date_debut and timezone.is_naive(date_debut):
            date_debut = timezone.make_aware(date_debut)
        if date_fin and timezone.is_naive(date_fin):
            date_fin = timezone.make_aware(date_fin)
    except (ValueError, TypeError):
        return JsonResponse({'faces_occupees': [], 'error': 'Format de date invalide'}, status=400)

    if not date_debut or not date_fin:
        return JsonResponse({'faces_occupees': [], 'error': 'Impossible de parser les dates'}, status=400)

    # ── Conflits via ReservationLigne (nouveau modèle) ────────────────────
    qs_resa = ReservationLigne.objects.filter(
        reservation__date_debut__lt=date_fin,
        reservation__date_fin__gt=date_debut,
        reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
    )
    if client_pk:
        qs_resa = qs_resa.exclude(reservation__client_id=client_pk)

    faces_reservees = set(qs_resa.values_list('face_id', flat=True))

    # ── Conflits via LigneCampagne (campagnes actives) ────────────────────
    qs_camp = LigneCampagne.objects.filter(
        face__isnull=False,
    ).filter(
        Q(
            campagne__statut__in=['en_cours', 'a_venir'],
            campagne__date_debut__lte=date_fin.date(),
            campagne__date_fin__gte=date_debut.date(),
        )
        | Q(
            campagne__statut='en_pause',
            campagne__date_debut__lte=date_fin.date(),
            campagne__date_mise_en_pause__lte=date_fin.date(),
        )
    )
    faces_campagnes = set(qs_camp.values_list('face_id', flat=True))

    faces_occupees = faces_reservees | faces_campagnes

    return JsonResponse({'faces_occupees': list(faces_occupees)})






def api_check_dispo(request, client_pk):
    """
    Retourne le statut de chaque face sur une période donnée.
    
    GET /gestion/campaigns/api/check-dispo/<client_pk>/?date_debut=...&date_fin=...
    
    Réponse :
    {
      "faces": {
        "42": { "statut": "libre",   "info": "" },
        "43": { "statut": "occupe",  "info": "ABC Corp · jusqu'au 31/01/2026" },
        "44": { "statut": "reserve", "info": "Client XYZ" },
        "45": { "statut": "panne",   "info": "" },
        "46": { "statut": "moi",     "info": "Déjà réservé par ce client" }
      }
    }
    """
    from django.utils.timezone import make_aware, datetime as dt
    from django.core.exceptions import ValidationError

    date_debut_str = request.GET.get('date_debut', '').strip()
    date_fin_str   = request.GET.get('date_fin',   '').strip()

    if not date_debut_str or not date_fin_str:
        return JsonResponse({'error': 'Dates manquantes.'}, status=400)

    try:
        date_debut = make_aware(dt.fromisoformat(date_debut_str))
        date_fin   = make_aware(dt.fromisoformat(date_fin_str))
    except ValueError:
        return JsonResponse({'error': 'Format de date invalide.'}, status=400)

    if date_fin <= date_debut:
        return JsonResponse({'error': 'date_fin doit être > date_debut.'}, status=400)

    # ── Récupération de toutes les faces de panneaux actifs ────────────────
    faces = (
        FacePanneau.objects
        .filter(support__type_support='panneau', support__actif=True)
        .select_related('support')
    )

    # ── Faces réservées par CE client sur cette période ────────────────────
    mes_faces = set(
        ReservationLigne.objects
        .filter(
            reservation__client_id=client_pk,
            reservation__date_debut__lt=date_fin,
            reservation__date_fin__gt=date_debut,
            reservation__statut__in=['en_attente', 'confirmee'],
        )
        .values_list('face_id', flat=True)
    )

    # ── Faces occupées par une campagne ────────────────────────────────────
    faces_campagne = {
        lc['face_id']: lc
        for lc in LigneCampagne.objects
        .filter(
            Q(
                campagne__statut__in=['en_cours', 'a_venir'],
                campagne__date_debut__lte=date_fin.date(),
                campagne__date_fin__gte=date_debut.date(),
            )
            | Q(
                campagne__statut='en_pause',
                campagne__date_debut__lte=date_fin.date(),
                campagne__date_mise_en_pause__lte=date_fin.date(),
            )
        )
        .select_related('campagne__client')
        .values(
            'face_id',
            'campagne__client__nom',
            'campagne__date_fin',
            'campagne__nom',
        )
    }

    # ── Faces réservées par UN AUTRE client ───────────────────────────────
    faces_reservation = {
        rl['face_id']: rl
        for rl in ReservationLigne.objects
        .filter(
            reservation__date_debut__lt=date_fin,
            reservation__date_fin__gt=date_debut,
            reservation__statut__in=['en_attente', 'confirmee'],
        )
        .exclude(reservation__client_id=client_pk)
        .values(
            'face_id',
            'reservation__client__nom',
        )
    }

    # ── Construction de la réponse ─────────────────────────────────────────
    result = {}
    for face in faces:
        fid = face.pk

        if face.etat == 'panne':
            result[fid] = {'statut': 'panne', 'info': ''}

        elif fid in mes_faces:
            result[fid] = {'statut': 'moi', 'info': 'Déjà réservé par ce client'}

        elif fid in faces_campagne:
            lc = faces_campagne[fid]
            result[fid] = {
                'statut': 'occupe',
                'info': f"{lc['campagne__client__nom']} · jusqu'au {lc['campagne__date_fin'].strftime('%d/%m/%Y')}",
            }

        elif fid in faces_reservation:
            rl = faces_reservation[fid]
            result[fid] = {
                'statut': 'reserve',
                'info': rl['reservation__client__nom'],
            }

        else:
            result[fid] = {'statut': 'libre', 'info': ''}

    return JsonResponse({'faces': result})







from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from campaigns.models import Client
from inventory.models import FacePanneau, Support


# ── Utilitaire ────────────────────────────────────────────────────────────────

def _parse_aware_datetime(value: str):
    """
    Parse une chaîne ISO (ex: "2025-08-01T08:00") en datetime aware.
    Gère les deux cas : naive et already-aware (avec offset).
    Lève ValueError si le format est invalide.
    """
    dt = timezone.datetime.fromisoformat(value)
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt)


# staff/views.py
"""
Vues du back-office staff pour traiter les demandes de réservation.
Accès réservé au staff (LoginRequired + UserPassesTestMixin).
"""

import logging
from datetime import datetime as datetime_type, date as date_type
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from campaigns.models import (
    Client,
    DemandeReservation,
    LigneCampagne,
    Reservation,
    ReservationLigne,
    STATUT_EN_ATTENTE,
    STATUT_CONFIRMEE,
)
from inventory.models import FacePanneau, Support

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Mixin de sécurité
# ══════════════════════════════════════════════════════════════════════════════

class StaffOnlyMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Mixin pour restreindre l'accès aux staff uniquement.
    """
    login_url = 'admin:login'
    
    def test_func(self):
        return self.request.user.is_staff
    
    def handle_no_permission(self):
        messages.error(self.request, "Accès réservé au staff.")
        return redirect('admin:login')


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard Staff
# ══════════════════════════════════════════════════════════════════════════════

class DashboardViewDemander(StaffOnlyMixin, View):
    """
    Page d'accueil du staff avec statistiques et actions rapides.
    """
    template_name = 'staff/dashboard.html'

    def get(self, request):
        now = timezone.now()
        
        # ── Statistiques demandes ─────────────────────────────────────────
        demandes_nouvelles = DemandeReservation.objects.filter(
            statut=DemandeReservation.STATUT_NOUVELLE
        ).count()
        
        demandes_en_cours = DemandeReservation.objects.filter(
            statut=DemandeReservation.STATUT_EN_COURS
        ).count()
        
        demandes_validees_mois = DemandeReservation.objects.filter(
            statut=DemandeReservation.STATUT_VALIDEE,
            created_at__year=now.year,
            created_at__month=now.month,
        ).count()
        
        # ── Demandes en attente depuis plus de 24h ────────────────────────
        demandes_urgentes = (
            DemandeReservation.objects
            .filter(
                statut__in=[
                    DemandeReservation.STATUT_NOUVELLE,
                    DemandeReservation.STATUT_EN_COURS,
                ]
            )
            .filter(created_at__lte=now - timezone.timedelta(hours=24))
            .count()
        )
        
        # ── Réservations actives ──────────────────────────────────────────
        reservations_actives = Reservation.objects.filter(
            date_debut__lte=now,
            date_fin__gte=now,
            statut=STATUT_CONFIRMEE,
        ).count()
        
        # ── Dernières demandes (non traitées) ──────────────────────────────
        dernieres_demandes = (
            DemandeReservation.objects
            .filter(
                statut__in=[
                    DemandeReservation.STATUT_NOUVELLE,
                    DemandeReservation.STATUT_EN_COURS,
                ]
            )
            .select_related('traite_par')
            .order_by('-created_at')[:5]
        )
        
        return render(request, self.template_name, {
            'demandes_nouvelles':  demandes_nouvelles,
            'demandes_en_cours':   demandes_en_cours,
            'demandes_validees_mois': demandes_validees_mois,
            'demandes_urgentes':   demandes_urgentes,
            'reservations_actives': reservations_actives,
            'dernieres_demandes':  dernieres_demandes,
        })



# ══════════════════════════════════════════════════════════════════════════════
# APIs AJAX
# ══════════════════════════════════════════════════════════════════════════════

class AjaxSearchClientView(StaffOnlyMixin, View):
    """
    AJAX : POST /staff/api/search-client/?q=jean
    Retourne max 10 clients correspondant à la recherche.
    """

    def get(self, request):
        q = request.GET.get('q', '').strip()
        
        if len(q) < 2:
            return JsonResponse([], safe=False)

        clients = Client.objects.filter(
            Q(nom__icontains=q) |
            Q(email__icontains=q) |
            Q(telephone__icontains=q),
            actif=True,
        ).values('id', 'nom', 'email', 'telephone')[:10]

        return JsonResponse(list(clients), safe=False)


class AjaxCheckFacesDispoView(StaffOnlyMixin, View):
    """
    AJAX : POST /staff/api/check-faces-dispo/
    Vérifie la disponibilité de faces sur une période modifiée.
    
    Body JSON:
    {
        "face_ids": [1, 2, 3],
        "date_debut": "2026-03-01",
        "date_fin": "2026-03-31"
    }
    
    Réponse JSON:
    {
        "resultats": [
            {
                "face_id": 1,
                "face_label": "A",
                "support_code": "OUA-001",
                "disponible": true,
                "conflits": []
            },
            {
                "face_id": 2,
                "face_label": "B",
                "support_code": "OUA-045",
                "disponible": false,
                "conflits": [
                    {"type": "campagne", "label": "Campaign XYZ", "debut": "2026-03-10", "fin": "2026-03-20"}
                ]
            }
        ]
    }
    """

    def post(self, request):
        import json
        
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON invalide'}, status=400)

        face_ids = data.get('face_ids', [])
        date_debut_str = data.get('date_debut', '')
        date_fin_str = data.get('date_fin', '')

        if not all([face_ids, date_debut_str, date_fin_str]):
            return JsonResponse({'error': 'Données manquantes'}, status=400)

        try:
            date_debut_d = date_type.fromisoformat(date_debut_str)
            date_fin_d = date_type.fromisoformat(date_fin_str)
            date_debut = timezone.make_aware(
                datetime_type.combine(date_debut_d, datetime_type.min.time())
            )
            date_fin = timezone.make_aware(
                datetime_type.combine(date_fin_d, datetime_type.max.time())
            )
        except ValueError:
            return JsonResponse({'error': 'Dates invalides'}, status=400)

        # Vérifier chaque face
        resultats = []
        faces = FacePanneau.objects.filter(pk__in=face_ids).select_related('support')

        for face in faces:
            statut = face.get_statut(date_debut=date_debut, date_fin=date_fin)
            disponible = statut == 'libre'
            conflits = []

            if not disponible:
                # Chercher les conflits
                if statut == 'occupe':
                    lc = LigneCampagne.objects.filter(
                        face=face,
                    ).filter(
                        Q(
                            campagne__statut__in=['en_cours', 'a_venir'],
                            campagne__date_debut__lte=date_fin_d,
                            campagne__date_fin__gte=date_debut_d,
                        )
                        | Q(
                            campagne__statut='en_pause',
                            campagne__date_debut__lte=date_fin_d,
                            campagne__date_mise_en_pause__lte=date_fin_d,
                        )
                    ).select_related('campagne').first()
                    if lc:
                        conflits.append({
                            'type': 'campagne',
                            'label': lc.campagne.nom,
                            'debut': str(lc.campagne.date_debut),
                            'fin': str(lc.campagne.date_fin),
                        })
                elif statut == 'reserve':
                    rl = ReservationLigne.objects.filter(
                        face=face,
                        reservation__date_debut__lt=date_fin,
                        reservation__date_fin__gt=date_debut,
                        reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                    ).select_related('reservation').first()
                    if rl:
                        conflits.append({
                            'type': 'reservation',
                            'label': rl.reservation.nom,
                            'debut': rl.reservation.date_debut.isoformat(),
                            'fin': rl.reservation.date_fin.isoformat(),
                        })

            resultats.append({
                'face_id': face.id,
                'face_label': face.label,
                'support_code': face.support.code,
                'disponible': disponible,
                'conflits': conflits,
            })

        return JsonResponse({'resultats': resultats})
    
    
    
"""
Vues de traitement des DemandeReservation par le gestionnaire.
À intégrer dans campaigns/views.py (ou un fichier dédié importé depuis là).
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils import timezone
from datetime import datetime, time



class DemandesListView(LoginRequiredMixin, SortableListMixin, View):
    """Liste des demandes de réservation à traiter par le gestionnaire."""
    template_name = 'campaigns/demandes_liste.html'
    SORT_FIELDS = {
        'reference': 'reference',
        'contact': 'nom_contact',
        'emplacements': 'nb_emplacements',
        'date_debut': 'date_debut_souhaitee',
        'statut': 'statut',
        'created_at': 'created_at',
    }
    DEFAULT_SORT = 'created_at'
    DEFAULT_DIR = 'desc'

    def get(self, request):
        statut = request.GET.get('statut', '')
        qs = (
            DemandeReservation.objects
            .select_related('client', 'reservation', 'traite_par')
        )

        if statut:
            qs = qs.filter(statut=statut)
        else:
            # Par défaut : uniquement les demandes qui nécessitent une action
            qs = qs.filter(statut__in=[
                DemandeReservation.STATUT_BROUILLON,
                DemandeReservation.STATUT_NOUVELLE,
                DemandeReservation.STATUT_EN_COURS,
            ])

        qs = self.apply_sort(qs)

        compteurs = {
            'brouillon': DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_BROUILLON).count(),
            'nouvelle': DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_NOUVELLE).count(),
            'en_cours': DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_EN_COURS).count(),
            'validee':  DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_VALIDEE).count(),
            'refusee':  DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_REFUSEE).count(),
        }

        context = {
            'demandes': qs,
            'statut_actif': statut,
            'compteurs': compteurs,
        }
        context.update(self.get_sort_context())
        return render(request, self.template_name, context)


class StaffDemandeWizardBase(StaffRequiredMixin, View):
    template_context = {
        'staff_mode': True,
        'base_template': 'base.html',
    }
    session_faces_key = 'gestion_demande_faces'
    session_supports_key = 'gestion_demande_supports'
    session_period_key = 'gestion_demande_periode'
    session_project_key = 'gestion_demande_projet'
    session_contact_key = 'gestion_demande_contact'
    session_edit_key = 'gestion_demande_edit_uuid'

    def get_selections(self, request):
        face_ids = request.session.get(self.session_faces_key, [])
        support_ids = request.session.get(self.session_supports_key, [])
        faces = list(
            FacePanneau.objects.filter(pk__in=face_ids)
            .select_related('support')
        )
        supports = list(Support.objects.filter(pk__in=support_ids))
        return faces, supports

    def clear_wizard_session(self, request):
        for key in (
            self.session_faces_key,
            self.session_supports_key,
            self.session_period_key,
            self.session_project_key,
            self.session_contact_key,
            self.session_edit_key,
        ):
            request.session.pop(key, None)

    def step_context(self, request, **context):
        context.update(self.template_context)
        context.update({
            'step1_url': reverse('demande_creation'),
            'step2_url': reverse('demande_creation_etape2'),
            'step3_url': reverse('demande_creation_etape3'),
            'cancel_url': (
                reverse(
                    'demande_detail',
                    kwargs={'uuid': request.session[self.session_edit_key]},
                )
                if request.session.get(self.session_edit_key)
                else reverse('demandes_liste')
            ),
            'editing_request': bool(request.session.get(self.session_edit_key)),
        })
        return context


class DemandeCreationView(StaffDemandeWizardBase):
    """Étape 1 : coordonnées, projet, période et emplacements."""
    template_name = 'portail/reserver_etape1.html'

    def _render_step(self, request, selection_form, contact_form):
        for field_name in ('client', 'nom_contact', 'societe', 'email', 'telephone'):
            contact_form.fields[field_name].widget.attrs.update({
                'form': 'form-etape1',
                'class': 'form-control-e3 staff-field-control',
            })
        return render(request, self.template_name, self.step_context(
            request,
            form=selection_form,
            contact_form=contact_form,
            panier=self.get_selections(request)[0],
            filtres={
                'ville': request.GET.get('ville', '').strip(),
                'categorie': request.GET.get('categorie', '').strip(),
                'format': request.GET.get('format', '').strip(),
            },
        ))

    def get(self, request):
        if request.session.get(self.session_edit_key) and request.GET.get('edit') != '1':
            self.clear_wizard_session(request)
        periode = request.session.get(self.session_period_key, {})
        selection_form = Etape1Form(initial={
            'faces_selectionnees': json.dumps(
                request.session.get(self.session_faces_key, [])
            ),
            'supports_selectionnees': json.dumps(
                request.session.get(self.session_supports_key, [])
            ),
            'date_debut': periode.get('date_debut'),
            'date_fin': periode.get('date_fin'),
        })
        return self._render_step(
            request,
            selection_form,
            DemandeGestionContactForm(
                initial=request.session.get(self.session_contact_key, {})
            ),
        )

    def post(self, request):
        selection_form = Etape1Form(request.POST)
        contact_form = DemandeGestionContactForm(request.POST)
        selection_valid = selection_form.is_valid()
        contact_valid = contact_form.is_valid()
        forms_valid = selection_valid and contact_valid
        if selection_valid:
            selection = selection_form.cleaned_data
            request.session[self.session_faces_key] = selection['faces_uuids']
            request.session[self.session_supports_key] = selection['supports_uuids']
            request.session[self.session_period_key] = {
                'date_debut': str(selection['date_debut']),
                'date_fin': str(selection['date_fin']),
            }
        if not forms_valid:
            return self._render_step(
                request, selection_form, contact_form
            )

        contact = contact_form.cleaned_data
        request.session[self.session_contact_key] = {
            'type_client': contact['type_client'],
            'client': str(contact['client'].pk) if contact['client'] else '',
            'nom_contact': contact['nom_contact'],
            'societe': contact['societe'],
            'email': contact['email'],
            'telephone': contact['telephone'],
            'accepte_contact': contact['accepte_contact'],
        }
        if not request.session.get(self.session_edit_key):
            request.session[self.session_project_key] = {
                'nom_campagne': '',
                'message': '',
            }
        return redirect('demande_creation_etape2')


class DemandeCreationEtape2View(StaffDemandeWizardBase):
    """Étape 2 : récapitulatif et validation de la demande."""
    template_name = 'campaigns/demande_creation_recap.html'

    def _context(self, request):
        faces, supports = self.get_selections(request)
        periode = request.session.get(self.session_period_key, {})
        date_debut = periode.get('date_debut')
        date_fin = periode.get('date_fin')
        if not date_debut or not date_fin or (not faces and not supports):
            messages.warning(request, "Veuillez compléter l'étape de sélection.")
            return None
        contact_data = request.session.get(self.session_contact_key)
        if not contact_data:
            messages.warning(request, "Veuillez renseigner les coordonnées du demandeur.")
            return None
        contact_payload = dict(contact_data)
        contact_payload['client'] = contact_payload.get('client') or ''
        contact_payload['accepte_contact'] = (
            'on' if contact_payload.get('accepte_contact') else ''
        )
        contact_form = DemandeGestionContactForm(contact_payload)
        if not contact_form.is_valid():
            messages.warning(
                request,
                "Les coordonnées du demandeur ne sont plus valides. Veuillez les vérifier.",
            )
            return None
        days = (date.fromisoformat(date_fin) - date.fromisoformat(date_debut)).days + 1
        project = request.session.get(self.session_project_key, {})
        return self.step_context(
            request,
            faces=faces,
            supports=supports,
            periode=periode,
            contact=contact_form.cleaned_data,
            project=project,
            nombre_emplacements=len(faces) + len(supports),
            duree_jours=days,
        )

    def get(self, request):
        context = self._context(request)
        if context is None:
            return redirect('demande_creation')
        return render(request, self.template_name, context)

    def post(self, request):
        context = self._context(request)
        if context is None:
            return redirect('demande_creation')
        data = context['contact']
        client = (
            data['client']
            if data['type_client'] == DemandeReservation.TYPE_CLIENT_EXISTANT
            else None
        )
        edit_uuid = request.session.get(self.session_edit_key)
        demande_uuid = edit_uuid
        try:
            with transaction.atomic():
                if edit_uuid:
                    demande = DemandeReservation.objects.select_for_update().get(
                        uuid=edit_uuid,
                        statut__in=(
                            DemandeReservation.STATUT_NOUVELLE,
                            DemandeReservation.STATUT_EN_COURS,
                        ),
                    )
                else:
                    demande = DemandeReservation(
                        statut=DemandeReservation.STATUT_EN_COURS,
                        token_partage=uuid.uuid4(),
                    )
                demande.client = client
                demande.type_client = data['type_client']
                demande.reference_client_saisie = client.reference if client else ''
                demande.nom_contact = (
                    (client.contact_nom or client.nom) if client else data['nom_contact']
                )
                demande.societe = client.nom if client else data['societe']
                demande.email = client.email if client else data['email']
                demande.telephone = client.telephone if client else data['telephone']
                demande.accepte_contact = data['accepte_contact']
                demande.date_debut_souhaitee = date.fromisoformat(context['periode']['date_debut'])
                demande.date_fin_souhaitee = date.fromisoformat(context['periode']['date_fin'])
                demande.nom_campagne = context['project'].get('nom_campagne', '')
                demande.message = context['project'].get('message', '')
                demande.full_clean(exclude=['reference'])
                demande.save()
                demande.faces_souhaitees.set(context['faces'])
                demande.supports_souhaites.set(context['supports'])
        except DemandeReservation.DoesNotExist:
            messages.error(
                request,
                "Le lien client est actif ou la demande a déjà été traitée; "
                "les modifications du gestionnaire ne sont pas autorisées.",
            )
            return redirect('demande_detail', uuid=demande_uuid)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, self.template_name, context)

        self.clear_wizard_session(request)
        if edit_uuid:
            messages.success(request, f"La demande {demande.reference} a été modifiée.")
            return redirect('demande_detail', uuid=demande.uuid)
        messages.success(
            request,
            f"La demande {demande.reference} est prête à être partagée avec le client.",
        )
        return redirect('demande_detail', uuid=demande.uuid)


class DemandeCreationEtape3View(StaffDemandeWizardBase):
    """Redirige l'ancienne troisième étape vers le récapitulatif."""

    def get(self, request):
        return redirect('demande_creation_etape2')

    def post(self, request):
        return redirect('demande_creation_etape2')


class DemandeDetailView(LoginRequiredMixin, View):
    """Détail d'une demande : emplacements, période, contact, actions."""
    template_name = 'campaigns/demande_detail.html'

    def get(self, request, uuid):
        demande = get_object_or_404(
            DemandeReservation.objects
                .select_related('client', 'reservation', 'reservation__client', 'campagne', 'traite_par')
                .prefetch_related('faces_souhaitees__support', 'supports_souhaites'),
            uuid=uuid,
        )

        # Les demandes du portail passent en traitement à la première ouverture.
        # Une demande partagée reste "nouvelle" jusqu'à sa validation par le client.
        if demande.est_nouvelle and not demande.token_partage:
            demande.marquer_en_cours(user=request.user)

        context = {
            'demande': demande,
            'campagne_form': DemandeCampagneForm(demande=demande),
        }
        client_reservations = demande.client or (
            Client.objects.filter(email__iexact=demande.email)
            .order_by('pk')
            .first()
        )
        context['reservations_client_periode'] = (
            Reservation.objects.filter(
                client=client_reservations,
                statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                date_debut__date__lte=demande.date_fin_souhaitee,
                date_fin__date__gte=demande.date_debut_souhaitee,
            )
            .prefetch_related('lignes__support', 'lignes__face')
            .order_by('date_debut', 'pk')
            if client_reservations else Reservation.objects.none()
        )
        context['peut_modifier_demande'] = (
            request.user.is_staff_regie_role
            and demande.statut in (
                DemandeReservation.STATUT_NOUVELLE,
                DemandeReservation.STATUT_EN_COURS,
            )
        )
        if (
            request.user.is_staff_regie_role
            and demande.statut == DemandeReservation.STATUT_BROUILLON
            and demande.token_partage
        ):
            context['share_url'] = request.build_absolute_uri(
                reverse(
                    'portail:demande_partage',
                    kwargs={'token': demande.token_partage},
                )
            )
        return render(request, self.template_name, context)


class DemandeAutoriserModificationView(StaffRequiredMixin, View):
    """Active ou réactive le lien de modification du client."""

    def post(self, request, uuid):
        demande = get_object_or_404(
            DemandeReservation,
            uuid=uuid,
            token_partage__isnull=False,
        )
        if demande.statut == DemandeReservation.STATUT_BROUILLON:
            demande.statut = DemandeReservation.STATUT_EN_COURS
            demande.save(update_fields=['statut', 'updated_at'])
            messages.success(request, "Le lien de modification client est désactivé.")
            return redirect('demande_detail', uuid=uuid)

        if demande.statut not in (
            DemandeReservation.STATUT_EN_COURS,
            DemandeReservation.STATUT_NOUVELLE,
        ):
            messages.error(request, "Cette demande ne peut plus être modifiée par le client.")
            return redirect('demande_detail', uuid=uuid)

        demande.statut = DemandeReservation.STATUT_BROUILLON
        demande.save(update_fields=['statut', 'updated_at'])
        messages.success(
            request,
            "La modification client est autorisée. Vous pouvez copier et partager le lien.",
        )
        return redirect('demande_detail', uuid=uuid)


class DemandeModifierView(StaffRequiredMixin, View):
    """Ouvre le même assistant de création avec les données de la demande."""

    def get(self, request, uuid):
        demande = get_object_or_404(
            DemandeReservation.objects.select_related('client'),
            uuid=uuid,
            statut__in=(
                DemandeReservation.STATUT_BROUILLON,
                DemandeReservation.STATUT_NOUVELLE,
                DemandeReservation.STATUT_EN_COURS,
            ),
        )
        if demande.statut == DemandeReservation.STATUT_BROUILLON:
            messages.error(
                request,
                "Désactivez d'abord le lien de modification client pour modifier la demande.",
            )
            return redirect('demande_detail', uuid=uuid)
        request.session[StaffDemandeWizardBase.session_edit_key] = str(demande.uuid)
        request.session[StaffDemandeWizardBase.session_faces_key] = list(
            demande.faces_souhaitees.values_list('pk', flat=True)
        )
        request.session[StaffDemandeWizardBase.session_supports_key] = list(
            demande.supports_souhaites.values_list('pk', flat=True)
        )
        request.session[StaffDemandeWizardBase.session_period_key] = {
            'date_debut': demande.date_debut_souhaitee.isoformat(),
            'date_fin': demande.date_fin_souhaitee.isoformat(),
        }
        request.session[StaffDemandeWizardBase.session_project_key] = {
            'nom_campagne': demande.nom_campagne,
            'message': demande.message,
        }
        request.session[StaffDemandeWizardBase.session_contact_key] = {
            'type_client': demande.type_client,
            'client': demande.client_id or '',
            'nom_contact': demande.nom_contact,
            'societe': demande.societe,
            'email': demande.email,
            'telephone': demande.telephone,
            'accepte_contact': demande.accepte_contact,
        }
        return redirect(f"{reverse('demande_creation')}?edit=1")


class DemandeAssocierCampagneView(StaffRequiredMixin, View):
    def post(self, request, uuid):
        demande = get_object_or_404(
            DemandeReservation.objects.select_related('client', 'reservation__client', 'campagne'),
            uuid=uuid,
        )
        form = DemandeCampagneForm(request.POST, demande=demande)
        if not form.is_valid():
            messages.error(request, "La campagne sélectionnée n'est pas valide pour ce client.")
            return redirect('demande_detail', uuid=uuid)

        demande.campagne = form.cleaned_data['campagne']
        demande.save(update_fields=['campagne', 'updated_at'])
        if demande.campagne_id:
            messages.success(request, "La campagne a été associée à la page de suivi du client.")
        else:
            messages.success(request, "L'association de campagne a été retirée.")
        return redirect('demande_detail', uuid=uuid)


class DemandeValiderView(LoginRequiredMixin, View):
    """
    Valide la demande :
      1. Récupère ou crée le Client (par email)
      2. Crée la Reservation
      3. Crée une ReservationLigne par face souhaitée
      4. Marque la demande comme validée

    Tout est fait dans une transaction atomique : si une seule face pose
    problème (conflit de disponibilité détecté par ReservationLigne.clean()),
    rien n'est créé.
    """

    def post(self, request, uuid):
        demande = get_object_or_404(DemandeReservation, uuid=uuid)

        if not demande.peut_etre_traitee:
            messages.error(request, "Cette demande a déjà été traitée.")
            return redirect('demande_detail', uuid=uuid)

        if demande.supports_souhaites.exists():
            messages.error(
                request,
                "Cette demande comprend un écran numérique et doit être traitée "
                "manuellement. Aucune réservation n'a été créée.",
            )
            return redirect('demande_detail', uuid=uuid)

        faces = list(demande.faces_souhaitees.select_related('support').all())
        if not faces:
            messages.error(
                request,
                "Impossible de valider : aucune face n'est associée à cette demande "
                "(cas des écrans numériques non géré automatiquement — traiter manuellement)."
            )
            return redirect('demande_detail', uuid=uuid)

        try:
            with transaction.atomic():
                # ── Client ────────────────────────────────────────────────
                # On ne réutilise un client existant que si son email n'est
                # pas vide (évite de matcher tous les clients avec email='')
                # et on prend le premier trouvé plutôt que d'utiliser
                # get_or_create(), qui échoue avec MultipleObjectsReturned
                # dès que plusieurs lignes Client partagent le même email
                # (le champ n'a probablement pas de contrainte unique=True).
                client = None
                if demande.email:
                    client = (
                        Client.objects
                        .filter(email=demande.email)
                        .order_by('pk')
                        .first()
                    )
                if client is None:
                    client = Client.objects.create(
                        email=demande.email,
                        nom=demande.societe or demande.nom_contact,
                        telephone=demande.telephone,
                    )

                # ── Réservation ───────────────────────────────────────────
                date_debut_dt = timezone.make_aware(
                    datetime.combine(demande.date_debut_souhaitee, time.min)
                )
                date_fin_dt = timezone.make_aware(
                    datetime.combine(demande.date_fin_souhaitee, time.max)
                )

                reservation = Reservation(
                    nom=demande.nom_campagne or f"Réservation {demande.reference}",
                    client=client,
                    date_debut=date_debut_dt,
                    date_fin=date_fin_dt,
                    created_by=request.user,
                    notes=(
                        f"Créée depuis la demande {demande.reference}.\n{demande.message}"
                    ).strip(),
                )
                reservation.full_clean()  # applique Reservation.clean() (durée, dates)
                reservation.save()

                # ── Lignes (une par face demandée) ───────────────────────
                # ReservationLigne.save() appelle full_clean() en interne,
                # ce qui vérifie les conflits (campagnes + autres réservations).
                for face in faces:
                    ReservationLigne.objects.create(
                        reservation=reservation,
                        support=face.support,
                        face=face,
                    )

                demande.marquer_validee(client=client, reservation=reservation, user=request.user)

            messages.success(
                request,
                f"Demande {demande.reference} validée. Réservation {reservation.reference} créée "
                f"({len(faces)} face{'s' if len(faces) > 1 else ''})."
            )

            # ── Email STAFF UNIQUEMENT (pas de mail au client à la validation) ──
            self._send_email_staff_validation(demande, reservation, faces, request)

        except ValidationError as e:
            # Regroupe les messages d'erreur (peut contenir un conflit de face)
            detail = "; ".join(e.messages) if hasattr(e, 'messages') else str(e)
            messages.error(request, f"Impossible de valider : {detail}")
        except Exception as e:
            messages.error(request, f"Erreur inattendue lors de la validation : {e}")

        return redirect('demande_detail', uuid=uuid)

    def _send_email_staff_validation(self, demande, reservation, faces, request):
        recap_emplacements = '\n'.join(
            f"  - {f.support.code} · Face {f.label} · {f.support.quartier}" for f in faces
        ) or '  (aucun)'

        corps_staff = (
            f"Demande validée : {demande.reference}\n\n"
            f"Réservation créée : {reservation.reference}\n"
            f"Validée par : {request.user.get_full_name() or request.user.username}\n\n"
            f"Contact   : {demande.nom_contact} ({demande.societe or '—'})\n"
            f"Email     : {demande.email}\n"
            f"Téléphone : {demande.telephone}\n\n"
            f"Période   : {demande.date_debut_souhaitee:%d/%m/%Y} → {demande.date_fin_souhaitee:%d/%m/%Y}\n"
            f"Campagne  : {demande.nom_campagne or '—'}\n\n"
            f"Emplacements réservés :\n{recap_emplacements}\n\n"
            f"Voir la réservation : {getattr(settings, 'SITE_URL', '')}/staff/reservations/{reservation.pk}/"
        )
        try:
            send_mail(
                subject=f"[GeoAd] Demande validée — {demande.reference}",
                message=corps_staff,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.CONTACT_EMAIL1, settings.CONTACT_EMAIL2],
                fail_silently=True,
            )
        except Exception as exc:
            logger.error("Email staff (validation) échoué pour demande %s : %s", demande.reference, exc)


class DemandeRefuserView(LoginRequiredMixin, View):
    """Refuse la demande, enregistre le motif, notifie le client ET le staff par email."""

    def post(self, request, uuid):
        demande = get_object_or_404(DemandeReservation, uuid=uuid)

        if not demande.peut_etre_traitee:
            messages.error(request, "Cette demande a déjà été traitée.")
            return redirect('demande_detail', uuid=uuid)

        motif = request.POST.get('motif', '').strip()
        if not motif:
            messages.error(request, "Le motif de refus est obligatoire.")
            return redirect('demande_detail', uuid=uuid)

        demande.marquer_refusee(user=request.user, notes=motif)

        # ── Email CLIENT ──────────────────────────────────────────────────
        try:
            send_mail(
                subject=f"Votre demande de réservation {demande.reference}",
                message=(
                    f"Bonjour {demande.nom_contact},\n\n"
                    f"Nous vous remercions pour votre demande de réservation "
                    f"({demande.reference}) concernant : {demande.get_resume_emplacements()}.\n\n"
                    f"Après étude, nous ne sommes malheureusement pas en mesure d'y donner suite "
                    f"pour la raison suivante :\n\n{motif}\n\n"
                    f"N'hésitez pas à nous recontacter pour toute autre demande.\n\n"
                    f"Cordialement,\nL'équipe régie publicitaire"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[demande.email],
                fail_silently=False,
            )
            messages.success(
                request,
                f"Demande {demande.reference} refusée. Le client a été notifié par email."
            )
        except Exception as e:
            messages.warning(
                request,
                f"Demande refusée, mais l'envoi de l'email au client a échoué : {e}"
            )

        # ── Email STAFF ───────────────────────────────────────────────────
        self._send_email_staff_refus(demande, motif, request)

        return redirect('demandes_liste')

    def _send_email_staff_refus(self, demande, motif, request):
        corps_staff = (
            f"Demande refusée : {demande.reference}\n\n"
            f"Refusée par : {request.user.get_full_name() or request.user.username}\n\n"
            f"Contact   : {demande.nom_contact} ({demande.societe or '—'})\n"
            f"Email     : {demande.email}\n"
            f"Téléphone : {demande.telephone}\n\n"
            f"Emplacements souhaités : {demande.get_resume_emplacements()}\n\n"
            f"Motif du refus :\n{motif}\n"
        )
        try:
            send_mail(
                subject=f"[GeoAd] Demande refusée — {demande.reference}",
                message=corps_staff,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.CONTACT_EMAIL1, settings.CONTACT_EMAIL2],
                fail_silently=True,
            )
        except Exception as exc:
            logger.error("Email staff (refus) échoué pour demande %s : %s", demande.reference, exc)


# ══════════════════════════════════════════════════════════════════════════════
# WIZARD STAFF — Création de réservation en 2 étapes
# (Étape 1 : Client, période et faces · Étape 2 : Vérification & validation)
#
# À COLLER DANS campaigns/views.py, à la suite des vues Reservation existantes.
# Réutilise : StaffRequiredMixin, Client, Support, FacePanneau, FormatSupport,
# Reservation, ReservationLigne, STATUT_EN_ATTENTE, STATUT_CONFIRMEE,
# _get_supports(), _parse_dates()  — déjà présents dans ton fichier.
#
# NÉCESSITE : import json  (en haut de campaigns/views.py, s'il n'y est pas déjà)
# ══════════════════════════════════════════════════════════════════════════════

import json

SESSION_CLIENT   = 'resa_wizard_client_pk'
SESSION_DATES    = 'resa_wizard_dates'      # {'date_debut': iso, 'date_fin': iso}
SESSION_FACES    = 'resa_wizard_faces'      # [id, id, ...]
SESSION_NOM      = 'resa_wizard_nom'


def _wizard_reset(request):
    for key in (SESSION_CLIENT, SESSION_DATES, SESSION_FACES, SESSION_NOM):
        request.session.pop(key, None)


# ── Première étape — Client, période et faces ─────────────────────────────────

class ReservationWizardEtape1View(StaffRequiredMixin, View):
    """
    Début de l'étape 1 — Sélectionner un client existant OU en créer un nouveau.

    URL  : /reservations/nouvelle/etape1/
           /clients/<client_pk>/reservations/nouvelle/etape1/  (client connu → ouvre le choix des faces)
    Name : reservation_wizard_etape1
    """
    def get(self, request, client_pk=None):
        if client_pk is not None:
            client = get_object_or_404(Client, pk=client_pk)
            request.session[SESSION_CLIENT] = client.pk
        return redirect('reservation_wizard_etape2')

    def post(self, request, client_pk=None):
        action = request.POST.get('action')

        # ── Sélection d'un client existant ──────────────────────────────────
        if action == 'selectionner':
            client_pk = request.POST.get('client_pk')
            client = get_object_or_404(Client, pk=client_pk)
            request.session[SESSION_CLIENT] = client.pk
            return redirect('reservation_wizard_etape2')

        # ── Création d'un nouveau client ────────────────────────────────────
        if action == 'creer':
            nom       = request.POST.get('nom', '').strip()
            email     = request.POST.get('email', '').strip()
            telephone = request.POST.get('telephone', '').strip()

            if not nom:
                messages.error(request, "Le nom du client est obligatoire.")
                return redirect('reservation_wizard_etape1')

            client = Client.objects.create(
                nom=nom, email=email, telephone=telephone,
            )
            request.session[SESSION_CLIENT] = client.pk
            messages.success(request, f"Client « {client.nom} » créé.")
            return redirect('reservation_wizard_etape2')

        messages.error(request, "Action invalide.")
        return redirect('reservation_wizard_etape1')


# ── Première étape — Sélection de la période et des faces ─────────────────────

class ReservationWizardEtape2View(StaffRequiredMixin, View):
    """
    Étape 2 — Choix de la période et des faces via une interface carte/liste
    interactive (identique à celle du wizard public), avec panier persistant.

    URL  : /reservations/nouvelle/etape2/
    Name : reservation_wizard_etape2
    """
    template_name = 'campaigns/reservation_wizard_etape2_faces.html'

    def _get_client(self, request):
        client_pk = request.session.get(SESSION_CLIENT)
        if not client_pk:
            return None
        return Client.objects.filter(pk=client_pk).first()

    def get(self, request):
        client = self._get_client(request)

        dates        = request.session.get(SESSION_DATES, {})
        selectionnes = request.session.get(SESSION_FACES, [])
        q = request.GET.get('q', '').strip()
        clients = Client.objects.all().order_by('nom')
        if q:
            clients = clients.filter(Q(nom__icontains=q) | Q(email__icontains=q))

        # Pré-remplissage du panier JS à partir des faces déjà en session.
        panier_faces = (
            FacePanneau.objects.filter(id__in=selectionnes)
            .select_related('support')
        )

        return render(request, self.template_name, {
            'client':          client,
            'panier_faces':    panier_faces,
            'mode_title':      'Nouvelle réservation — Étape 1/2',
            'date_debut':      dates.get('date_debut', ''),
            'date_fin':        dates.get('date_fin', ''),
            'nom_reservation': request.session.get(SESSION_NOM, ''),
            'clients': clients,
            'client_search': q,
        })

    def post(self, request):
        action = request.POST.get('action')
        if action == 'selectionner':
            client = get_object_or_404(Client, pk=request.POST.get('client_pk'))
            request.session[SESSION_CLIENT] = client.pk
            return redirect('reservation_wizard_etape2')

        if action == 'creer':
            nom = request.POST.get('nom', '').strip()
            if not nom:
                messages.error(request, "Le nom du client est obligatoire.")
                return redirect('reservation_wizard_etape2')
            client = Client.objects.create(
                nom=nom,
                email=request.POST.get('email', '').strip(),
                telephone=request.POST.get('telephone', '').strip(),
            )
            request.session[SESSION_CLIENT] = client.pk
            messages.success(request, f"Client « {client.nom} » créé.")
            return redirect('reservation_wizard_etape2')

        client = self._get_client(request)
        if not client:
            messages.warning(request, "Veuillez d'abord sélectionner un client.")
            return redirect('reservation_wizard_etape2')

        try:
            face_ids = [int(fid) for fid in json.loads(request.POST.get('faces_selectionnees', '[]'))]
        except (ValueError, TypeError):
            face_ids = []

        date_debut_str  = request.POST.get('date_debut', '').strip()
        date_fin_str    = request.POST.get('date_fin', '').strip()
        nom_reservation = request.POST.get('nom_reservation', '').strip()

        try:
            _parse_dates(date_debut_str, date_fin_str)
        except ValidationError as e:
            messages.error(request, e.message)
            return redirect('reservation_wizard_etape2')

        if not face_ids:
            messages.error(request, "Veuillez sélectionner au moins une face.")
            return redirect('reservation_wizard_etape2')

        request.session[SESSION_DATES] = {
            'date_debut': date_debut_str,
            'date_fin':   date_fin_str,
        }
        request.session[SESSION_FACES] = face_ids
        request.session[SESSION_NOM]   = nom_reservation

        return redirect('reservation_wizard_etape3')


# ── Deuxième étape — Vérification & validation ────────────────────────────────

class ReservationWizardEtape3View(StaffRequiredMixin, View):
    """
    Étape 2 — Récapitulatif complet, détection des conflits, et création
    atomique de la Reservation + ses ReservationLigne.

    URL  : /reservations/nouvelle/etape3/
    Name : reservation_wizard_etape3
    """
    template_name = 'campaigns/reservation_wizard_etape3_verification.html'

    def _check_session(self, request):
        return bool(
            request.session.get(SESSION_CLIENT)
            and request.session.get(SESSION_DATES, {}).get('date_debut')
            and request.session.get(SESSION_FACES)
        )

    def _get_context(self, request):
        client      = get_object_or_404(Client, pk=request.session[SESSION_CLIENT])
        dates       = request.session[SESSION_DATES]
        face_ids    = request.session[SESSION_FACES]
        nom         = request.session.get(SESSION_NOM, '') or f"Réservation {timezone.now():%d/%m/%Y}"

        date_debut, date_fin = _parse_dates(dates['date_debut'], dates['date_fin'])
        faces = (
            FacePanneau.objects.filter(id__in=face_ids)
            .select_related('support')
            .order_by('support__code', 'label')
        )
        duree_jours = (date_fin.date() - date_debut.date()).days + 1

        return {
            'client':      client,
            'faces':       faces,
            'date_debut':  date_debut,
            'date_fin':    date_fin,
            'duree_jours': duree_jours,
            'nom_reservation': nom,
            'mode_title':  'Nouvelle réservation — Étape 2/2',
        }

    def get(self, request):
        if not self._check_session(request):
            messages.warning(request, "Veuillez compléter les étapes précédentes.")
            return redirect('reservation_wizard_etape1')

        context = self._get_context(request)
        return render(request, self.template_name, context)

    def post(self, request):
        if not self._check_session(request):
            return redirect('reservation_wizard_etape1')

        context     = self._get_context(request)
        client      = context['client']
        faces       = context['faces']
        date_debut  = context['date_debut']
        date_fin    = context['date_fin']
        nom_reservation = context['nom_reservation']

        # ── Revalidation des conflits juste avant écriture (les dispos ont pu
        #    changer depuis l'étape 2) ────────────────────────────────────────
        nouvelles_lignes = []
        erreurs = []

        for face in faces:
            if face.support.type_support != 'panneau':
                erreurs.append(f"Panneau {face.support.code} — Face {face.label} : type de support invalide.")
                continue

            conflit_resa = (
                ReservationLigne.objects.filter(
                    face=face,
                    reservation__date_debut__lt=date_fin,
                    reservation__date_fin__gt=date_debut,
                    reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                )
                .select_related('reservation__client')
                .first()
            )
            if conflit_resa:
                r = conflit_resa.reservation
                erreurs.append(
                    f"Panneau {face.support.code} — Face {face.label} : déjà réservée "
                    f"du {r.date_debut:%d/%m/%Y} au {r.date_fin:%d/%m/%Y} (client : {r.client.nom})."
                )
                continue

            from campaigns.models import LigneCampagne
            conflit_camp = (
                LigneCampagne.objects.filter(
                    face=face,
                    campagne__date_debut__lte=date_fin.date(),
                    campagne__date_fin__gte=date_debut.date(),
                )
                .select_related('campagne__client')
                .first()
            )
            if conflit_camp:
                c = conflit_camp.campagne
                erreurs.append(
                    f"Panneau {face.support.code} — Face {face.label} : occupée par la "
                    f"campagne « {c.nom} » (client : {c.client.nom})."
                )
                continue

            nouvelles_lignes.append(ReservationLigne(support=face.support, face=face))

        if erreurs:
            for err in erreurs:
                messages.error(request, err)
            # On revient à l'étape 3 avec le récap (l'utilisateur peut retourner
            # à l'étape 2 pour ajuster la sélection).
            return render(request, self.template_name, {**context, 'erreurs': erreurs})

        try:
            with transaction.atomic():
                reservation = Reservation.objects.create(
                    client=client,
                    nom=nom_reservation,
                    date_debut=date_debut,
                    date_fin=date_fin,
                    statut=STATUT_EN_ATTENTE,
                    created_by=request.user if request.user.is_authenticated else None,
                )
                for ligne in nouvelles_lignes:
                    ligne.reservation = reservation
                ReservationLigne.objects.bulk_create(nouvelles_lignes)
        except Exception as e:
            messages.error(request, f"Erreur lors de la création : {str(e)}")
            return render(request, self.template_name, {**context, 'erreurs': [str(e)]})

        _wizard_reset(request)
        messages.success(
            request,
            f"Réservation « {nom_reservation} » créée avec {len(nouvelles_lignes)} face(s)."
        )
        return redirect('reservation_detail', client_pk=client.pk, resa_pk=reservation.pk)


class ReservationWizardCancelView(StaffRequiredMixin, View):
    """Annule le wizard en cours et vide la session."""
    def get(self, request):
        _wizard_reset(request)
        return redirect('reservation_list')