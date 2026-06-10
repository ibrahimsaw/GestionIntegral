import json
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import ListView, DetailView, DeleteView, TemplateView, FormView, View

from accounts.decorators import *
from accounts.models import AuditLog
from accounts.audit import log_action
from campaigns.mixins import StaffRequiredMixin
from campaigns.models import *
from .forms import *
from .models import *
from accounts.models import User
from django.db.models import Q
from django.utils.safestring import mark_safe

# ── API GeoJSON pour Leaflet ──────────────────────────────────────────────────

class ApiGeojsonView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        """Retourne tous les supports en GeoJSON avec couleur dynamique."""
        type_filtre = request.GET.get('type', '')
        etat_filtre = request.GET.get('etat', '')
        type_panneau_filtre = request.GET.get('type_panneau', '')
        
        qs = Support.objects.prefetch_related('faces').all()
        
        if type_filtre:
            qs = qs.filter(type_support=type_filtre)  # ✅ corrigé : 'type' au lieu de 'type_support'
        if etat_filtre:
            qs = qs.filter(etat=etat_filtre)
        if type_panneau_filtre:
            codes_valides = [
                code for code, label in Support.FORMAT_CHOICES 
                if ' — ' in label and label.split(' — ')[1] == type_panneau_filtre
            ]
            qs = qs.filter(format__in=codes_valides)
        features = []
        for s in qs:
            d = s.disponibilite_json()
            
            # ✅ S'assurer que type_panneau est bien exposé dans les properties
            if 'type_panneau' not in d:
                d['type_panneau'] = s.type_panneau if hasattr(s, 'type_panneau') else None

            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [float(s.longitude), float(s.latitude)]},
                'properties': d,
            })

        return JsonResponse({'type': 'FeatureCollection', 'features': features})


api_geojson = ApiGeojsonView.as_view()


"""
inventory/views.py — vue api_support_popup mise à jour.

Nouvelles données retournées :
  Panneau → chaque face inclut :
    - visuel_url   : visuel de la LigneCampagne (ou du Campagne si absent)
    - campagne_nom, campagne_ref, campagne_statut
    - date_debut / date_fin

  Écran → chaque spot inclut :
    - visuel_url   : visuel de la LigneCampagne (ou du Campagne si absent)
    - campagne_nom, campagne_ref
"""


