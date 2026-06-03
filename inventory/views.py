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
from campaigns.models import LigneCampagne
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
            from campaigns.models import LigneCampagne
            faces_data = []
            for face in support.faces.all():
                lc = LigneCampagne.objects.filter(
                    face=face,
                    campagne__date_debut__lte=today,
                    campagne__date_fin__gte=today,
                    campagne__statut__in=['en_cours', 'a_venir'],
                ).select_related('campagne__client').first()

                visuel_url = None
                visuels_urls = []
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
                print(f"DEBUG: Face {face.label} - Visuel URL: {visuel_url}")  # --- IGNORE ---
                # visuel_urls
                print(f"DEBUG: Face {face.label} - Visuels URLs: {visuels_urls}") 

                faces_data.append({
                    'label':           face.label,
                    'format':          support.format,
                    'eclairage':       face.get_eclairage_display(),
                    'disponible':      lc is None,
                    'client':          lc.campagne.client.nom           if lc else None,
                    'campagne_nom':    lc.campagne.nom                   if lc else None,
                    'campagne_ref':    lc.campagne.reference             if lc else None,
                    'campagne_statut': lc.campagne.get_statut_display()  if lc else None,
                    'date_debut':      lc.campagne.date_debut.strftime('%d/%m/%Y') if lc else None,
                    'date_fin':        lc.campagne.date_fin.strftime('%d/%m/%Y')   if lc else None,
                    'visuel_url':      visuel_url,
                    'visuels_urls':    visuels_urls,
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

        # 3. Filtre Python (Occupation) - On convertit en liste ICI si nécessaire
        if occupation_f in ['occupe', 'libre']:
            # On transforme en liste pour pouvoir appeler la méthode is_occupe()
            # Note: cela rend la pagination "mémoire" (Django gère les listes pour paginer)
            if occupation_f == 'occupe':
                queryset = [s for s in queryset if s.is_occupe()]
            else:
                queryset = [s for s in queryset if not s.is_occupe()]
        
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
        })
        return context
    


class SupportDetailView(ClientStaffRequiredMixin, DetailView):
    model = Support
    template_name = 'inventory/support_detail.html'
    context_object_name = 'support'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # filtre le support en fonction de user client
        if self.request.user.is_client:
            client = self.request.user.client_profile
            if client is None:
                raise PermissionDenied
            support = get_object_or_404(
                Support.objects.filter(lignes_campagne__campagne__client=client).distinct(),  # ✅ corrigé
                pk=self.kwargs['pk']
            )
        else:
            support = self.get_object()
        today   = timezone.now().date()

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

        # ── Informations générales (communes à tous les supports) ─────────
        info_rows = [
            ('Code',         support.code),
            ('Type',         support.get_type_support_display()),
            ('Ville',        support.ville or '—'),
            ('Quartier',     support.quartier or '—'),
            ('Installation', support.date_installation.strftime('%d/%m/%Y')),
        ]

        # ── Logique spécifique PANNEAU ────────────────────────────────────
        ecran       = None
        ecran_stats = []
        spots_data  = []
        # les 4 dernières maintenances (avec ou sans photo) pour l'historique
        maintenances = support.maintenances.order_by('-date_intervention')[:4]
        if support.type_support == 'panneau':
            # Format du panneau (désormais sur Support)
            if support.format:
                info_rows.append(('Format',    support.get_format_display()))
                info_rows.append(('Dimensions', support.dimensions))
                info_rows.append(('Surface',    support.surface_m2))
                info_rows.append(('Type',       support.type_panneau))

            # Faces
            for face in support.faces.all():
                disponible    = face.is_disponible()
                status_icon   = 'bi-check2'      if disponible else 'bi-broadcast'
                status_class  = 'bs-disponible'  if disponible else 'bs-occupe'
                status_label  = 'Disponible'     if disponible else 'Occupé'

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
                    ('Résolution',       ecran.get_resolution_display()),
                    ('Cellule',           f'{ecran.cellule}'),
                    ('Type écran',       ecran.get_type_ecran_display()),
                    ('Plage diffusion',  plage_str),
                    ('Occupation',       f'{taux}%'),
                ]

                ecran_stats = [
                    ('Résolution', 'bi-aspect-ratio', ecran.get_resolution_display(), 'var(--text)'),
                    ('Cellule',     'bi-tv',           f'{ecran.cellule}',       'var(--text)'),
                    ('Occupation', 'bi-pie-chart',    f'{taux}%',                      'var(--color-primary)'),
                    ('Diffusion',  'bi-clock',        plage_str,                       'var(--color-primary)'),
                ]

                # Données pour la frise JS
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
        qs = Maintenance.objects.select_related('support', 'effectue_par')

        # ✅ Filtre technicien → voit uniquement ses maintenances
        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)

        # ✅ Filtres GET
        support_id = self.request.GET.get('support')
        etat_apres = self.request.GET.get('etat_apres')
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
        qs = Maintenance.objects.select_related('support', 'effectue_par')
        # ✅ Technicien → voit uniquement ses maintenances
        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)
        return qs


class MaintenanceCreateView(LoginRequiredMixin, CreateView):
    model         = Maintenance
    form_class    = MaintenanceForm
    template_name = 'inventory/maintenance_form.html'
    success_url   = reverse_lazy('maintenance_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # ✅ passe le user au form
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        # ✅ Pré-remplit le support si passé en GET (?support=9427)
        support_id = self.kwargs.get('pk')
        if support_id:
            initial['support'] = support_id
        return initial

    def form_valid(self, form):
        # ✅ Si technicien → force effectue_par
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