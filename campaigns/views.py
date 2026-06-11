import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count
from django.db.models.deletion import ProtectedError
from django.utils import timezone
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
from .models import Client, Contrat, Campagne, LigneCampagne, CampagneVisuel,ReservationPanneau
from inventory.models import Support, FacePanneau, EcranNumerique
from .forms import ClientForm, ContratForm, CampagneForm, LigneCampagneForm
# from .mixins import *


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

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'campaigns/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # ── Filtre ville ──────────────────────────────────────────
        ville_active = self.request.GET.get("ville", "").strip()

        for campagne in Campagne.objects.filter(statut__in=['a_venir', 'en_cours']):
            campagne.auto_update_statut()

        def codes_par_type(type_label, etat=None):
            """Retourne les codes de format définis pour un type donné"""
            codes = [
                code for code, label in Support.FORMAT_CHOICES
                if ' — ' in label and label.split(' — ')[1] == type_label
            ]
            return codes

        codes_standard = codes_par_type('Standard', etat=Support.ETAT_BON)
        codes_geants   = codes_par_type('Géant',    etat=Support.ETAT_BON)
        codes_sucettes = codes_par_type('Sucette',  etat=Support.ETAT_BON)
        codes_gm       = codes_par_type('Grand Marché', etat=Support.ETAT_BON)

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
            

        nb_panneaux     = supports.filter(type_support=Support.TYPE_PANNEAU).count()
        nb_panneaux_bon = supports.filter(type_support=Support.TYPE_PANNEAU, etat=Support.ETAT_BON).count()
        nb_ecrans       = supports.filter(type_support=Support.TYPE_ECRAN).count()
        nb_ecrans_bon   = supports.filter(type_support=Support.TYPE_ECRAN, etat=Support.ETAT_BON).count()
        nb_supports_panne = supports.filter(etat=Support.ETAT_PANNE).count()
        nb_supports_bon   = supports.filter(etat=Support.ETAT_BON).count()
        nb_standard = supports.filter(format__in=codes_standard, etat=Support.ETAT_BON).count()
        nb_geants   = supports.filter(format__in=codes_geants, etat=Support.ETAT_BON).count()
        nb_sucettes = supports.filter(format__in=codes_sucettes, etat=Support.ETAT_BON).count()
        nb_gm       = supports.filter(format__in=codes_gm, etat=Support.ETAT_BON).count()

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
            ReservationPanneau.objects.filter(
                date_fin__gte=today,
            ).values_list('face_id', flat=True)
        )

        def pct(codes):
            faces_qs = FacePanneau.objects.filter(support__format__in=codes)
            if ville_active:
                faces_qs = faces_qs.filter(support__ville__iexact=ville_active)
            total = faces_qs.count()
            if total == 0:
                return 0, 0, 0, 0, 0, 0, 0, 0

            nb_panne   = faces_qs.filter(etat=Support.ETAT_PANNE).count()
            faces_bon  = faces_qs.filter(etat=Support.ETAT_BON)
            total_bon  = faces_bon.count()

            nb_occupe  = faces_bon.filter(pk__in=faces_occupees_ids).count()
            nb_reserve = faces_bon.filter(
                pk__in=faces_reservees_ids
            ).exclude(
                pk__in=faces_occupees_ids
            ).count()
            nb_libre   = total_bon - nb_occupe - nb_reserve

            return (
                round(nb_occupe  / total * 100) if total else 0,
                round(nb_reserve / total * 100) if total else 0,
                round(nb_libre   / total * 100) if total else 0,
                nb_occupe,
                nb_reserve,
                nb_libre,
                nb_panne,   # ← nouveau
                total,
            )
    
        o_std, r_std, d_std, nbo_std, nbr_std, nbl_std, npt_std, nbt_std = pct(codes_standard)
        o_geo, r_geo, d_geo, nbo_geo, nbr_geo, nbl_geo, npt_geo, nbt_geo = pct(codes_geants)
        o_suc, r_suc, d_suc, nbo_suc, nbr_suc, nbl_suc, npt_suc, nbt_suc = pct(codes_sucettes)
        o_gm,  r_gm,  d_gm,  nbo_gm,  nbr_gm,  nbl_gm,  npt_gm,  nbt_gm  = pct(codes_gm)
        # Totaux globaux toutes formats confondus
        total_faces_occupe  = nbo_std + nbo_geo + nbo_suc + nbo_gm
        total_faces_reserve = nbr_std + nbr_geo + nbr_suc + nbr_gm
        total_faces_libre   = nbl_std + nbl_geo + nbl_suc + nbl_gm
        total_faces_panne   = npt_std + npt_geo + npt_suc + npt_gm
        total_faces         = nbt_std + nbt_geo + nbt_suc + nbt_gm

        format_stats_json = json.dumps([
            {
                'label'     : f'Standard ({nbt_std} faces)',
                'occupe'    : o_std,
                'reserve'   : r_std,
                'disponible': d_std,
                'panne'     : round(npt_std / nbt_std * 100) if nbt_std else 0,  # ← %
                'nb_occupe' : nbo_std,
                'nb_reserve': nbr_std,
                'nb_libre'  : nbl_std,
                'nb_panne'  : npt_std,
            },
            {
                'label'     : f'Géants ({nbt_geo} faces)',
                'occupe'    : o_geo,
                'reserve'   : r_geo,
                'disponible': d_geo,
                'panne'     : round(npt_geo / nbt_geo * 100) if nbt_geo else 0,
                'nb_occupe' : nbo_geo,
                'nb_reserve': nbr_geo,
                'nb_libre'  : nbl_geo,
                'nb_panne'  : npt_geo,
            },
            {
                'label'     : f'Sucettes ({nbt_suc} faces)',
                'occupe'    : o_suc,
                'reserve'   : r_suc,
                'disponible': d_suc,
                'panne'     : round(npt_suc / nbt_suc * 100) if nbt_suc else 0,
                'nb_occupe' : nbo_suc,
                'nb_reserve': nbr_suc,
                'nb_libre'  : nbl_suc,
                'nb_panne'  : npt_suc,
            },
            {
                'label'     : f'Grand Marché ({nbt_gm} faces)',
                'occupe'    : o_gm,
                'reserve'   : r_gm,
                'disponible': d_gm,
                'panne'     : round(npt_gm / nbt_gm * 100) if nbt_gm else 0,
                'nb_occupe' : nbo_gm,
                'nb_reserve': nbr_gm,
                'nb_libre'  : nbl_gm,
                'nb_panne'  : npt_gm,
            },
        ])
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
            'nb_ecrans'      : nb_ecrans,
            'nb_standard'    : nb_standard,
            'face_standard'  : nbt_std,
            'nb_geants'      : nb_geants,
            'face_geants'    : nbt_geo,
            'nb_sucettes'    : nb_sucettes,
            'face_sucettes'  : nbt_suc,
            'nb_gm'          : nb_gm,
            'face_gm'        : nbt_gm,
            'total_faces'  : total_faces,
            'total_faces_occupe'  : total_faces_occupe,
            'total_faces_reserve' : total_faces_reserve,
            'total_faces_libre'   : total_faces_libre,
            'total_faces_panne'   : total_faces_panne,
            

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
            'url': reverse('support_detail', args=[s.pk])
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
                'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'panneau'),
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
        if self.request.user.is_admin:
            context = super().get_context_data(**kwargs)
            client = self.object
            campagnes = client.campagnes.all().order_by('-date_debut')
            contrats = client.contrats.all().order_by('-date_debut')
            reservations = client.reservations.all().order_by('-date_debut')
            campagnes_actives = [c for c in client.campagnes.all() if c.statut == 'en_cours']
            client.campagnes_stats = {
                'panneau': len([c for c in campagnes_actives if c.type_support == 'panneau']),
                'ecran': len([c for c in campagnes_actives if c.type_support == 'ecran']),
                'total': len(campagnes_actives),
            }
            client.spots_stats = {
                'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'panneau'),
                'ecran': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'ecran'),
                'total': sum(c.calculer_nombre_spots() for c in campagnes_actives),
            }
            context.update({
                'campagnes': campagnes,
                'contrats': contrats,
                'reservations': reservations,
            })
        else:
            context = super().get_context_data(**kwargs)
            client = self.object
            campagnes = client.campagnes.filter(actif=True).order_by('-date_debut')
            contrats = client.contrats.filter(archive=True).order_by('-date_debut')
            campagnes_actives = [c for c in campagnes if c.statut == 'en_cours' and c.actif]
            client.campagnes_stats = {
                'panneau': len([c for c in campagnes_actives if c.type_support == 'panneau']),
                'ecran': len([c for c in campagnes_actives if c.type_support == 'ecran']),
                'total': len(campagnes_actives),
            }
            client.spots_stats = {
                'panneau': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'panneau'),
                'ecran': sum(c.calculer_nombre_spots() for c in campagnes_actives if c.type_support == 'ecran'),
                'total': sum(c.calculer_nombre_spots() for c in campagnes_actives),
            }
            context.update({
                'campagnes': campagnes,
                'contrats': contrats,
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
        if type_support in ['panneau', 'ecran']:
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


# ── Lignes de Campagne ────────────────────────────────────────────────────────


class SupportBulkActionView(StaffRequiredMixin, View):
    template_name = 'campaigns/supports_add_bulk.html'

    def get_campagne(self, campagne_pk):
        return get_object_or_404(Campagne, pk=campagne_pk)

    def get(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = campagne.type_support
        
        is_modification = campagne.lignes.exists()
        mode_title = "Modifier la sélection" if is_modification else "Ajouter des supports"
        supports_enrichis = []

        if type_support == 'panneau':
            supports_qs = Support.objects.filter(type_support='panneau', etat='bon').prefetch_related('faces')
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
        else:
            supports_qs = Support.objects.filter(type_support='ecran', etat='bon')
            selectionnes = list(campagne.lignes.values_list('support_id', flat=True))

        return render(request, self.template_name, {
            'campagne': campagne,
            'supports': supports_qs,
            'selectionnes': selectionnes,
            'mode_title': mode_title,
            'is_modification': is_modification,
            'supports_enrichis': supports_enrichis,
        })
    def post(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        type_support = campagne.type_support

        if type_support == 'panneau':
            face_ids = request.POST.getlist('faces')
            
            # Synchronisation : on retire les faces décochées
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
            # Synchronisation pour écrans
            campagne.lignes.filter(face__isnull=True).exclude(support_id__in=support_ids).delete()
            for support_id in support_ids:
                support = get_object_or_404(Support, pk=support_id, type_support='ecran')
                LigneCampagne.objects.update_or_create(
                    campagne=campagne, support=support, defaults={'ordre_dans_boucle': 0}
                )

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
    
    

class ReservationPanneauBulkActionView(StaffRequiredMixin, View):
    template_name = 'campaigns/reservations_add_bulk.html'

    def get_client(self, client_pk):
        return get_object_or_404(Client, pk=client_pk)

    def get(self, request, client_pk):
        client = self.get_client(client_pk)
        
        # Détection du mode (si le client a déjà des réservations en cours ou à venir)
        now = timezone.now()
        reservations_actives = client.reservations.filter(date_fin__gte=now).order_by('date_debut')
        is_modification = reservations_actives.exists()
        
        mode_title = "Modifier les réservations" if is_modification else "Réserver des panneaux"

        # Initialisation des variables de dates pour le template
        date_debut_iso = ""
        date_fin_iso = ""

        if is_modification:
            # On récupère la date de début de la première réservation et la date de fin de la plus lointaine
            premiere_resa = reservations_actives.first()
            derniere_resa = client.reservations.filter(date_fin__gte=now).order_by('-date_fin').first()
            
            # Formatage indispensable pour l'input HTML5 datetime-local (YYYY-MM-DDTHH:MM)
            if premiere_resa and premiere_resa.date_debut:
                date_debut_iso = premiere_resa.date_debut.strftime('%Y-%m-%dT%H:%M')
            if derniere_resa and derniere_resa.date_fin:
                date_fin_iso = derniere_resa.date_fin.strftime('%Y-%m-%dT%H:%M')

        # On ne récupère que les panneaux physiques en bon état
        supports = Support.objects.filter(type_support='panneau', etat='bon').prefetch_related('faces')
        
        # Liste des IDs de faces déjà réservées par ce client
        selectionnes = list(reservations_actives.values_list('face_id', flat=True))
        
        # Les clients associés aux faces réservées
        clt = Client.objects.filter(reservations__face_id__in=selectionnes).distinct()
        
        return render(request, self.template_name, {
            'client': client,
            'supports': supports,
            'selectionnes': selectionnes,
            'clt': clt,
            'mode_title': mode_title,
            'is_modification': is_modification,
            'date_debut': date_debut_iso,  # <-- Transmis proprement au format HTML5
            'date_fin': date_fin_iso,      # <-- Transfinancé proprement au format HTML5
        })

    def post(self, request, client_pk):
        client = self.get_client(client_pk)
        
        # Récupération des IDs des faces cochées et des dates depuis le formulaire
        face_ids = [int(fid) for fid in request.POST.getlist('faces') if fid.isdigit()]
        date_debut_str = request.POST.get('date_debut')
        date_fin_str = request.POST.get('date_fin')

        if not date_debut_str or not date_fin_str:
            messages.error(request, "Les dates de début et de fin sont obligatoires.")
            return redirect(request.path)

        # Conversion des dates du formulaire (Format attendu HTML5: YYYY-MM-DDTHH:MM)
        try:
            date_debut = timezone.make_aware(timezone.datetime.fromisoformat(date_debut_str))
            date_fin = timezone.make_aware(timezone.datetime.fromisoformat(date_fin_str))
        except ValueError:
            messages.error(request, "Format de date invalide.")
            return redirect(request.path)

        # Utilisation d'une transaction atomique pour éviter les états inconsistants en BDD
        with transaction.atomic():
            # 1. NETTOYAGE : Supprime les réservations de faces qui ont été décochées
            client.reservations.filter(face__isnull=False).exclude(face_id__in=face_ids).delete()

            # 2. OPTIMISATION : Trouver quelles faces parmi celles cochées existent déjà pour ce client
            faces_deja_reservees = set(
                client.reservations.filter(face_id__in=face_ids).values_list('face_id', flat=True)
            )

            # Id des faces réellement nouvelles à créer
            faces_a_creer_ids = set(face_ids) - faces_deja_reservees

            if faces_a_creer_ids:
                # Récupération de toutes les faces d'un coup (1 seule requête SQL au lieu d'une boucle)
                faces_objects = FacePanneau.objects.filter(id__in=faces_a_creer_ids).select_related('support')
                
                nouvelles_reservations = []
                for face in faces_objects:
                    instance_reservation = ReservationPanneau(
                        client=client,
                        support=face.support,
                        face=face,
                        date_debut=date_debut,
                        date_fin=date_fin
                    )
                    
                    # Déclenche la validation clean() du modèle (gestion des conflits de date)
                    try:
                        instance_reservation.full_clean()
                        nouvelles_reservations.append(instance_reservation)
                    except ValidationError as e:
                        messages.error(request, f"Erreur pour le panneau {face.support.code} - Face {face.label} : {e.messages[0]}")
                        return redirect(request.path)

                # 3. ENREGISTREMENT EN MASSE : Insertion ultra-rapide en BDD
                if nouvelles_reservations:
                    ReservationPanneau.objects.bulk_create(nouvelles_reservations)
                    
                    # Log global (optionnel, selon votre système d'audit log)
                    # log_action(request, AuditLog.ACTION_UPDATE, 'client', obj=client, detail=f"Réservation de {len(nouvelles_reservations)} panneaux")

        messages.success(request, "Les réservations de panneaux ont été mises à jour avec succès.")
        return redirect('client_detail', pk=client_pk)
    

from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from .models import ReservationPanneau

# 1. Corriger __str__ dans ReservationPanneau
def __str__(self):
    return f"Réservation de {self.support} pour {self.client}"  # self.panneau → self.support

# 2. Passer client_pk à l'API de dispo
def verifier_dispo_faces_api(request, client_pk=None):
    date_debut_str = request.GET.get('date_debut')
    date_fin_str = request.GET.get('date_fin')
    
    if not date_debut_str or not date_fin_str:
        return JsonResponse({'faces_occupees': [], 'message': 'Dates manquantes'}, status=200)
        
    try:
        date_debut = parse_datetime(date_debut_str)
        date_fin = parse_datetime(date_fin_str)
        
        if date_debut and timezone.is_naive(date_debut):
            date_debut = timezone.make_aware(date_debut)
        if date_fin and timezone.is_naive(date_fin):
            date_fin = timezone.make_aware(date_fin)
            
    except (ValueError, TypeError):
        return JsonResponse({'faces_occupees': [], 'error': 'Format de date invalide'}, status=400)
        
    if not date_debut or not date_fin:
        return JsonResponse({'faces_occupees': [], 'error': 'Impossible de parser les dates'}, status=400)

    # On exclut les réservations du client courant (pour le mode modification)
    qs = ReservationPanneau.objects.filter(
        date_debut__lt=date_fin,
        date_fin__gt=date_debut
    )
    if client_pk:
        qs = qs.exclude(client_id=client_pk)
    
    conflits = qs.values_list('face_id', flat=True)
    return JsonResponse({'faces_occupees': list(set(conflits))})