class ApiSupportPopupView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        """API JSON — données complètes du support pour le side panel carte."""
        support = get_object_or_404(Support, pk=pk)
        today = timezone.now().date()

        data = {
            'id':                support.pk,
            'code':              support.code,
            'nom':               support.nom,
            'type':              support.type_support,
            'type_panneau':      support.type_panneau if support.type_support == 'panneau' else None,
            'etat':              support.etat,
            'adresse':           support.adresse,
            'ville':             support.ville,
            'quartier':          support.quartier,
            'date_installation': support.date_installation.strftime('%d/%m/%Y') if support.date_installation else None,
            'url_detail':        f'/inventory/{support.pk}/',
            'url_edit':          f'/inventory/{support.pk}/modifier/',
            'photo':             support.photo_principale.url if support.photo_principale else None,
            'photos_maintenance': [],
        }

        # Construire la liste de photos en vérifiant que le fichier existe réellement
        photos_list = []
        for pm in support.maintenances.order_by('-date_intervention')[:6]:
            try:
                has_file = bool(pm.photo and getattr(pm.photo, 'name', ''))
            except Exception:
                has_file = False
            if not has_file:
                continue
            photos_list.append({
                'url': pm.photo.url,
                'legende': pm.description or f"Maintenance du {pm.date_intervention.strftime('%d/%m/%Y')}",
                'date': pm.date_intervention.strftime('%d/%m/%Y'),
            })
        data['photos_maintenance'] = photos_list

        if support.type_support == 'panneau':
            from campaigns.models import LigneCampagne, ReservationPanneau
            faces_data = []
            for face in support.faces.all():
                # ── Statut consolidé ──────────────────────────────────────
                statut = face.get_statut()  # 'panne' | 'occupe' | 'reserve' | 'libre'

                lc = None
                reservation = None
                visuel_url  = None
                visuels_urls = []

                if statut == 'occupe':
                    lc = LigneCampagne.objects.filter(
                        face=face,
                        campagne__date_debut__lte=today,
                        campagne__date_fin__gte=today,
                        campagne__statut__in=['en_cours', 'a_venir'],
                    ).select_related('campagne__client').first()

                    if lc:
                        if lc.visuel:
                            visuel_url = lc.visuel.url
                            visuels_urls.append(lc.visuel.url)
                        else:
                            premier_visuel = lc.campagne.visuels.first()
                            if premier_visuel:
                                visuel_url = premier_visuel.fichier.url
                        for v in lc.campagne.visuels.all():
                            if v.fichier.url not in visuels_urls:
                                visuels_urls.append(v.fichier.url)

                elif statut == 'reserve':
                    reservation = ReservationPanneau.objects.filter(
                        face=face,
                        date_fin__gte=timezone.now(),
                    ).select_related('client').first()

                faces_data.append({
                    'label'          : face.label,
                    'format'         : support.format,
                    'eclairage'      : face.get_eclairage_display(),
                    'statut'         : statut,                          # ← nouveau
                    'disponible'     : statut == 'libre',               # rétrocompat JS
                    # ── Campagne (si occupée) ──────────────────────────
                    'client'         : lc.campagne.client.nom                        if lc else None,
                    'campagne_nom'   : lc.campagne.nom                               if lc else None,
                    'campagne_ref'   : lc.campagne.reference                         if lc else None,
                    'campagne_statut': lc.campagne.get_statut_display()              if lc else None,
                    'date_debut'     : lc.campagne.date_debut.strftime('%d/%m/%Y')   if lc else None,
                    'date_fin'       : lc.campagne.date_fin.strftime('%d/%m/%Y')     if lc else None,
                    'visuel_url'     : visuel_url,
                    'visuels_urls'   : visuels_urls,
                    # ── Réservation (si réservée) ──────────────────────
                    'client_reserve' : reservation.client.nom                        if reservation else None,
                    'date_debut'     : reservation.date_debut.strftime('%d/%m/%Y')   if reservation and statut == 'reserve' else (lc.campagne.date_debut.strftime('%d/%m/%Y') if lc else None),
                    'date_fin'       : reservation.date_fin.strftime('%d/%m/%Y')     if reservation and statut == 'reserve' else (lc.campagne.date_fin.strftime('%d/%m/%Y') if lc else None),
                })
            data['faces'] = faces_data
        elif support.type_support == 'ecran' and hasattr(support, 'ecran_info'):
            from campaigns.models import LigneCampagne
            ecran = support.ecran_info
            taux = ecran.taux_occupation_pourcentage(today)
            total_j = ecran.secondes_totales_disponibles_jour
            hours_j = total_j / 3600 if total_j > 0 else 1
            libres_1h = max(0, round(3600 - ecran.calculer_occupation_reelle(today) / hours_j))

            data['ecran'] = {
                'resolution':         ecran.get_resolution_display(),
                'cellule':            ecran.get_cellule_display(),
                'taux_occupation':    taux,
                'secondes_libres_1h': libres_1h,
                'heure_debut':        ecran.heure_allumage.strftime('%H:%M'),
                'heure_fin':          ecran.heure_extinction.strftime('%H:%M'),
            }

            spots_qs = LigneCampagne.objects.filter(
                support=support,
                campagne__date_debut__lte=today,
                campagne__date_fin__gte=today,
                campagne__statut__in=['en_cours', 'a_venir'],
            ).select_related('campagne__client').order_by('ordre_dans_boucle')

            spots_data = []
            for sp in spots_qs:
                visuel_url = None
                visuels_urls = []
                if sp.visuel:
                    visuel_url = sp.visuel.url
                    visuels_urls.append(sp.visuel.url)
                else:
                    premier_visuel = sp.campagne.visuels.first()
                    if premier_visuel:
                        visuel_url = premier_visuel.fichier.url
                
                for v in sp.campagne.visuels.all():
                    if v.fichier.url not in visuels_urls:
                        visuels_urls.append(v.fichier.url)

                spots_data.append({
                    'client':          sp.campagne.client.nom,
                    'campagne_nom':    sp.campagne.nom,
                    'campagne_ref':    sp.campagne.reference,
                    'campagne_statut': sp.campagne.get_statut_display(),
                    'date_debut':      sp.campagne.date_debut.strftime('%d/%m/%Y'),
                    'date_fin':        sp.campagne.date_fin.strftime('%d/%m/%Y'),
                    'duree':           sp.campagne.duree_passage or 10,
                    'freq':            sp.campagne.frequence or 120,
                    'passages_heure':  3600 // (sp.campagne.frequence or 120),
                    'ordre':           sp.ordre_dans_boucle,
                    'visuel_url':      visuel_url,
                    'visuels_urls':    visuels_urls,
                })
            data['spots'] = spots_data

        return JsonResponse(data)


