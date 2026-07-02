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

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'campaigns/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # ── Filtre ville ──────────────────────────────────────────
        ville_active = self.request.GET.get("ville", "").strip()

        for campagne in Campagne.objects.filter(statut__in=['a_venir', 'en_cours']):
            campagne.auto_update_statut()

        def get_format_category_codes():
            """Retourne les codes de formats groupés par catégorie depuis la table FormatSupport."""
            categories = {}
            for row in FormatSupport.objects.exclude(categorie='').values('categorie', 'code').order_by('categorie', 'code'):
                categories.setdefault(row['categorie'], []).append(row['code'])
            return categories

        CATEGORY_COLORS = {
            'Standard': 'var(--color-primary)',
            'Géant': '#f59e0b',
            'Sucette': '#a78bfa',
            'Marché': '#f97316',
        }
        DEFAULT_CATEGORY_COLOR = '#64748b'

        def build_format_category_stats(format_categories, supports, faces_occupees_ids, faces_reservees_ids):
            stats = []
            for categorie, codes in format_categories.items():
                faces_qs = FacePanneau.objects.filter(support__format__in=codes)
                if ville_active:
                    faces_qs = faces_qs.filter(support__ville__iexact=ville_active)

                total_faces = faces_qs.count()
                if total_faces == 0:
                    continue

                faces_bon = faces_qs.filter(etat=Support.ETAT_BON)
                nb_bon = faces_bon.count()
                nb_occupe = faces_bon.filter(pk__in=faces_occupees_ids).count()
                nb_reserve = faces_bon.filter(pk__in=faces_reservees_ids).exclude(pk__in=faces_occupees_ids).count()
                nb_libre = nb_bon - nb_occupe - nb_reserve
                nb_panne = faces_qs.filter(etat=Support.ETAT_PANNE).count()
                nb_supports = supports.filter(type_support=Support.TYPE_PANNEAU, format__in=codes, etat=Support.ETAT_BON).count()

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
                    'color': CATEGORY_COLORS.get(categorie, DEFAULT_CATEGORY_COLOR),
                })
            return stats

        format_categories = get_format_category_codes()

        # ── Queryset de base filtré par ville ─────────────────────
        supports = Support.objects.prefetch_related('faces').select_related('ecran_info').all()
        # ── Campagnes filtrées par ville ──────────────────────────
        campagnes_qs = Campagne.objects.select_related('client').order_by('-created_at')
        # Clients actifs ayant des supports dans la ville sélectionnée
        clients_qs = Client.objects.filter(actif=True)
        # Campagnes en cours avec au moins un support dans la ville
        campagnes_actives_qs = Campagne.objects.filter(statut='en_cours')
        campagnes_a_venir_qs = Campagne.objects.filter(statut='a_venir')
        
        if ville_active:
            supports = supports.filter(ville__iexact=ville_active)
            campagnes_qs = campagnes_qs.filter(lignes__support__ville__iexact=ville_active).distinct()
            clients_qs = clients_qs.filter(campagnes__lignes__support__ville__iexact=ville_active).distinct()
            campagnes_actives_qs = campagnes_actives_qs.filter(lignes__support__ville__iexact=ville_active).distinct()
            campagnes_a_venir_qs = campagnes_a_venir_qs.filter(lignes__support__ville__iexact=ville_active).distinct()
            
        revenu_actif = sum(c.montant_total() for c in campagnes_actives_qs)
        revenu_a_venir = sum(c.montant_total() for c in campagnes_a_venir_qs)
        campagnes_meres_qs = Campagne.objects.filter(est_mere=True, statut__in=['en_cours', 'a_venir'])
        campagnes_meres_count = campagnes_meres_qs.count()
        revenu_meres = sum(c.montant_total() for c in campagnes_meres_qs)

        top_campaignes_prix = sorted(
            campagnes_qs,
            key=lambda c: c.montant_total(),
            reverse=True
        )[:6]
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

        top_faces = FacePanneau.objects.annotate(
            usage_count=Count('lignes_campagne', filter=Q(lignes_campagne__campagne__statut__in=['en_cours','a_venir'])),
            revenue=Coalesce(
                Sum('lignes_campagne__campagne__prix', filter=Q(lignes_campagne__campagne__statut__in=['en_cours','a_venir'])),
                Value(0, output_field=DecimalField(max_digits=12, decimal_places=2)),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
        ).filter(usage_count__gt=0).order_by('-revenue', '-usage_count')[:6]
        top_faces_json = json.dumps([
            {
                'label': f'{f.support.code}-{f.label}',
                'revenue': float(f.revenue or 0),
                'usage': f.usage_count,
            }
            for f in top_faces
        ])

        nb_panneaux     = supports.filter(type_support=Support.TYPE_PANNEAU).count()
        nb_panneaux_bon = supports.filter(type_support=Support.TYPE_PANNEAU, etat=Support.ETAT_BON).count()
        nb_ecrans       = supports.filter(type_support=Support.TYPE_ECRAN).count()
        nb_ecrans_bon   = supports.filter(type_support=Support.TYPE_ECRAN, etat=Support.ETAT_BON).count()
        nb_ecrans_panne   = supports.filter(type_support=Support.TYPE_ECRAN, etat=Support.ETAT_PANNE).count()
        nb_supports_panne = supports.filter(etat=Support.ETAT_PANNE).count()
        nb_supports_bon   = supports.filter(etat=Support.ETAT_BON).count()

        ids_occupes = set(
            LigneCampagne.objects.filter(
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
                campagne__statut__in=['en_cours', 'a_venir'],
            ).values_list('support_id', flat=True)
        )

        # ── IDs des faces occupées (campagne active) ─────────────
        faces_occupees_ids = set(
            LigneCampagne.objects.filter(
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
                campagne__statut__in=['en_cours', 'a_venir'],
            ).values_list('face_id', flat=True)
        )

        # ── IDs des faces réservées ───────────────────────────────
        faces_reservees_ids = set(
            ReservationLigne.objects.filter(
                reservation__date_fin__gte=today,
                reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
            ).values_list('face_id', flat=True)
        )

        format_category_stats = build_format_category_stats(format_categories, supports, faces_occupees_ids, faces_reservees_ids)
        total_faces_occupe  = sum(cat['nb_occupe']  for cat in format_category_stats)
        total_faces_reserve = sum(cat['nb_reserve'] for cat in format_category_stats)
        total_faces_libre   = sum(cat['nb_libre']   for cat in format_category_stats)
        total_faces_panne   = sum(cat['nb_panne']   for cat in format_category_stats)
        total_faces         = sum(cat['nb_faces']   for cat in format_category_stats)
        total_supports      = supports.count()

        category_index = {cat['categorie']: cat for cat in format_category_stats}
        standard = category_index.get('Standard', {})
        geant = category_index.get('Géant', {})
        sucette = category_index.get('Sucette', {})
        marche = category_index.get('Marché', {})

        nb_standard = standard.get('nb_supports', 0)
        nbt_std = standard.get('nb_faces', 0)
        nbo_std = standard.get('nb_occupe', 0)
        nbr_std = standard.get('nb_reserve', 0)
        nbl_std = standard.get('nb_libre', 0)
        npt_std = standard.get('nb_panne', 0)
        o_std = round(nbo_std / nbt_std * 100) if nbt_std else 0

        nb_geants = geant.get('nb_supports', 0)
        nbt_geo = geant.get('nb_faces', 0)
        nbo_geo = geant.get('nb_occupe', 0)
        nbr_geo = geant.get('nb_reserve', 0)
        nbl_geo = geant.get('nb_libre', 0)
        npt_geo = geant.get('nb_panne', 0)
        o_geo = round(nbo_geo / nbt_geo * 100) if nbt_geo else 0

        nb_sucettes = sucette.get('nb_supports', 0)
        nbt_suc = sucette.get('nb_faces', 0)
        nbo_suc = sucette.get('nb_occupe', 0)
        nbr_suc = sucette.get('nb_reserve', 0)
        nbl_suc = sucette.get('nb_libre', 0)
        npt_suc = sucette.get('nb_panne', 0)
        o_suc = round(nbo_suc / nbt_suc * 100) if nbt_suc else 0

        nb_gm = marche.get('nb_supports', 0)
        nbt_gm = marche.get('nb_faces', 0)
        nbo_gm = marche.get('nb_occupe', 0)
        nbr_gm = marche.get('nb_reserve', 0)
        nbl_gm = marche.get('nb_libre', 0)
        npt_gm = marche.get('nb_panne', 0)
        o_gm = round(nbo_gm / nbt_gm * 100) if nbt_gm else 0

        format_stats_json = json.dumps(format_category_stats)
        types_stats_json = json.dumps([
            {'label': 'Panneaux',          'count': nb_panneaux_bon},
            {'label': 'Écrans',            'count': nb_ecrans_bon},
            {'label': 'Supports en Panne', 'count': nb_supports_panne},
        ])

        # ── Liste des villes disponibles (pour le filtre) ─────────
        villes = sorted({
            v.strip()
            for v in Support.objects.values_list("ville", flat=True)
            if v and v.strip()
        })

        context.update({
            # Filtre
            'ville_active' : ville_active,
            'villes'       : villes,

            # KPI globaux (respectent le filtre ville)
            'total_supports'    : supports.count(),
            'supports_bon'      : nb_supports_bon,
            'supports_panne'    : nb_supports_panne,
            'total_clients'     : clients_qs.count(),
            'campagnes_actives' : campagnes_actives_qs.count(),
            'campagnes_a_venir' : campagnes_a_venir_qs.count(),
            'campagnes_recentes': campagnes_qs[:8],
            'alertes'           : get_cached_alertes(),

            # Répartition parc
            'nb_panneaux'    : nb_panneaux,
            'nb_panneaux_bon': nb_panneaux_bon,
            'nb_ecrans_bon'  : nb_ecrans_bon,
            'nb_ecrans_panne': nb_ecrans_panne,
            'nb_ecrans'      : nb_ecrans,
            'format_categories': format_category_stats,
            'total_faces'  : total_faces,
            'total_faces_occupe'  : total_faces_occupe,
            'total_faces_reserve' : total_faces_reserve,
            'total_faces_libre'   : total_faces_libre,
            'total_faces_panne'   : total_faces_panne,
            
            # Détail revenu / campagnes
            'revenu_actif'          : revenu_actif,
            'revenu_a_venir'        : revenu_a_venir,
            'revenu_meres'          : revenu_meres,
            'campagnes_meres_count' : campagnes_meres_count,
            'top_campaigns_json'    : top_campaigns_json,
            'top_faces_json'        : top_faces_json,
            
            # Détail par type
            'nbl_std': nbl_std, 'nbo_std': nbo_std, 'nbr_std': nbr_std, 'npt_std': npt_std, 'nbt_std': nbt_std, 'o_std': o_std,
            'nbl_geo': nbl_geo, 'nbo_geo': nbo_geo, 'nbr_geo': nbr_geo, 'npt_geo': npt_geo, 'nbt_geo': nbt_geo, 'o_geo': o_geo,
            'nbl_suc': nbl_suc, 'nbo_suc': nbo_suc, 'nbr_suc': nbr_suc, 'npt_suc': npt_suc, 'nbt_suc': nbt_suc, 'o_suc': o_suc,
            'nbl_gm':  nbl_gm,  'nbo_gm':  nbo_gm,  'nbr_gm':  nbr_gm,  'npt_gm':  npt_gm,  'nbt_gm':  nbt_gm,  'o_gm':  o_gm,
            # Graphiques
            'format_stats_json': format_stats_json,
            'types_stats_json' : types_stats_json,
        })
        return context
    
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
        return qs.prefetch_related('campagnes')

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

class CampagneListView(ClientStaffRequiredMixin, ListView):
    model = Campagne
    template_name = 'campaigns/campagne_list.html'
    context_object_name = 'campagnes'

    def get_queryset(self):
        qs = super().get_queryset().select_related('client')
        if self.request.user.is_client_role and self.request.user.client_profile:
            qs = qs.filter(client=self.request.user.client_profile)
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
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # not is_admin
        if not self.request.user.is_admin:
            if self.request.user.is_client_role and self.request.user.client_profile:
                context['campagnes'] = context['campagnes'].filter(client=self.request.user.client_profile)
            context['campagnes'] = context['campagnes'].filter(actif=True)
        context['q']                    = self.request.GET.get('q', '')
        context['statut']               = self.request.GET.get('statut', '')
        context['statut_choices']       = Campagne.STATUT_CHOICES
        context['type_support']         = self.request.GET.get('type_support', '')
        context['type_support_choices'] = Campagne.TYPE_SUPPORT_CHOICES
        context['actif']                = self.request.GET.get('actif', '')
        return context

    def post(self, request, *args, **kwargs):
        # Seul le staff peut effectuer des actions
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
            #count = qs.count()
            #qs.delete()
            #messages.success(request, f"{count} campagne(s) supprimée(s).")
            # fonction (Class) de suppression à implémenter (avec confirmation côté client)
            request.session['selected_campagne_ids'] = selected_ids
            return redirect('campagne_selected_delete')
        elif action == 'archiver':
            count = qs.update(actif=False)
            messages.success(request, f"{count} campagne(s) archivée(s).")
            pass
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

        # Enrichissement des lignes avec spots calculés
        for ligne in lignes:
            if campagne.type_support == 'ecran' and hasattr(ligne.support, 'ecran_info'):
                ligne.spots_calcules = ligne.support.ecran_info.calculer_nombre_spots_campagne(campagne)
            elif campagne.type_support == 'panneau' and ligne.face:
                ligne.spots_calcules = ligne.face.calculer_nombre_spots_campagne(campagne)
            else:
                ligne.spots_calcules = 0

        context['lignes'] = lignes
        context['sous_campagnes'] = campagne.sous_campagnes.all()

        if campagne.est_mere:
            enfants = campagne.sous_campagnes.all()
            context['total_supports'] = sum(child.lignes.count() for child in enfants)
            context['total_spots'] = sum(child.calculer_nombre_spots() for child in enfants)
        else:
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
        
        # Quand le Statut est "Brouillon",actif est forcé en False
        if form.cleaned_data.get('statut') == Campagne.STATUT_BROUILLON:
            form.instance.actif = False
        
        self.object = form.save()
        
        # Récupérer depuis cleaned_data plutôt que request.FILES
        fichiers = form.cleaned_data.get('visuels_multiples', [])
        for f in fichiers:
            CampagneVisuel.objects.create(campagne=self.object, fichier=f)

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
        return super().form_valid(form)  # ← aussi, il manquait ça dans ton code original

    def get_success_url(self):
        return reverse('campagne_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Utilisation d'une logique ternaire pour plus de clarté
        context['title'] = f"Modifier — {self.object.nom}" if self.object else "Nouvelle Campagne"
        if self.object:
            context['obj'] = self.object
        return context


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

        is_modification = campagne.lignes.exists()
        mode_title = "Modifier la sélection" if is_modification else "Ajouter des supports"
        supports_enrichis = []

        if type_support == 'ecran':
            # Écrans : on filtre sur le type d'écran uniquement.
            supports_qs = Support.objects.filter(
                type_support='ecran', 
                etat='bon'
            ).order_by('ville', 'quartier')

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
            # Panneaux : on ne garde que les panneaux du format ciblé par la campagne.
            supports_qs = Support.objects.filter(
                type_support='panneau',
                format=type_support,
                etat='bon'
            ).prefetch_related('faces').order_by('ville', 'quartier')
            
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
            # ✅ Choix pour les selects dans le template
            'duree_choices': DUREE_CHOICES,
            'frequence_choices': FREQUENCE_CHOICES,
        })

    def post(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = campagne.type_support

        if type_support != 'ecran':
            face_ids = request.POST.getlist('faces')

            # Synchronisation : retirer les faces décochées
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

            # Synchronisation : retirer les écrans décochés
            campagne.lignes.filter(face__isnull=True).exclude(support_id__in=support_ids).delete()

            for support_id in support_ids:
                support = get_object_or_404(Support, pk=support_id, type_support='ecran')

                # ✅ Récupérer les paramètres spécifiques à cet écran depuis le POST
                date_debut_str      = request.POST.get(f'date_debut_{support_id}', '').strip()
                date_fin_str        = request.POST.get(f'date_fin_{support_id}', '').strip()
                duree_passage_str   = request.POST.get(f'duree_passage_{support_id}', '').strip()
                frequence_str       = request.POST.get(f'frequence_{support_id}', '').strip()
                tranches_horaires   = request.POST.get(f'tranches_horaires_{support_id}', '').strip()

                # Conversion des valeurs (None si vide → héritera de la campagne)
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
                        # ✅ Enregistrer les paramètres spécifiques (None = héritage campagne)
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
        format_choices_map = dict(Support.FORMAT_CHOICES)
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
        format_choices_map = dict(Support.FORMAT_CHOICES)
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
    
    GET /campaigns/api/check-dispo/<client_pk>/?date_debut=...&date_fin=...
    
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
# Liste des demandes
# ══════════════════════════════════════════════════════════════════════════════

class DemandeListView(StaffOnlyMixin, View):
    """
    Affiche la liste des demandes avec filtres et recherche.
    """
    template_name = 'staff/demandes_liste.html'

    def get(self, request):
        qs = (
            DemandeReservation.objects
            .select_related('client', 'traite_par')
            .prefetch_related('faces_souhaitees__support', 'supports_souhaites')
        )

        # ── Filtres ───────────────────────────────────────────────────────
        statut = request.GET.get('statut', '').strip()
        q      = request.GET.get('q', '').strip()
        tri    = request.GET.get('tri', '-created_at').strip()

        if statut and statut in dict(DemandeReservation.STATUT_CHOICES):
            qs = qs.filter(statut=statut)
        
        if q:
            qs = qs.filter(
                Q(reference__icontains=q) |
                Q(nom_contact__icontains=q) |
                Q(email__icontains=q) |
                Q(societe__icontains=q) |
                Q(nom_campagne__icontains=q)
            )

        # ── Tri ───────────────────────────────────────────────────────────
        if tri in ['-created_at', 'created_at', '-date_debut_souhaitee', 'date_debut_souhaitee']:
            qs = qs.order_by(tri)
        else:
            qs = qs.order_by('-created_at')

        # ── Pagination ────────────────────────────────────────────────────
        paginator = Paginator(qs, 20)
        page_obj  = paginator.get_page(request.GET.get('page', 1))

        # ── Compteurs ─────────────────────────────────────────────────────
        nb_nouvelles = DemandeReservation.objects.filter(
            statut=DemandeReservation.STATUT_NOUVELLE
        ).count()
        
        nb_en_cours = DemandeReservation.objects.filter(
            statut=DemandeReservation.STATUT_EN_COURS
        ).count()

        return render(request, self.template_name, {
            'page_obj':          page_obj,
            'statut':            statut,
            'q':                 q,
            'tri':               tri,
            'statut_choices':    DemandeReservation.STATUT_CHOICES,
            'nb_nouvelles':      nb_nouvelles,
            'nb_en_cours':       nb_en_cours,
        })


# ══════════════════════════════════════════════════════════════════════════════
# Détail d'une demande
# ══════════════════════════════════════════════════════════════════════════════

class DemandeDetailView(StaffOnlyMixin, View):
    """
    Affiche le détail complet d'une demande et permet le traitement.
    """
    template_name = 'staff/demande_detail.html'

    def get(self, request, uuid):
        demande = get_object_or_404(
            DemandeReservation.objects
            .select_related('client', 'reservation', 'traite_par')
            .prefetch_related(
                'faces_souhaitees__support',
                'supports_souhaites',
            ),
            uuid=uuid
        )

        # ── Marquer en cours si nouvelle ──────────────────────────────────
        if demande.statut == DemandeReservation.STATUT_NOUVELLE:
            demande.marquer_en_cours(request.user)

        # ── Vérifier disponibilité des faces sur la période ────────────────
        faces_avec_statut = self._get_faces_avec_statut(demande)

        # ── Clients existants pour le formulaire ──────────────────────────
        clients = Client.objects.filter(actif=True).order_by('nom')

        # ── Supports écrans (si demande écrans) ───────────────────────────
        supports_ecrans = demande.supports_souhaites.all()

        return render(request, self.template_name, {
            'demande':            demande,
            'faces_avec_statut':  faces_avec_statut,
            'clients':            clients,
            'supports_ecrans':    supports_ecrans,
            'duree_jours':        demande.duree_jours(),
        })

    def _get_faces_avec_statut(self, demande):
        """
        Retourne une liste de dicts avec chaque face et son statut
        + détails des conflits éventuels.
        """
        faces_avec_statut = []
        
        date_debut_dt = timezone.make_aware(
            datetime_type.combine(demande.date_debut_souhaitee, datetime_type.min.time())
        )
        date_fin_dt = timezone.make_aware(
            datetime_type.combine(demande.date_fin_souhaitee, datetime_type.max.time())
        )

        for face in demande.faces_souhaitees.all().select_related('support'):
            statut = face.get_statut(
                date_debut=date_debut_dt,
                date_fin=date_fin_dt,
            )
            
            # ── Détails du conflit ────────────────────────────────────────
            conflit = None
            if statut in ('occupe', 'reserve'):
                # Chercher conflit campagne
                lc = (
                    LigneCampagne.objects
                    .filter(
                        face=face,
                        campagne__date_debut__lte=demande.date_fin_souhaitee,
                        campagne__date_fin__gte=demande.date_debut_souhaitee,
                        campagne__statut__in=['en_cours', 'a_venir'],
                    )
                    .select_related('campagne__client')
                    .first()
                )
                if lc:
                    conflit = {
                        'type':  'campagne',
                        'label': f"{lc.campagne.nom}",
                        'client_nom': lc.campagne.client.nom,
                        'debut': lc.campagne.date_debut,
                        'fin':   lc.campagne.date_fin,
                    }
                else:
                    # Chercher conflit réservation
                    rl = (
                        ReservationLigne.objects
                        .filter(
                            face=face,
                            reservation__date_debut__lte=date_fin_dt,
                            reservation__date_fin__gte=date_debut_dt,
                            reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                        )
                        .exclude(reservation__demandes=demande)  # Exclure la demande actuelle
                        .select_related('reservation__client')
                        .first()
                    )
                    if rl:
                        conflit = {
                            'type':  'reservation',
                            'label': rl.reservation.nom,
                            'client_nom': rl.reservation.client.nom,
                            'debut': rl.reservation.date_debut,
                            'fin':   rl.reservation.date_fin,
                        }

            faces_avec_statut.append({
                'face':    face,
                'statut':  statut,
                'conflit': conflit,
            })

        return faces_avec_statut


# ══════════════════════════════════════════════════════════════════════════════
# Traitement (validation) d'une demande
# ══════════════════════════════════════════════════════════════════════════════

class DemandeTraiterView(StaffOnlyMixin, View):
    """
    POST : crée le Client (si nouveau) + Reservation + ReservationLigne
           puis valide la demande et envoie les emails.
    """
    template_name = 'staff/demande_detail.html'

    def post(self, request, uuid):
        demande = get_object_or_404(DemandeReservation, uuid=uuid)

        if demande.est_traitee:
            messages.warning(request, "Cette demande a déjà été traitée.")
            return redirect('staff:demande_detail', uuid=uuid)

        # ── Récupération des données ──────────────────────────────────────
        client_pk       = request.POST.get('client_pk', '').strip()
        nouveau_client  = request.POST.get('nouveau_client') == '1'
        face_ids        = request.POST.getlist('faces')
        nom_reservation = request.POST.get(
            'nom_reservation',
            demande.nom_campagne or f"Réservation {demande.reference}"
        ).strip()

        # ── Dates ─────────────────────────────────────────────────────────
        try:
            date_debut_str = request.POST.get('date_debut', str(demande.date_debut_souhaitee))
            date_fin_str   = request.POST.get('date_fin', str(demande.date_fin_souhaitee))
            
            date_debut_d = date_type.fromisoformat(date_debut_str)
            date_fin_d   = date_type.fromisoformat(date_fin_str)
            
            date_debut = timezone.make_aware(
                datetime_type.combine(date_debut_d, datetime_type.min.time())
            )
            date_fin = timezone.make_aware(
                datetime_type.combine(date_fin_d, datetime_type.max.time())
            )
        except (ValueError, TypeError) as e:
            messages.error(request, f"Dates invalides : {e}")
            return redirect('staff:demande_detail', uuid=uuid)

        if not face_ids:
            messages.error(request, "Sélectionnez au moins une face.")
            return redirect('staff:demande_detail', uuid=uuid)

        try:
            with transaction.atomic():
                # 1. ── Récupérer ou créer le client ──────────────────────
                if nouveau_client:
                    client = Client.objects.create(
                        nom       = demande.societe or demande.nom_contact,
                        email     = demande.email,
                        telephone = demande.telephone,
                        actif     = True,
                    )
                    logger.info(f"[Demande {demande.reference}] Client créé : {client.nom}")
                elif client_pk:
                    client = get_object_or_404(Client, pk=client_pk)
                else:
                    messages.error(request, "Sélectionnez un client ou créez-en un nouveau.")
                    return redirect('staff:demande_detail', uuid=uuid)

                # 2. ── Créer la Reservation ──────────────────────────────
                reservation = Reservation.objects.create(
                    client      = client,
                    nom         = nom_reservation,
                    date_debut  = date_debut,
                    date_fin    = date_fin,
                    statut      = STATUT_CONFIRMEE,
                    created_by  = request.user,
                )
                logger.info(f"[Demande {demande.reference}] Réservation créée : {reservation.reference}")

                # 3. ── Créer les ReservationLigne avec validation ────────
                faces = FacePanneau.objects.filter(
                    pk__in=face_ids
                ).select_related('support')
                
                erreurs = []
                lignes  = []
                
                for face in faces:
                    ligne = ReservationLigne(
                        reservation=reservation,
                        support=face.support,
                        face=face,
                    )
                    
                    # Vérifier les conflits
                    conflit = ReservationLigne.objects.filter(
                        face=face,
                        reservation__date_debut__lt=date_fin,
                        reservation__date_fin__gt=date_debut,
                        reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
                    ).exclude(reservation=reservation).first()
                    
                    if conflit:
                        erreurs.append(
                            f"Face {face.support.code}-{face.label} : "
                            f"conflit avec {conflit.reservation.client.nom}"
                        )
                    else:
                        lignes.append(ligne)

                if erreurs:
                    messages.error(request, f"Conflits détectés : {', '.join(erreurs)}")
                    return redirect('staff:demande_detail', uuid=uuid)

                if lignes:
                    ReservationLigne.objects.bulk_create(lignes)
                    logger.info(f"[Demande {demande.reference}] {len(lignes)} lignes créées")

                # 4. ── Valider la demande ────────────────────────────────
                demande.valider(request.user, reservation, client)

                # 5. ── Envoyer les emails ────────────────────────────────
                self._send_confirmation_emails(demande, reservation, date_debut_d, date_fin_d)

        except Exception as exc:
            logger.error(f"Erreur traitement demande {uuid} : {exc}", exc_info=True)
            messages.error(request, f"Erreur : {exc}")
            return redirect('staff:demande_detail', uuid=uuid)

        messages.success(
            request,
            f"✅ Demande {demande.reference} validée — Réservation {reservation.reference} créée."
        )
        return redirect('staff:demande_detail', uuid=uuid)

    def _send_confirmation_emails(self, demande, reservation, date_debut_d, date_fin_d):
        """Envoie les emails de confirmation au visiteur."""
        try:
            # Email au visiteur
            send_mail(
                subject=f"Confirmation de réservation — {demande.reference} | Régie INTEGRAL",
                message=(
                    f"Bonjour {demande.nom_contact},\n\n"
                    f"Votre demande de réservation {demande.reference} a été validée ✅\n\n"
                    f"Détails de votre réservation :\n"
                    f"  Référence : {reservation.reference}\n"
                    f"  Période : {date_debut_d:%d/%m/%Y} → {date_fin_d:%d/%m/%Y} "
                    f"({demande.duree_jours()} jours)\n"
                    f"  Campagne : {demande.nom_campagne or 'Non spécifiée'}\n\n"
                    f"Notre équipe vous contactera bientôt pour les détails de la mise en place.\n\n"
                    f"Cordialement,\n"
                    f"Équipe Régie INTEGRAL\n"
                    f"📞 +226 XX XX XX XX\n"
                    f"✉️ contact@integral.bf"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[demande.email],
                fail_silently=False,
            )
            logger.info(f"Email confirmation envoyé à {demande.email}")
        except Exception as e:
            logger.warning(f"Erreur envoi email confirmation : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Refus d'une demande
# ══════════════════════════════════════════════════════════════════════════════

class DemandeRefuserView(StaffOnlyMixin, View):
    """
    POST : refuse la demande et envoie un email au visiteur.
    """

    def post(self, request, uuid):
        demande = get_object_or_404(DemandeReservation, uuid=uuid)

        if demande.est_traitee:
            messages.warning(request, "Cette demande a déjà été traitée.")
            return redirect('staff:demande_detail', uuid=uuid)

        notes = request.POST.get('notes_staff', '').strip()
        demande.refuser(request.user, notes)

        logger.info(f"Demande {demande.reference} refusée par {request.user.username}")

        # Email de refus au visiteur
        try:
            send_mail(
                subject=f"Demande de réservation — {demande.reference} | Régie INTEGRAL",
                message=(
                    f"Bonjour {demande.nom_contact},\n\n"
                    f"Nous avons examiné votre demande {demande.reference} "
                    f"pour la période {demande.date_debut_souhaitee:%d/%m/%Y} → "
                    f"{demande.date_fin_souhaitee:%d/%m/%Y}.\n\n"
                    f"Malheureusement, nous ne sommes pas en mesure de la satisfaire "
                    f"pour le moment.\n\n"
                    + (f"Raison : {notes}\n\n" if notes else "")
                    + f"N'hésitez pas à nous recontacter pour une autre période.\n\n"
                    f"Cordialement,\n"
                    f"Équipe Régie INTEGRAL\n"
                    f"📞 +226 XX XX XX XX"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[demande.email],
                fail_silently=False,
            )
            logger.info(f"Email refus envoyé à {demande.email}")
        except Exception as e:
            logger.warning(f"Erreur envoi email refus : {e}")

        messages.success(request, f"❌ Demande {demande.reference} refusée et email envoyé.")
        return redirect('staff:demande_detail', uuid=uuid)


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