import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count, Sum, DecimalField, Value
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

from django.urls import reverse_lazy
from accounts.models import AuditLog
from accounts.decorators import *
from accounts.audit import log_action
from .models import *
from inventory.models import Support, FacePanneau, EcranNumerique, FormatSupport
from .forms import ClientForm, ContratForm, CampagneForm, LigneCampagneForm
# from .mixins import *
from datetime import datetime, date  # ← Ajouter 'date' ici
today = timezone.now().date()
from core.mixins import SortableListMixin

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
    campagnes_qs = client.campagnes.filter(statut__in=['en_cours', 'a_venir']).order_by('-date_debut')

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

from inventory.models import Support, FacePanneau, FormatSupport
from .models import (
    Campagne, Client, LigneCampagne, ReservationLigne,
    STATUT_EN_ATTENTE, STATUT_CONFIRMEE,
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'campaigns/dashboard.html'

    CATEGORY_COLORS = {
        'Standard': 'var(--color-primary)',
        'Géant': '#f59e0b',
        'Sucette': '#a78bfa',
        'Marché': '#f97316',
    }
    DEFAULT_CATEGORY_COLOR = '#64748b'
    COLOR_ECRANS = '#06b6d4'
    COLOR_PANNE = '#94a3b8'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        ville_active = self.request.GET.get("ville", "").strip()

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
        for row in FormatSupport.objects.exclude(categorie='').values('categorie', 'code').order_by('categorie', 'code'):
            format_categories_codes.setdefault(row['categorie'], []).append(row['code'])

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
            campagnes_qs = campagnes_qs.filter(lignes__support__ville__iexact=ville_active).distinct()
            clients_qs = clients_qs.filter(campagnes__lignes__support__ville__iexact=ville_active).distinct()
            campagnes_actives_qs = campagnes_actives_qs.filter(lignes__support__ville__iexact=ville_active).distinct()
            campagnes_a_venir_qs = campagnes_a_venir_qs.filter(lignes__support__ville__iexact=ville_active).distinct()

        # Prefetch pour limiter les requêtes N+1 déclenchées par montant_total()
        campagnes_actives_list = list(
            campagnes_actives_qs.prefetch_related('lignes__face__support', 'lignes__support')
        )
        campagnes_a_venir_list = list(
            campagnes_a_venir_qs.prefetch_related('lignes__face__support', 'lignes__support')
        )

        revenu_actif = sum(c.montant_total() for c in campagnes_actives_list)
        revenu_a_venir = sum(c.montant_total() for c in campagnes_a_venir_list)

        campagnes_meres_qs = Campagne.objects.filter(
            est_mere=True, statut__in=['en_cours', 'a_venir']
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
            usage_count=Count('lignes_campagne', filter=Q(lignes_campagne__campagne__statut__in=['en_cours', 'a_venir'])),
            revenue=Coalesce(
                Sum('lignes_campagne__campagne__prix', filter=Q(lignes_campagne__campagne__statut__in=['en_cours', 'a_venir'])),
                Value(0, output_field=DecimalField(max_digits=12, decimal_places=2)),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ).filter(usage_count__gt=0)

        if ville_active:
            top_faces_qs = top_faces_qs.filter(support__ville__iexact=ville_active)

        top_faces = top_faces_qs.order_by('-revenue', '-usage_count')[:6]
        top_faces_json = json.dumps([
            {'label': f'{f.support.code}-{f.label}', 'revenue': float(f.revenue or 0), 'usage': f.usage_count}
            for f in top_faces
        ])

        # ── Comptages du parc : 1 seule requête agrégée au lieu de 7 ──
        parc = supports.aggregate(
            total=Count('id'),
            nb_panneaux=Count('id', filter=Q(type_support=Support.TYPE_PANNEAU)),
            nb_panneaux_bon=Count('id', filter=Q(type_support=Support.TYPE_PANNEAU, etat=Support.ETAT_BON)),
            nb_ecrans=Count('id', filter=Q(type_support=Support.TYPE_ECRAN)),
            nb_ecrans_bon=Count('id', filter=Q(type_support=Support.TYPE_ECRAN, etat=Support.ETAT_BON)),
            nb_ecrans_panne=Count('id', filter=Q(type_support=Support.TYPE_ECRAN, etat=Support.ETAT_PANNE)),
            nb_supports_panne=Count('id', filter=Q(etat=Support.ETAT_PANNE)),
            nb_supports_bon=Count('id', filter=Q(etat=Support.ETAT_BON)),
        )

        # ── Faces occupées / réservées (déjà optimal) ──
        faces_occupees_ids = set(
            LigneCampagne.objects.filter(
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
                campagne__statut__in=['en_cours', 'a_venir'],
            ).values_list('face_id', flat=True)
        )
        faces_reservees_ids = set(
            ReservationLigne.objects.filter(
                reservation__date_fin__gte=today,
                reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
            ).values_list('face_id', flat=True)
        )

        format_category_stats = self._build_format_category_stats(
            format_categories_codes, ville_active, faces_occupees_ids, faces_reservees_ids
        )

        total_faces_occupe = sum(cat['nb_occupe'] for cat in format_category_stats)
        total_faces_reserve = sum(cat['nb_reserve'] for cat in format_category_stats)
        total_faces_libre = sum(cat['nb_libre'] for cat in format_category_stats)
        total_faces_panne = sum(cat['nb_panne'] for cat in format_category_stats)
        total_faces = sum(cat['nb_faces'] for cat in format_category_stats)

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

        # ── Villes disponibles : distinct côté base plutôt qu'en Python ──
        villes = list(
            Support.objects.exclude(ville='').values_list('ville', flat=True).distinct().order_by('ville')
        )

        context.update({
            'ville_active': ville_active,
            'villes': villes,

            'total_supports': parc['total'],
            'supports_bon': parc['nb_supports_bon'],
            'supports_panne': parc['nb_supports_panne'],
            'total_clients': clients_qs.count(),
            'campagnes_actives': len(campagnes_actives_list),
            'campagnes_a_venir': len(campagnes_a_venir_list),
            'campagnes_recentes': campagnes_qs[:8],
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
            'total_faces': total_faces,
            'total_faces_occupe': total_faces_occupe,
            'total_faces_reserve': total_faces_reserve,
            'total_faces_libre': total_faces_libre,
            'total_faces_panne': total_faces_panne,

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
        faces_bon = faces_qs.filter(etat=Support.ETAT_BON).values_list('id', 'support__format')
        bon_par_format = {}
        for face_id, fmt in faces_bon:
            bon_par_format.setdefault(fmt, []).append(face_id)

        # 3) nb de supports en bon état par format
        supports_qs = Support.objects.filter(type_support=Support.TYPE_PANNEAU, format__in=all_codes, etat=Support.ETAT_BON)
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

def _build_alertes():
    alertes = []
    today = timezone.now().date()
    fin_dans_7j = today + timezone.timedelta(days=2)

    supports_panne = Support.objects.filter(etat='panne').only('code', 'pk')
    for s in supports_panne:
        alertes.append({
            'type': 'danger',
            'msg': f'Support {s.code} en panne',
            'url': reverse('support_detail', args=[s.uuid])
        })

    bientot = Campagne.objects.filter(
        statut='en_cours',
        date_fin__range=(today, fin_dans_7j)
    ).only('nom', 'date_fin', 'pk')

    for c in bientot:
        jours = (c.date_fin - today).days
        msg = f'Campagne "{c.nom}" se termine ' + (f"dans {jours}j" if jours > 0 else "aujourd'hui")
        alertes.append({
            'type': 'warning',
            'msg': msg,
            'url': reverse('campagne_detail', args=[c.pk])
        })
    return alertes


def get_cached_alertes():
    res = cache.get('dashboard_alertes')
    if not res:
        res = _build_alertes()
        cache.set('dashboard_alertes', res, 3600)
    return res

# ── Clients ──────────────────────────────────────────────────────────────────

class ClientListView(StaffRequiredMixin, ListView):
    model = Client
    template_name = 'campaigns/client_list.html'
    context_object_name = 'clients'

    def get_queryset(self):
        qs = Client.objects.annotate(
            cp_panneau=Count('campagnes', filter=Q(campagnes__statut='en_cours', campagnes__type_support='panneau', campagnes__actif=True)),
            cp_ecran=Count('campagnes', filter=Q(campagnes__statut='en_cours', campagnes__type_support='ecran', campagnes__actif=True)),
            cp_total=Count('campagnes', filter=Q(campagnes__statut='en_cours', campagnes__actif=True)),
        )
        q = self.request.GET.get('q', '')
        if q:
            qs = qs.filter(Q(nom__icontains=q) | Q(contact_nom__icontains=q))
        return qs.prefetch_related('campagnes').order_by('nom')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for client in context['clients']:
            campagnes_actives = [c for c in client.campagnes.all() if c.statut == 'en_cours' and c.actif]
            client.campagnes_stats = {
                'panneau': client.cp_panneau,
                'ecran': client.cp_ecran,
                'total': client.cp_total,
            }
            client.spots_stats = {
                'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support and c.type_support != 'ecran'),
                'ecran': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'ecran'),
                'total': sum(c.calculer_nombre_spots() for c in campagnes_actives),
            }
        if not self.request.user.is_admin:
            context['clients'] = context['clients'].filter(actif=True)
        context['q'] = self.request.GET.get('q', '')
        context['title'] = 'Liste des Clients'
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
        client = super().get_object(queryset=queryset)
        return client

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object
        if self.request.user.is_admin:
            campagnes = client.campagnes.all().order_by('-date_debut')
            contrats = client.contrats.all().order_by('-date_debut')
        else:
            campagnes = client.campagnes.filter(actif=True).order_by('-date_debut')
            contrats = client.contrats.filter(archive=True).order_by('-date_debut')

        campagnes_actives = [c for c in campagnes if c.statut == 'en_cours' and (self.request.user.is_admin or c.actif)]
        client.campagnes_stats = {
            'panneau': len([c for c in campagnes_actives if c.type_support and c.type_support != 'ecran']),
            'ecran': len([c for c in campagnes_actives if c.type_support == 'ecran']),
            'total': len(campagnes_actives),
        }
        client.spots_stats = {
            'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support and c.type_support != 'ecran'),
            'ecran': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'ecran'),
            'total': sum(c.calculer_nombre_spots() for c in campagnes_actives),
        }

        reservations = (
            client.reservations_globales
            .filter(statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE], date_fin__gte=timezone.now())
            .prefetch_related('lignes__face', 'lignes__support')
            .order_by('date_debut')
        )

        context.update({
            'campagnes': campagnes,
            'contrats': contrats,
            'reservations': reservations,
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
                        campagnes_count = campagnes.count()
                        campagnes.delete()
                        log_action(
                            request,
                            AuditLog.ACTION_DELETE,
                            'commercial',
                            detail=f"Suppression de {campagnes_count} campagnes associées à {client_name} avant suppression du client"
                        )
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
            log_action(request, AuditLog.ACTION_DELETE, 'commercial', detail=f"Suppression client: {client_name}")
            messages.success(request, f'Client "{client_name}" supprimé.')
            return redirect(self.get_success_url())
        # Sinon on Archive le client (actif=False) et on archive aussi les campagnes associées
        else:
            self.object.actif = False
            self.object.save()
            campagnes = self.object.campagnes.filter(actif=True)
            campagnes.update(actif=False)
            log_action(request, AuditLog.ACTION_UPDATE, 'commercial', detail=f"Client {client_name} archivé (actif=False) avec {campagnes.count()} campagnes associées archivées.")
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
        context['title'] = 'Nouveau Contrat'
        return context

    def form_valid(self, form):
        if client_pk := self.kwargs.get('client_pk'):
            form.instance.client_id = client_pk
        response = super().form_valid(form)
        log_action(self.request, AuditLog.ACTION_CREATE, 'commercial', obj=self.object, detail=f"Création contrat pour {self.object.client.nom}")
        messages.success(self.request, f'Contrat créé pour {self.object.client.nom}.')
        return response

    def form_invalid(self, form):
        messages.error(self.request, f'Erreurs dans le formulaire: {form.errors}')
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse('client_detail', kwargs={'pk': self.object.client.pk})


class ContratUpdateView(StaffRequiredMixin, UpdateView):
    model = Contrat
    form_class = ContratForm
    template_name = 'campaigns/contrat_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request, AuditLog.ACTION_UPDATE, 'commercial', obj=self.object, detail=f"Modification contrat: {self.object}")
        messages.success(self.request, 'Contrat mis à jour.')
        return response

    def get_success_url(self):
        return reverse('client_detail', kwargs={'pk': self.object.client.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Modifier Contrat — {self.object}'
        context['obj'] = self.object
        return context


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
            log_action(request, AuditLog.ACTION_DELETE, 'commercial', detail=f"Suppression contrat: {contrat_str}")
            messages.success(request, 'Contrat supprimé.')
            return response
        # Sinon on Archive le client (actif=False) et on archive aussi les campagnes associées
        else:
            contrat = self.get_object()
            contrat_str = str(contrat)
            contrat.archive = False
            contrat.save()
            log_action(request, AuditLog.ACTION_UPDATE, 'commercial', detail=f"Archivage contrat: {contrat_str}")
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

    def get_queryset(self):
        qs = super().get_queryset().select_related('client')

        if self.request.user.is_client_role and self.request.user.client_profile:
            qs = qs.filter(client=self.request.user.client_profile)

        # ── Restriction non-admin : uniquement les campagnes actives ──
        if not self.request.user.is_admin:
            qs = qs.filter(actif=True)

        q            = self.request.GET.get('q', '')
        statut       = self.request.GET.get('statut', '')
        type_support = self.request.GET.get('type_support', '')
        actif        = self.request.GET.get('actif', '')

        if type_support:
            qs = qs.filter(type_support=type_support)
        if q:
            qs = qs.filter(Q(nom__icontains=q) | Q(client__nom__icontains=q) | Q(reference__icontains=q))
        if statut:
            qs = qs.filter(statut=statut)
        if actif:
            qs = qs.filter(actif=actif)

        qs = qs.annotate(nb_supports_annotate=Count('lignes__support', distinct=True))
        qs = self.apply_sort(qs)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)  # ne touche plus à context['campagnes']

        context['q']                    = self.request.GET.get('q', '')
        context['statut']               = self.request.GET.get('statut', '')
        context['statut_choices']       = Campagne.STATUT_CHOICES
        context['type_support']         = self.request.GET.get('type_support', '')
        context['type_support_choices'] = get_type_support_choices()
        context['actif']                = self.request.GET.get('actif', '')

        return context
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
            count = qs.update(actif=False)
            messages.success(request, f"{count} campagne(s) archivée(s).")
        else:
            messages.error(request, f"Action inconnue : {action}")
        return redirect('campagne_list')

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
    Regroupe les lignes par support et calcule, pour chaque support :
    - quantite (spots pour écran, nb de faces pour panneau)
    - prix / affichage / impression unitaires et totaux
    - total_support = prix_total + affichage_total + impression_total
    Retourne (rows, totaux_campagne)
    """
    grouped = {}
    order = []
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
        })

        totaux['quantite'] += quantite
        totaux['prix'] += prix_total
        totaux['affichage'] += aff_total
        totaux['impression'] += imp_total
        totaux['total'] += total_support

    return rows, totaux


class CampagneDetailView(ClientStaffRequiredMixin, DetailView):
    model = Campagne
    template_name = 'campaigns/campagne_detail.html'
    context_object_name = 'campagne'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        campagne = self.object

        lignes = campagne.lignes.select_related(
            'support__ecran_info', 'face__support'
        ).all()

        for ligne in lignes:
            if campagne.type_support == 'ecran' and hasattr(ligne.support, 'ecran_info'):
                ligne.spots_calcules = ligne.support.ecran_info.calculer_nombre_spots_campagne(campagne)
            elif campagne.type_support != 'ecran' and ligne.face:
                ligne.spots_calcules = ligne.face.calculer_nombre_spots_campagne(campagne)
            else:
                ligne.spots_calcules = 0

        context['lignes'] = lignes
        context['sous_campagnes'] = campagne.sous_campagnes.all()

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

            for enfant in enfants:
                enfant_lignes = enfant.lignes.select_related(
                    'support__ecran_info', 'face__support'
                ).all()
                for ligne in enfant_lignes:
                    if enfant.type_support == 'ecran' and hasattr(ligne.support, 'ecran_info'):
                        ligne.spots_calcules = ligne.support.ecran_info.calculer_nombre_spots_campagne(enfant)
                    elif enfant.type_support != 'ecran' and ligne.face:
                        ligne.spots_calcules = ligne.face.calculer_nombre_spots_campagne(enfant)
                    else:
                        ligne.spots_calcules = 0

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
        else:
            support_rows, totaux_campagne = build_support_rows(campagne, lignes)
            context['support_rows'] = support_rows
            context['totaux_campagne'] = totaux_campagne
            context['total_supports'] = lignes.count()
            context['total_spots'] = campagne.calculer_nombre_spots()

        return context



class CampagneCreateUpdateView(StaffRequiredMixin, UpdateView):
    model = Campagne
    form_class = CampagneForm
    template_name = 'campaigns/campagne_form.html'

    def get_object(self, queryset=None):
        # Si 'pk' est présent dans l'URL, c'est une modification, sinon une création
        pk = self.kwargs.get('pk')
        return get_object_or_404(Campagne, pk=pk) if pk else None

    def get_initial(self):
        initial = super().get_initial()
        # Utilisation de .get() pour éviter les erreurs si la clé n'existe pas
        if client_id := self.request.GET.get('client'):
            initial['client'] = client_id
        if campagne_parente_id := self.request.GET.get('campagne_parente'):
            initial['campagne_parente'] = campagne_parente_id
            if not initial.get('client'):
                try:
                    mere = Campagne.objects.select_related('client').get(pk=campagne_parente_id)
                    initial['client'] = mere.client_id
                except Campagne.DoesNotExist:
                    pass
        return initial

    @transaction.atomic
    def form_valid(self, form):
        is_create = self.object is None
        
        if is_create:
            form.instance.created_by = self.request.user
        
        if form.cleaned_data.get('statut') == Campagne.STATUT_BROUILLON:
            form.instance.actif = False
        
        self.object = form.save()
        
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
        # Utilisation d'une logique ternaire pour plus de clarté
        context['title'] = f"Modifier — {self.object.nom}" if self.object else "Nouvelle Campagne"
        if self.object:
            context['obj'] = self.object
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
        log_action(request, AuditLog.ACTION_VIEW, 'campaign', obj=campagne, detail=f"Lancement campagne: {campagne.nom}")
        messages.success(request, f'Campagne "{campagne.nom}" lancée.')
        return redirect('campagne_detail', pk=pk)

    def get(self, request, pk):
        return redirect('campagne_detail', pk=pk)


class CampagneDeleteView(StaffRequiredMixin, DeleteView):
    model = Campagne
    template_name = 'partials/confirm_delete.html'

    def delete(self, request, *args, **kwargs):
        campagne = self.get_object()
        nom = str(campagne)
        response = super().delete(request, *args, **kwargs)
        log_action(request, AuditLog.ACTION_DELETE, 'campaign', detail=f"Suppression campagne: {nom}")
        messages.success(request, f'Campagne "{nom}" supprimée.')
        return response

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


class SupportBulkActionView(StaffRequiredMixin, View):
    template_name = 'campaigns/supports_add_bulk.html'

    def get_campagne(self, campagne_pk):
        return get_object_or_404(Campagne, pk=campagne_pk)

    def get(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = request.GET.get('type_support', campagne.type_support or '').strip()
        ville = request.GET.get('ville', campagne.lieu or '').strip()

        is_modification = campagne.lignes.exists()
        mode_title = "Modifier la sélection" if is_modification else "Ajouter des supports"
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
                etat='bon'
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

        else:
            # Panneaux : on ne garde que les panneaux du format ciblé, éventuellement de la ville ciblée.
            supports_qs = Support.objects.filter(
                type_support='panneau',
                format=type_support,
                etat='bon'
            )
            if ville:
                supports_qs = supports_qs.filter(ville=ville)
            supports_qs = supports_qs.prefetch_related('faces').order_by('ville', 'quartier')

            selectionnes = list(campagne.lignes.values_list('face_id', flat=True))

            for support in supports_qs:
                faces_enrichies = []
                for face in support.faces.all():
                    faces_enrichies.append({
                        'face': face,
                        'statut': face.get_statut(client=campagne.client),
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
        })

    def post(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = campagne.type_support

        if type_support != 'ecran':
            face_ids = request.POST.getlist('faces')

            campagne.lignes.filter(face__isnull=False).exclude(face_id__in=face_ids).delete()

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
        .filter(type_support='panneau', etat='bon')
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
    
class ReservationListView(StaffRequiredMixin, ListView):
    model = Reservation
    template_name = 'campaigns/reservation_list.html'
    context_object_name = 'reservations'
    paginate_by = 20

    def get_queryset(self):
        qs = Reservation.objects.select_related('client', 'created_by').prefetch_related('lignes__face__support')

        q        = self.request.GET.get('q', '').strip()
        statut_f = self.request.GET.get('statut', '')
        client_f = self.request.GET.get('client', '')
        date_f   = self.request.GET.get('date', '')

        if q:
            qs = qs.filter(
                Q(nom__icontains=q) |
                Q(client__nom__icontains=q)
            )
        if statut_f:
            qs = qs.filter(statut=statut_f)
        if client_f:
            qs = qs.filter(client__pk=client_f)
        if date_f:
            qs = qs.filter(date_debut__date=date_f)

        return qs.order_by('-date_debut')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context.update({
            'q':        self.request.GET.get('q', ''),
            'statut_f': self.request.GET.get('statut', ''),
            'client_f': self.request.GET.get('client', ''),
            'date_f':   self.request.GET.get('date', ''),
            'statut_choices': [
                (STATUT_EN_ATTENTE, 'En attente'),
                (STATUT_CONFIRMEE,  'Confirmée'),
                (STATUT_ANNULEE,    'Annulée'),
            ],
            'clients': Client.objects.order_by('nom'),
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
        campagne__date_debut__lte=date_fin.date(),
        campagne__date_fin__gte=date_debut.date(),
        campagne__statut__in=['en_cours', 'a_venir'],
        face__isnull=False,
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
            campagne__date_debut__lte=date_fin.date(),
            campagne__date_fin__gte=date_debut.date(),
            campagne__statut__in=['en_cours', 'a_venir'],
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
                        campagne__date_debut__lte=date_fin_d,
                        campagne__date_fin__gte=date_debut_d,
                        campagne__statut__in=['en_cours', 'a_venir'],
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



class DemandesListView(LoginRequiredMixin, View):
    """Liste des demandes de réservation à traiter par le gestionnaire."""
    template_name = 'campaigns/demandes_liste.html'

    def get(self, request):
        statut = request.GET.get('statut', '')
        qs = (
            DemandeReservation.objects
            .select_related('client', 'reservation', 'traite_par')
            .order_by('-created_at')
        )

        if statut:
            qs = qs.filter(statut=statut)
        else:
            # Par défaut : uniquement les demandes qui nécessitent une action
            qs = qs.filter(statut__in=[
                DemandeReservation.STATUT_NOUVELLE,
                DemandeReservation.STATUT_EN_COURS,
            ])

        compteurs = {
            'nouvelle': DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_NOUVELLE).count(),
            'en_cours': DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_EN_COURS).count(),
            'validee':  DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_VALIDEE).count(),
            'refusee':  DemandeReservation.objects.filter(statut=DemandeReservation.STATUT_REFUSEE).count(),
        }

        return render(request, self.template_name, {
            'demandes': qs,
            'statut_actif': statut,
            'compteurs': compteurs,
        })


class DemandeDetailView(LoginRequiredMixin, View):
    """Détail d'une demande : emplacements, période, contact, actions."""
    template_name = 'campaigns/demande_detail.html'

    def get(self, request, uuid):
        demande = get_object_or_404(
            DemandeReservation.objects
                .select_related('client', 'reservation', 'traite_par')
                .prefetch_related('faces_souhaitees__support', 'supports_souhaites'),
            uuid=uuid,
        )

        # Passe automatiquement en "en_cours" dès l'ouverture par le staff
        if demande.est_nouvelle:
            demande.marquer_en_cours(user=request.user)

        return render(request, self.template_name, {'demande': demande})


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
            return redirect('campaigns:demande_detail', uuid=uuid)

        faces = list(demande.faces_souhaitees.select_related('support').all())
        if not faces:
            messages.error(
                request,
                "Impossible de valider : aucune face n'est associée à cette demande "
                "(cas des écrans numériques non géré automatiquement — traiter manuellement)."
            )
            return redirect('campaigns:demande_detail', uuid=uuid)

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
# WIZARD STAFF — Création de réservation en 3 étapes
# (Étape 1 : Client · Étape 2 : Faces · Étape 3 : Vérification & Validation)
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


# ── Étape 1 — Client ─────────────────────────────────────────────────────────

class ReservationWizardEtape1View(StaffRequiredMixin, View):
    """
    Étape 1 — Sélectionner un client existant OU en créer un nouveau.

    URL  : /reservations/nouvelle/etape1/
           /clients/<client_pk>/reservations/nouvelle/etape1/  (client déjà connu → saute à l'étape 2)
    Name : reservation_wizard_etape1
    """
    template_name = 'campaigns/reservation_wizard_etape1_client.html'

    def get(self, request, client_pk=None):
        # Client déjà connu (venant de la fiche client) → on pré-remplit la
        # session et on saute directement à l'étape 2.
        if client_pk is not None:
            client = get_object_or_404(Client, pk=client_pk)
            request.session[SESSION_CLIENT] = client.pk
            return redirect('reservation_wizard_etape2')

        q = request.GET.get('q', '').strip()
        clients = Client.objects.all().order_by('nom')
        if q:
            clients = clients.filter(Q(nom__icontains=q) | Q(email__icontains=q))

        client_pk_session = request.session.get(SESSION_CLIENT)

        return render(request, self.template_name, {
            'clients':      clients,
            'q':            q,
            'selected_pk':  client_pk_session,
            'mode_title':   'Nouvelle réservation — Étape 1/3',
        })

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


# ── Étape 2 — Sélection des faces ────────────────────────────────────────────

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
        if not client:
            messages.warning(request, "Veuillez d'abord sélectionner un client.")
            return redirect('reservation_wizard_etape1')

        dates        = request.session.get(SESSION_DATES, {})
        selectionnes = request.session.get(SESSION_FACES, [])

        # Pré-remplissage du panier JS à partir des faces déjà en session.
        panier_faces = (
            FacePanneau.objects.filter(id__in=selectionnes)
            .select_related('support')
        )

        return render(request, self.template_name, {
            'client':          client,
            'panier_faces':    panier_faces,
            'mode_title':      'Nouvelle réservation — Étape 2/3',
            'date_debut':      dates.get('date_debut', ''),
            'date_fin':        dates.get('date_fin', ''),
            'nom_reservation': request.session.get(SESSION_NOM, ''),
        })

    def post(self, request):
        client = self._get_client(request)
        if not client:
            return redirect('reservation_wizard_etape1')

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


# ── Étape 3 — Vérification & validation ──────────────────────────────────────

class ReservationWizardEtape3View(StaffRequiredMixin, View):
    """
    Étape 3 — Récapitulatif complet, détection des conflits, et création
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
            'mode_title':  'Nouvelle réservation — Étape 3/3',
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