api_support_popup = ApiSupportPopupView.as_view()


class ApiFacesSupportView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        """Retourne les faces d'un panneau (pour formulaire dynamique)."""
        faces = FacePanneau.objects.filter(support_id=pk).values('id', 'label')
        return JsonResponse({'faces': list(faces)})


api_faces_support = ApiFacesSupportView.as_view()


# ── Carte principale ──────────────────────────────────────────────────────────

class CarteView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/carte.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['stats'] = {
            'total': Support.objects.count(),
            'bon': Support.objects.filter(etat='bon').count(),
            'maintenance': Support.objects.filter(etat='maintenance').count(),
            'panne': Support.objects.filter(etat='panne').count(),
            'panneaux': Support.objects.filter(type_support='panneau').count(),
            'ecrans': Support.objects.filter(type_support='ecran').count(),
        }
        context['cfg'] = settings.DESIGN_CONFIG
        return context


carte = CarteView.as_view()


from django.http import JsonResponse

def get_faces_support(request, support_id):
    faces = FacePanneau.objects.filter(support_id=support_id).values('id', 'label')
    data = [{'id': f['id'], 'label': f['label']} for f in faces]
    return JsonResponse({'faces': data})

# class TechnicienUpdateView(TechnicienRequiredMixin, FormView):
#     template_name = 'inventory/technicien_update.html'
#     form_class = MaintenanceForm

#     def dispatch(self, request, *args, **kwargs):
#         self.support = get_object_or_404(Support, pk=kwargs.get('pk'))
#         return super().dispatch(request, *args, **kwargs)

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context['support'] = self.support
#         return context

#     def form_valid(self, form):
#         maintenance = form.save(commit=False)
#         maintenance.support = self.support
#         maintenance.uploaded_by = self.request.user
#         maintenance.save()
#         if maintenance.etat_apres:
#             old_etat = self.support.etat
#             self.support.etat = maintenance.etat_apres
#             self.support.save()
#             log_action(
#                 self.request,
#                 AuditLog.ACTION_UPDATE,
#                 'inventory',
#                 obj=self.support,
#                 detail=f"Changement état support {self.support.code}: {old_etat} → {maintenance.etat_apres}",
#             )
#         messages.success(self.request, 'État mis à jour et photo enregistrée.')
#         return super().form_valid(form)

#     def get_success_url(self):
#         return reverse('support_detail', kwargs={'pk': self.support.pk})


class SupportListView(TechnicienStaffRequiredMixin, ListView):
    model = Support
    template_name = 'inventory/support_list.html'
    context_object_name = 'supports'
    paginate_by = 15
    
    occupation = {
        'occupe': 'Occupé',
        'libre': 'Libre',
        'reserve': 'Réservé',
        'total_occupe': 'Totalement occupé',
        'total_reserve': 'Totalement réservé',
        'non_reserve': 'Non réservé',
        'occupe_ou_reserve': 'Occupé ou réservé',
    }

    def get_queryset(self):
        queryset = Support.objects.prefetch_related('faces').select_related('ecran_info').all()
        q = self.request.GET.get('q', '')
        type_f = self.request.GET.get('type', '')
        etat_f = self.request.GET.get('etat', '')
        occupation_f = self.request.GET.get('occupation', '')
        type_panneau_f = self.request.GET.get('type_panneau', '')

        # 2. Filtres SQL (Rapides)
        if type_panneau_f:
            codes_valides = [
                code for code, label in Support.FORMAT_CHOICES 
                if ' — ' in label and label.split(' — ')[1] == type_panneau_f
            ]
            queryset = queryset.filter(format__in=codes_valides)

        if q:
            queryset = queryset.filter(
                Q(code__icontains=q) | Q(nom__icontains=q) | Q(adresse__icontains=q)
            )
        
        if type_f:
            queryset = queryset.filter(type_support=type_f)
            
        if etat_f:
            queryset = queryset.filter(etat=etat_f)

        if occupation_f in ['occupe','total_occupe', 'libre', 'reserve','total_reserve', 'non_reserve', 'occupe_ou_reserve']:
            if occupation_f == 'occupe':
                # Occupé physiquement (réservé ou non, l'occupation prime)
                queryset = [s for s in queryset if s.is_occupe()]
            elif occupation_f == 'total_occupe':
                # Totalement occupé = toutes les faces sont occupées (même si certaines sont réservées)
                queryset = [s for s in queryset if s.is_occupe() and not s.is_libre()]
            elif occupation_f == 'reserve':
                # Réservé = non occupé physiquement MAIS a une réservation active/à venir
                queryset = [s for s in queryset if s.is_reserve()]
            elif occupation_f == 'total_reserve':
                # Totalement réservé = aucune face n'est ni libre ni occupée (toutes sont réservées)
                queryset = [s for s in queryset if s.is_reserve() and not s.is_libre() and not s.is_occupe()]
            elif occupation_f == 'non_reserve':
                # Non réservé = ni occupé ni réservé
                queryset = [s for s in queryset if not s.is_reserve()]
            elif occupation_f == 'occupe_ou_reserve':
                # Occupé ou réservé = soit occupé, soit réservé
                queryset = [s for s in queryset if s.is_occupe() or s.is_reserve()]
            else:  # libre
                # Libre = ni occupé ni réservé
                queryset = [s for s in queryset if not s.is_occupe() and not s.is_reserve()]
        
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Préparation des choix pour le template
        type_panneau_choices = sorted(list(set(
            label.split(' — ')[1] for code, label in Support.FORMAT_CHOICES if ' — ' in label
        )))

        # On renvoie les variables pour que le formulaire de filtre reste rempli
        context.update({
            'q': self.request.GET.get('q', ''),
            'type_f': self.request.GET.get('type', ''),
            'etat_f': self.request.GET.get('etat', ''),
            'occupation_f': self.request.GET.get('occupation', ''),
            'type_panneau_f': self.request.GET.get('type_panneau', ''),
            'type_panneau_choices': type_panneau_choices,
            'occupation_choices': self.occupation
        })
        return context
    


class SupportDetailView(ClientStaffRequiredMixin, DetailView):
    model = Support
    template_name = 'inventory/support_detail.html'
    context_object_name = 'support'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Filtre le support en fonction du user client
        if self.request.user.is_client:
            client = self.request.user.client_profile
            if client is None:
                raise PermissionDenied
            support = get_object_or_404(
                Support.objects.filter(lignes_campagne__campagne__client=client).distinct(),
                pk=self.kwargs['pk']
            )
        else:
            support = self.get_object()

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
        
        reservations = ReservationPanneau.objects.filter(
            support=support,
            date_fin__gte=timezone.now()
        ).select_related('client').order_by('date_debut')

        # ── Informations générales ─────────────────────────────────────────
        info_rows = [
            ('Code',         support.code),
            ('Type',         support.get_type_support_display()),
            ('Ville',        support.ville or '—'),
            ('Quartier',     support.quartier or '—'),
            ('Installation', support.date_installation.strftime('%d/%m/%Y')),
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
                # CORRECTION : datetime + client courant
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
                h_allumage   = ecran.heure_allumage.strftime('%H:%M')
                h_extinction = ecran.heure_extinction.strftime('%H:%M')
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
                    ('Cellule',    'bi-tv',           f'{ecran.cellule}',             'var(--text)'),
                    ('Occupation', 'bi-pie-chart',    f'{taux}%',                     'var(--color-primary)'),
                    ('Diffusion',  'bi-clock',        plage_str,                      'var(--color-primary)'),
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

from django.views.generic import CreateView
from django.db import transaction

class SupportCreateView(StaffRequiredMixin, CreateView):
    model = Support
    form_class = SupportForm
    template_name = 'inventory/support_form.html'

    def get_initial(self):
        # Récupère les coordonnées depuis l'URL (clic sur la carte)
        initial = super().get_initial()
        initial['latitude'] = self.request.GET.get('lat', '')
        initial['longitude'] = self.request.GET.get('lng', '')
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['ecran_form'] = EcranNumeriqueForm(self.request.POST, prefix='ecran')
        else:
            context['ecran_form'] = EcranNumeriqueForm(prefix='ecran')
        
        context['face_form'] = FacePanneauForm() # Pour le template dynamique
        context['title'] = "Nouveau Support"
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        ecran_form = context['ecran_form']
        
        with transaction.atomic():
            # 1. Sauvegarde du support
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            self.object.save()

            # 2. Logique selon le type
            type_support = self.object.type_support
            
            if type_support == 'panneau':
                nb_faces = form.cleaned_data.get('nb_faces', 2)
                for i in range(nb_faces):
                    label = chr(65 + i)
                    FacePanneau.objects.create(
                        support=self.object,
                        label=label,
                        eclairage=self.request.POST.get(f'face_{i}_eclairage', 'oui'),
                        notes=self.request.POST.get(f'face_{i}_notes', '')
                    )
            
            elif type_support == 'ecran' and ecran_form.is_valid():
                ecran = ecran_form.save(commit=False)
                ecran.support = self.object
                ecran.save()

        log_action(self.request, AuditLog.ACTION_CREATE, 'inventory', obj=self.object, detail=f"Création support: {self.object.code}")
        messages.success(self.request, f'Support {self.object.code} créé.')
        return redirect('support_detail', pk=self.object.pk)
    
from django.db import transaction
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import UpdateView


class SupportUpdateView(StaffRequiredMixin, UpdateView):
    model = Support
    form_class = SupportForm
    template_name = 'inventory/support_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        support = self.object  # UpdateView définit déjà self.object
        # Récupération de l'instance écran si elle existe
        ecran_instance = getattr(support, 'ecran_info', None)
        
        # Initialisation du formulaire écran avec préfixe pour le template
        if self.request.POST:
            context['ecran_form'] = EcranNumeriqueForm(
                self.request.POST, 
                self.request.FILES, 
                prefix='ecran', 
                instance=ecran_instance
            )
        else:
            context['ecran_form'] = EcranNumeriqueForm(prefix='ecran', instance=ecran_instance)
        
        # Pour le rendu JS des faces dans le template
        context['face_form'] = FacePanneauForm()
        context['title'] = f'Modifier — {support.code}'
        context['existing_faces'] = support.faces.all()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        ecran_form = context['ecran_form']
        support = self.object

        with transaction.atomic():
            # 1. Sauvegarde du support principal
            support = form.save()

            # 2. Cas ÉCRAN : Validation et sauvegarde
            if support.type_support == 'ecran':
                if ecran_form.is_valid():
                    e = ecran_form.save(commit=False)
                    e.support = support
                    e.save()
                else:
                    # Si le formulaire écran a des erreurs, on renvoie vers form_invalid
                    return self.form_invalid(form)
            
            # 3. Cas PANNEAU : Gestion dynamique des faces
            elif support.type_support == 'panneau':
                nb_faces = form.cleaned_data.get('nb_faces', 1)
                
                # Création/Mise à jour des faces basées sur les champs dynamiques du JS
                for i in range(nb_faces):
                    label = chr(65 + i)  # A, B, C...
                    data = {
                        'format': self.request.POST.get(f'face_{i}_format', '4x3'),
                        'eclairage': self.request.POST.get(f'face_{i}_eclairage', 'non'),
                        'notes': self.request.POST.get(f'face_{i}_notes', '')
                    }
                    FacePanneau.objects.update_or_create(
                        support=support, 
                        label=label, 
                        defaults=data
                    )
                
                # Nettoyage : supprimer les faces qui n'existent plus si on a réduit le nombre
                support.faces.filter(label__gt=chr(64 + nb_faces)).delete()

            # 4. Journalisation (Audit Log)
            log_action(
                self.request,
                AuditLog.ACTION_UPDATE,
                'inventory',
                obj=support,
                detail=f"Modification support: {support.code}"
            )

        messages.success(self.request, f'Le support {support.code} a été mis à jour.')
        return redirect('support_detail', pk=support.pk)

    def form_invalid(self, form):
        # On s'assure que les messages d'erreur apparaissent bien
        messages.error(self.request, "Veuillez corriger les erreurs dans le formulaire.")
        context = self.get_context_data()
        return super().form_invalid(form)

    

    
class SupportDeleteView(StaffRequiredMixin, DeleteView):
    model = Support
    template_name = 'partials/confirm_delete.html'
    context_object_name = 'obj'
    success_url = reverse_lazy('support_list')

    def delete(self, request, *args, **kwargs):
        # 1. On récupère l'objet avant la suppression pour le log
        support = cast(Support, self.get_object())
        code = support.code
        
        # 2. Exécution de la suppression réelle
        response = super().delete(request, *args, **kwargs)
        
        # 3. Log de l'action et message de succès
        log_action(
            request, 
            AuditLog.ACTION_DELETE, 
            'inventory', 
            detail=f"Suppression support: {code}"
        )
        messages.success(request, f'Le support {code} a été supprimé avec succès.')
        
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': 'Supprimer le support',
            'header': 'Suppression de support',
            'message_title': 'Supprimer ce support ?',
            'message_body': 'Vous êtes sur le point de supprimer le support',
            'hint': 'Cette opération supprimera définitivement le support et ses données associées.',
            'confirm_label': 'Supprimer le support',
            'cancel_url': reverse_lazy('support_detail', kwargs={'pk': self.object.pk}),
        })
        return context




class MaintenanceListView(LoginRequiredMixin, ListView):
    model               = Maintenance
    template_name       = 'inventory/maintenance_list.html'
    context_object_name = 'maintenances'
    paginate_by         = 20

    def get_queryset(self):
        qs = Maintenance.objects.select_related('support', 'face', 'effectue_par')

        # ✅ Filtre technicien → voit uniquement ses maintenances
        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)

        # ✅ Filtres GET
        support_id = self.request.GET.get('support')
        etat_apres = self.request.GET.get('etat')
        technicien = self.request.GET.get('technicien')

        if support_id: qs = qs.filter(support_id=support_id)
        if etat_apres: qs = qs.filter(etat_apres=etat_apres)
        if technicien:
            qs = qs.filter(effectue_par_id=technicien)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['etat_choices'] = Maintenance.ETAT_CHOICES
        context['supports']     = Support.objects.filter(actif=True).order_by('code')
        # ✅ Pour le filtre technicien, on affiche uniquement les techniciens qui ont des maintenances (ou tous si admin)
        if self.request.user.is_technicien:
            context['techniciens'] = User.objects.filter(
                maintenances__effectue_par=self.request.user
            ).distinct()
        else:
            context['techniciens'] = User.objects.filter(maintenances__isnull=False).distinct()
        return context


class MaintenanceDetailView(LoginRequiredMixin, DetailView):
    model               = Maintenance
    template_name       = 'inventory/maintenance_detail.html'
    context_object_name = 'maintenance'

    def get_queryset(self):
        qs = Maintenance.objects.select_related('support', 'face', 'effectue_par')
        # ✅ Technicien → voit uniquement ses maintenances
        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)
        return qs


class MaintenanceCreateView(LoginRequiredMixin, CreateView):
    model         = Maintenance
    form_class    = MaintenanceForm
    template_name = 'inventory/maintenance_form.html'
    success_url   = reverse_lazy('maintenance_list')

    # Dans MaintenanceCreateView et MaintenanceUpdateView
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        support_id = self.kwargs.get('pk') or self.request.GET.get('support')
        if support_id:
            kwargs['support_pk'] = support_id
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        # ✅ Pré-remplit le support si passé en URL ou GET
        support_id = self.kwargs.get('pk') or self.request.GET.get('support')
        if support_id:
            initial['support'] = support_id
        return initial

    def form_valid(self, form):
        support_id = self.kwargs.get('pk') or self.request.GET.get('support')
        if support_id and not form.instance.support_id:
            form.instance.support_id = support_id

        if self.request.user.is_technicien:
            form.instance.effectue_par = self.request.user

        response = super().form_valid(form)
        messages.success(self.request, f'Maintenance enregistrée pour {self.object.support.code}.')
        return response
    
    def form_invalid(self, form):
        messages.error(self.request, "Veuillez corriger les erreurs dans le formulaire.")
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Nouvelle maintenance'
        return context


class MaintenanceUpdateView(LoginRequiredMixin, UpdateView):
    model         = Maintenance
    form_class    = MaintenanceForm
    template_name = 'inventory/maintenance_form.html'
    success_url   = reverse_lazy('maintenance_list')

    def get_queryset(self):
        qs = Maintenance.objects.select_related('support', 'effectue_par')
        # ✅ Technicien → ne peut modifier que ses propres maintenances
        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)
        return qs

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'Maintenance mise à jour pour {self.object.support.code}.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Modifier maintenance — {self.object.support.code}'
        context['obj']   = self.object
        return context
    

class SupportPeriodesVanneView(LoginRequiredMixin, View):
    template_name = 'inventory/support_periodes_panne.html'

    def get(self, request, pk):
        support  = get_object_or_404(Support, pk=pk)
        periodes = support.get_periodes_panne()

        # ── Statistiques globales ─────────────────────────────────
        nb_total    = len(periodes)
        nb_resolues = sum(1 for p in periodes if p['resolue'])
        nb_en_cours = nb_total - nb_resolues

        duree_totale = sum(
            (p['duree'] for p in periodes if p['duree']),
            timedelta()
        )
        duree_moyenne = (
            duree_totale / nb_total if nb_total > 0 else timedelta()
        )

        # ── Stats par face (panneaux uniquement) ──────────────────
        stats_par_face = {}
        if support.type_support == Support.TYPE_PANNEAU:
            for face in support.faces.all():
                periodes_face = [p for p in periodes if p['face'] and p['face'].pk == face.pk]
                duree_face    = sum(
                    (p['duree'] for p in periodes_face if p['duree']),
                    timedelta()
                )
                stats_par_face[face.label] = {
                    'face'        : face,
                    'nb_pannes'   : len(periodes_face),
                    'nb_resolues' : sum(1 for p in periodes_face if p['resolue']),
                    'nb_en_cours' : sum(1 for p in periodes_face if not p['resolue']),
                    'duree_totale': duree_face,
                    'periodes'    : periodes_face,
                }

        context = {
            'support'       : support,
            'periodes'      : periodes,
            'nb_total'      : nb_total,
            'nb_resolues'   : nb_resolues,
            'nb_en_cours'   : nb_en_cours,
            'duree_totale'  : duree_totale,
            'duree_moyenne' : duree_moyenne,
            'stats_par_face': stats_par_face,
            'title'         : f'Périodes de panne — {support.code}',
        }
        return render(request, self.template_name, context)
    
class PeriodesParVueView(LoginRequiredMixin, View):
    template_name = 'inventory/periodes_panne_liste.html'

    def get(self, request):
        # ── Filtres ───────────────────────────────────────────────
        support_pk  = request.GET.get('support', '')
        face_label  = request.GET.get('face', '')
        statut_f    = request.GET.get('statut', '')   # 'resolue' | 'en_cours'
        date_debut  = request.GET.get('date_debut', '')
        date_fin    = request.GET.get('date_fin', '')

        # ── Construction des périodes depuis Maintenance ──────────
        # On part des maintenances groupées par (support, face)
        maints_qs = (
            Maintenance.objects
            .select_related('support', 'face')
            .order_by('support_id', 'face_id', 'date_intervention')
        )

        if support_pk:
            maints_qs = maints_qs.filter(support_id=support_pk)
        if face_label:
            maints_qs = maints_qs.filter(face__label=face_label)

        # ── Groupement par (support, face) ────────────────────────
        from itertools import groupby
        from operator import attrgetter

        periodes = []

        # Grouper par support puis par face
        def group_key(m):
            return (m.support_id, m.face_id)

        for (support_id, face_id), group in groupby(maints_qs, key=group_key):
            maints_group = list(group)
            support      = maints_group[0].support
            face         = maints_group[0].face
            panne_debut  = None
            panne_maint  = None

            for maint in maints_group:
                if maint.etat_apres == ETAT_PANNE and panne_debut is None:
                    panne_debut = maint.date_intervention
                    panne_maint = maint

                elif maint.etat_apres == ETAT_BON and panne_debut is not None:
                    periodes.append({
                        'support'     : support,
                        'face'        : face,
                        'debut'       : panne_debut,
                        'fin'         : maint.date_intervention,
                        'duree'       : maint.date_intervention - panne_debut,
                        'resolue'     : True,
                        'maint_panne' : panne_maint,
                        'maint_bon'   : maint,
                    })
                    panne_debut = None
                    panne_maint = None

            # Panne toujours ouverte
            if panne_debut is not None:
                periodes.append({
                    'support'     : support,
                    'face'        : face,
                    'debut'       : panne_debut,
                    'fin'         : None,
                    'duree'       : timezone.now() - panne_debut,
                    'resolue'     : False,
                    'maint_panne' : panne_maint,
                    'maint_bon'   : None,
                })

        # ── Filtres post-construction ─────────────────────────────
        if statut_f == 'resolue':
            periodes = [p for p in periodes if p['resolue']]
        elif statut_f == 'en_cours':
            periodes = [p for p in periodes if not p['resolue']]

        if date_debut:
            try:
                dt = datetime.strptime(date_debut, '%Y-%m-%d')
                periodes = [p for p in periodes if p['debut'].date() >= dt.date()]
            except ValueError:
                pass

        if date_fin:
            try:
                dt = datetime.strptime(date_fin, '%Y-%m-%d')
                periodes = [p for p in periodes if p['debut'].date() <= dt.date()]
            except ValueError:
                pass

        # ── Tri chronologique décroissant ─────────────────────────
        periodes.sort(key=lambda p: p['debut'], reverse=True)

        # ── KPI globaux ───────────────────────────────────────────
        nb_total    = len(periodes)
        nb_resolues = sum(1 for p in periodes if p['resolue'])
        nb_en_cours = nb_total - nb_resolues
        duree_totale = sum(
            (p['duree'] for p in periodes if p['duree']),
            timedelta()
        )
        duree_moyenne = duree_totale / nb_total if nb_total else timedelta()

        # ── Données pour les filtres ──────────────────────────────
        supports_liste = Support.objects.filter(
            maintenances__isnull=False
        ).distinct().order_by('code')

        context = {
            'title'         : 'Périodes de panne',
            'periodes'      : periodes,
            'nb_total'      : nb_total,
            'nb_resolues'   : nb_resolues,
            'nb_en_cours'   : nb_en_cours,
            'duree_totale'  : duree_totale,
            'duree_moyenne' : duree_moyenne,
            'supports_liste': supports_liste,
            'filters'       : {
                'support'   : support_pk,
                'face'      : face_label,
                'statut'    : statut_f,
                'date_debut': date_debut,
                'date_fin'  : date_fin,
            },
        }
        return render(request, self.template_name, context)