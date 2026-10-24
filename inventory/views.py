import json
import re
import unicodedata
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
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
from core.mixins import SortableListMixin
from .forms import *
from .models import *
from accounts.models import User
from django.db.models import Q
from django.utils.safestring import mark_safe
from django.core.exceptions import PermissionDenied
from django.db.models.deletion import ProtectedError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import UpdateView
from django.views.generic.edit import UpdateView, DeleteView
from django.db import transaction
from typing import cast



from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin # Optionnel: pour sécuriser l'accès
from .models import FormatSupport, format_sort_key, formats_croissants

# 1. LISTER LES FORMATS
class FormatSupportListView(LoginRequiredMixin, SortableListMixin, ListView):
    model = FormatSupport
    template_name = 'inventory/format_list.html'
    context_object_name = 'formats'
    paginate_by = 20
    SORT_FIELDS = {
        'code': 'code',
        'categorie': 'categorie',
        'dimensions': 'dimensions',
        'superficie': 'superficie',
    }
    DEFAULT_SORT = 'categorie'
    DEFAULT_DIR = 'asc'

    def get_queryset(self):
        return sorted(
            super().get_queryset(),
            key=lambda format_support: (
                format_support.categorie.casefold(),
                *format_sort_key(format_support),
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['codes_formats_panneaux'] = set(
            Support.objects.filter(
                type_support=Support.TYPE_PANNEAU,
            ).values_list('format', flat=True)
        )
        return context


def _extraire_dimensions(texte):
    """
    Extrait (largeur, hauteur) en mètres depuis un texte du type
    '4m × 3m', '10m × 4.14m', '1,20m × 1,80m' ou '12 x 4'.
    Retourne None si le texte n'est pas exploitable.
    """
    import re
    if not texte:
        return None
    m = re.search(
        r'(\d+(?:[.,]\d+)?)\s*m?\s*[x×*]\s*(\d+(?:[.,]\d+)?)',
        texte, re.IGNORECASE,
    )
    if not m:
        return None
    try:
        largeur = float(m.group(1).replace(',', '.'))
        hauteur = float(m.group(2).replace(',', '.'))
    except ValueError:
        return None
    if largeur <= 0 or hauteur <= 0:
        return None
    return largeur, hauteur


class FormatSupportDetailView(LoginRequiredMixin, DetailView):
    model = FormatSupport
    template_name = 'inventory/format_detail.html'
    context_object_name = 'format'
    paginate_by = 20

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        fmt = self.object
        est_ecran = fmt.categorie == 'Ecran'

        # Les écrans portent leur format dans ecran_info.cellule,
        # les panneaux dans Support.format.
        if est_ecran:
            supports = Support.objects.filter(
                type_support=Support.TYPE_ECRAN,
                ecran_info__cellule=fmt.code,
            )
        else:
            supports = Support.objects.filter(
                type_support=Support.TYPE_PANNEAU,
                format=fmt.code,
            )
        supports = supports.order_by('ville', 'quartier', 'code')

        support_count = supports.count()

        # ── Répartition par état ──────────────────────────────────────
        etats = {row['etat']: row['n'] for row in supports.values('etat').annotate(n=Count('pk'))}
        nb_bon = etats.get(Support.ETAT_BON, 0)
        nb_maintenance = etats.get(Support.ETAT_MAINTENANCE, 0)
        nb_maintenance_preventive = etats.get(Support.ETAT_A_MAINTENIR, 0)
        nb_panne = etats.get(Support.ETAT_PANNE, 0)

        # ── Répartition par ville ─────────────────────────────────────
        villes = [
            {'ville': row['ville'] or 'Non renseignée', 'n': row['n']}
            for row in supports.order_by().values('ville').annotate(n=Count('pk')).order_by('-n', 'ville')
        ]

        # ── Aperçu proportionnel du gabarit (boîte max 220 × 120 px) ──
        dims = _extraire_dimensions(fmt.dimensions)
        preview_w, preview_h = 140, 100
        ratio = None
        if dims:
            largeur, hauteur = dims
            ratio = round(largeur / hauteur, 2)
            max_w, max_h = 220, 120
            echelle = min(max_w / largeur, max_h / hauteur)
            preview_w = max(int(largeur * echelle), 40)
            preview_h = max(int(hauteur * echelle), 24)

        surface_totale = None
        if not est_ecran and fmt.superficie and support_count:
            surface_totale = round(fmt.superficie * support_count, 2)

        paginator = Paginator(supports, self.paginate_by)
        page_obj = paginator.get_page(self.request.GET.get('page'))

        context.update({
            'supports': page_obj,
            'page_obj': page_obj,
            'paginator': paginator,
            'is_paginated': page_obj.has_other_pages(),
            'support_count': support_count,
            'format_utilise_panneau': Support.objects.filter(
                type_support=Support.TYPE_PANNEAU,
                format=fmt.code,
            ).exists(),
            'est_ecran': est_ecran,
            'unite_surface': 'cellules' if est_ecran else 'm²',
            'nb_bon': nb_bon,
            'nb_maintenance': nb_maintenance,
            'nb_maintenance_preventive': nb_maintenance_preventive,
            'nb_panne': nb_panne,
            'villes': villes,
            'nb_villes': len(villes),
            'surface_totale': surface_totale,
            'ratio': ratio,
            'preview_w': preview_w,
            'preview_h': preview_h,
        })
        return context


# 2. AJOUTER UN FORMAT
class FormatSupportCreateView(LoginRequiredMixin, CreateView):
    model = FormatSupport
    fields = ['code', 'dimensions', 'superficie', 'categorie']
    template_name = 'inventory/format_form.html'
    success_url = reverse_lazy('format_list') # Redirection après succès

# 3. MODIFIER UN FORMAT (Bonus utile)
class FormatSupportUpdateView(LoginRequiredMixin, UpdateView):
    model = FormatSupport
    fields = ['code', 'dimensions', 'superficie', 'categorie']
    template_name = 'inventory/format_form.html' # Réutilise le même formulaire qu'à l'ajout
    success_url = reverse_lazy('format_list')

# 4. SUPPRIMER UN FORMAT
class FormatSupportDeleteView(LoginRequiredMixin, DeleteView):
    model = FormatSupport
    template_name = 'partials/confirm_delete.html'
    success_url = reverse_lazy('format_list')

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self._has_linked_panels():
            messages.error(
                request,
                f'Impossible de supprimer le format « {self.object.code} » : '
                'au moins un panneau l’utilise.',
            )
            return redirect('format_support_detail', pk=self.object.pk)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self._has_linked_panels():
            messages.error(
                request,
                f'Impossible de supprimer le format « {self.object.code} » : '
                'au moins un panneau l’utilise.',
            )
            return redirect('format_support_detail', pk=self.object.pk)
        return super().post(request, *args, **kwargs)

    def _has_linked_panels(self):
        return Support.objects.filter(
            type_support=Support.TYPE_PANNEAU,
            format=self.object.code,
        ).exists()



# ── MARCHÉS ────────────────────────────────────────────────────────────────

class MarcheListView(LoginRequiredMixin, ListView):
    model = Marche
    template_name = 'inventory/marche_list.html'
    context_object_name = 'marches'

    def get_queryset(self):
        qs = Marche.objects.all()
        q = self.request.GET.get('q', '')
        if q:
            qs = qs.filter(Q(nom__icontains=q) | Q(ville__icontains=q) | Q(quartier__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['q'] = self.request.GET.get('q', '')
        return context


class MarcheDetailView(LoginRequiredMixin, DetailView):
    model = Marche
    template_name = 'inventory/marche_detail.html'
    context_object_name = 'marche'
    paginate_by = 20

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        emplacements = self.object.emplacements.select_related('support_installe').order_by('code')
        context['emplacements'] = emplacements
        paginator = Paginator(emplacements, self.paginate_by)
        page_obj = paginator.get_page(self.request.GET.get('page'))
        context['emplacements_page'] = page_obj
        context['page_obj'] = page_obj
        context['paginator'] = paginator
        context['is_paginated'] = page_obj.has_other_pages()
        context['title'] = self.object.nom
        return context


class MarcheCreateView(StaffRequiredMixin, CreateView):
    model = Marche
    fields = ['nom', 'ville', 'quartier', 'adresse', 'latitude', 'longitude', 'rayon_metres', 'description', 'actif']
    template_name = 'inventory/marche_form.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        log_action(self.request, AuditLog.ACTION_CREATE, 'inventory', obj=self.object, detail=f"Création marché: {self.object.nom}")
        messages.success(self.request, f'Marché "{self.object.nom}" créé. Vous pouvez maintenant y ajouter des emplacements.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Nouveau marché'
        return context

    def get_success_url(self):
        return reverse('marche_detail', kwargs={'pk': self.object.pk})


class MarcheUpdateView(StaffRequiredMixin, UpdateView):
    model = Marche
    fields = ['nom', 'ville', 'quartier', 'adresse', 'latitude', 'longitude', 'rayon_metres', 'description', 'actif']
    template_name = 'inventory/support_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Modifier — {self.object.nom}'
        context['initial_type_choice'] = 'marche'
        context['ecran_form'] = EcranNumeriqueForm(prefix='ecran')
        context['face_form'] = FacePanneauForm()
        context['marches'] = Marche.objects.filter(actif=True).order_by('nom')
        context['marche_form'] = MarcheForm(instance=self.object, prefix='marche')
        context['form'] = SupportForm(initial={'type_support': 'marche'})
        context['form'].fields['type_support'].initial = 'marche'
        context['form'].initial['type_support'] = 'marche'
        return context

    def get_success_url(self):
        return reverse('marche_detail', kwargs={'pk': self.object.pk})


class MarcheDeleteView(StaffRequiredMixin, DeleteView):
    model = Marche
    template_name = 'partials/confirm_delete.html'
    context_object_name = 'obj'
    success_url = reverse_lazy('marche_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        marche = self.object
        context.update({
            'title': 'Supprimer le marché',
            'header': 'Suppression de marché',
            'message_title': 'Supprimer ce marché ?',
            'message_body': f"Vous êtes sur le point de supprimer le marché {marche.nom}",
            'hint': "Cette opération supprimera aussi tous ses emplacements. Les panneaux qui y sont installés seront détachés (mais pas supprimés).",
            'confirm_label': 'Supprimer le marché',
            'cancel_url': reverse('marche_detail', kwargs={'pk': marche.pk}),
        })
        return context


# ── EMPLACEMENTS ──────────────────────────────────────────────────────────

class EmplacementDetailView(LoginRequiredMixin, DetailView):
    model = Emplacement
    template_name = 'inventory/emplacement_detail.html'
    context_object_name = 'emplacement'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['marche'] = self.object.marche
        context['title'] = f'{self.object.marche.nom} — {self.object.code}'
        # Campagnes rattachées (relation inverse depuis campaigns.LigneCampagne,
        # pas d'import direct nécessaire dans ce fichier).
        context['campagnes_liees'] = (
            self.object.lignes_campagne.select_related('campagne', 'campagne__client').order_by('-campagne__date_debut')
        )
        return context


class EmplacementCreateView(StaffRequiredMixin, FormView):
    form_class = EmplacementCreateForm
    template_name = 'inventory/emplacement_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.marche = get_object_or_404(Marche, pk=kwargs['marche_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                # Sérialise les créations concurrentes sur le même marché
                # lorsque le moteur de base de données prend en charge ce verrou.
                Marche.objects.select_for_update().get(pk=self.marche.pk)
                nombre = form.cleaned_data['nombre']
                existing_codes = list(
                    self.marche.emplacements.values_list('code', flat=True)
                )
                codes = [
                    generer_prochain_code_emplacement(
                        self.marche, offset=i, existing_codes=existing_codes
                    )
                    for i in range(nombre)
                ]
                deja_utilises = list(
                    self.marche.emplacements.filter(code__in=codes)
                    .values_list('code', flat=True)
                )
                if deja_utilises:
                    form.add_error(
                        None,
                        "Création impossible : les codes suivants existent déjà sur ce "
                        f"marché : {', '.join(deja_utilises)}. Réessayez.",
                    )
                    return self.form_invalid(form)

                Emplacement.objects.bulk_create([
                    Emplacement(
                        marche=self.marche,
                        code=code,
                        notes=form.cleaned_data.get('notes', ''),
                    )
                    for code in codes
                ])
        except IntegrityError:
            form.add_error(
                None,
                "Création impossible : un code généré vient d'être utilisé sur ce "
                "marché. Aucun emplacement n'a été ajouté ; veuillez réessayer.",
            )
            return self.form_invalid(form)

        messages.success(
            self.request,
            f'{len(codes)} emplacement(s) ajouté(s) au marché {self.marche.nom} '
            f'({codes[0]} à {codes[-1]}).',
        )
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['marche'] = self.marche
        context['title'] = f'Nouvel emplacement — {self.marche.nom}'
        existing_codes = list(
            self.marche.emplacements.values_list('code', flat=True)
        )
        context['code_prefix'] = generer_prefixe_code_emplacement(self.marche)
        context['code_start'] = prochain_numero_emplacement(
            context['code_prefix'],
            existing_codes,
        )
        return context

    def get_success_url(self):
        return reverse('marche_detail', kwargs={'pk': self.marche.pk})


class EmplacementUpdateView(StaffRequiredMixin, UpdateView):
    model = Emplacement
    fields = ['code', 'notes']
    template_name = 'inventory/emplacement_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['marche'] = self.object.marche
        context['title'] = f'Modifier — {self.object.marche.nom} / {self.object.code}'
        # Campagnes actuellement rattachées à cet emplacement (relation inverse
        # depuis campaigns.LigneCampagne, aucun import direct nécessaire ici).
        context['campagnes_liees'] = (
            self.object.lignes_campagne.select_related('campagne', 'campagne__client').all()
        )
        return context

    def get_success_url(self):
        return reverse('marche_detail', kwargs={'pk': self.object.marche.pk})


class EmplacementDeleteView(StaffRequiredMixin, DeleteView):
    model = Emplacement
    template_name = 'partials/confirm_delete.html'
    context_object_name = 'obj'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        emplacement = self.object
        context.update({
            'title': 'Supprimer l\'emplacement',
            'header': 'Suppression d\'emplacement',
            'message_title': 'Supprimer cet emplacement ?',
            'message_body': f"Vous êtes sur le point de supprimer l'emplacement {emplacement.code} ({emplacement.marche.nom})",
            'hint': "Si un panneau y est actuellement installé, il sera détaché (mais pas supprimé).",
            'confirm_label': 'Supprimer l\'emplacement',
            'cancel_url': reverse('marche_detail', kwargs={'pk': emplacement.marche.pk}),
        })
        return context

    def get_success_url(self):
        return reverse('marche_detail', kwargs={'pk': self.object.marche.pk})


class EmplacementBulkActionView(StaffRequiredMixin, View):
    """Applique une action aux emplacements sélectionnés d'un seul marché."""

    def post(self, request, marche_pk, *args, **kwargs):
        marche = get_object_or_404(Marche, pk=marche_pk)
        raw_ids = request.POST.getlist('selected_ids')
        try:
            selected_ids = {int(value) for value in raw_ids}
        except (TypeError, ValueError):
            messages.error(request, "La sélection d'emplacements est invalide.")
            return redirect('marche_detail', pk=marche.pk)

        if not selected_ids:
            messages.warning(request, "Cochez au moins un emplacement.")
            return redirect('marche_detail', pk=marche.pk)

        emplacements = list(
            Emplacement.objects.filter(
                marche=marche,
                pk__in=selected_ids,
            ).select_related('support_installe').order_by('code')
        )
        if len(emplacements) != len(selected_ids):
            messages.error(
                request,
                "La sélection contient un emplacement invalide ou appartenant à un autre marché.",
            )
            return redirect('marche_detail', pk=marche.pk)

        action = request.POST.get('action')
        if action == 'edit_notes':
            notes = request.POST.get('notes')
            if notes is None:
                messages.error(request, "Saisissez les notes à appliquer.")
            else:
                Emplacement.objects.filter(
                    marche=marche,
                    pk__in=selected_ids,
                ).update(notes=notes.strip())
                messages.success(
                    request,
                    f"Notes mises à jour pour {len(emplacements)} emplacement(s).",
                )
        elif action == 'delete':
            if not request.user.is_admin:
                raise PermissionDenied
            eligible = []
            blocked = []
            linked_campaign_ids = set(
                LigneCampagne.objects.filter(
                    emplacement_id__in=selected_ids,
                ).values_list('emplacement_id', flat=True)
            )
            for emplacement in emplacements:
                if getattr(emplacement, 'support_installe', None):
                    blocked.append(f"{emplacement.code} (panneau installé)")
                elif emplacement.pk in linked_campaign_ids:
                    blocked.append(f"{emplacement.code} (lié à une campagne)")
                else:
                    eligible.append(emplacement)

            if eligible:
                from django.db.models.deletion import ProtectedError
                try:
                    with transaction.atomic():
                        Emplacement.objects.filter(
                            marche=marche,
                            pk__in=[emplacement.pk for emplacement in eligible],
                        ).delete()
                except ProtectedError:
                    messages.error(
                        request,
                        "Suppression annulée : au moins un emplacement est encore "
                        "utilisé par une campagne ou un support.",
                    )
                else:
                    messages.success(
                        request,
                        f"{len(eligible)} emplacement(s) supprimé(s).",
                    )
            if blocked:
                messages.warning(
                    request,
                    "Non supprimé(s) pour éviter de détacher un panneau ou de casser "
                    "l'historique d'une campagne : " + ", ".join(blocked),
                )
            if not eligible and not blocked:
                messages.warning(request, "Aucun emplacement n'a pu être supprimé.")
        elif action == 'download':
            if len(emplacements) > 50:
                messages.error(
                    request,
                    "Téléchargez au maximum 50 rapports à la fois.",
                )
                return redirect('marche_detail', pk=marche.pk)

            import io
            import zipfile
            from django.http import HttpResponse
            from django.template.loader import render_to_string
            from django.utils.text import slugify
            from reports.views import HTML, _build_context_emplacement_detail

            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(
                archive_buffer,
                mode='w',
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for emplacement in emplacements:
                    context = _build_context_emplacement_detail(
                        emplacement,
                        request=request,
                    )
                    html = render_to_string(
                        'reports/emplacement_detail_pdf.html',
                        context,
                        request=request,
                    )
                    pdf = HTML(
                        string=html,
                        base_url=request.build_absolute_uri(),
                    ).write_pdf()
                    archive.writestr(
                        f"rapport_emplacement_{slugify(emplacement.code) or 'emplacement'}_{emplacement.pk}.pdf",
                        pdf,
                    )

            response = HttpResponse(
                archive_buffer.getvalue(),
                content_type='application/zip',
            )
            response['Content-Disposition'] = (
                f'attachment; filename="rapports_{slugify(marche.nom) or marche.pk}.zip"'
            )
            return response
        else:
            messages.error(request, "Choisissez une action globale valide.")

        return redirect('marche_detail', pk=marche.pk)


class ApiMarchesGeojsonView(LoginRequiredMixin, View):
    """Retourne les marchés en GeoJSON (position de référence), pour l'affichage sur la carte globale."""
    def get(self, request, *args, **kwargs):
        features = []
        for m in Marche.objects.filter(actif=True):
            if m.latitude is None or m.longitude is None:
                continue
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [float(m.longitude), float(m.latitude)]},
                'properties': {
                    'id': m.pk,
                    'nom': m.nom,
                    'nb_emplacements': m.nb_emplacements,
                    'nb_libres': m.nb_emplacements_libres,
                    'rayon_metres': m.rayon_metres,
                },
            })
        return JsonResponse({'type': 'FeatureCollection', 'features': features})


api_marches_geojson = ApiMarchesGeojsonView.as_view()


class ApiMarchePopupView(LoginRequiredMixin, View):
    """Données complètes d'un marché pour le side panel de la carte."""
    def get(self, request, pk, *args, **kwargs):
        marche = get_object_or_404(Marche, pk=pk)
        emplacements = marche.emplacements.select_related('support_installe').order_by('code')

        data = {
            'id': marche.pk,
            'nom': marche.nom,
            'ville': marche.ville,
            'quartier': marche.quartier,
            'adresse': marche.adresse,
            'description': marche.description,
            'nb_emplacements': marche.nb_emplacements,
            'nb_libres': marche.nb_emplacements_libres,
            'nb_occupes': marche.nb_emplacements_occupes,
            'taux_occupation': marche.taux_occupation_pourcentage,
            'rayon_metres': marche.rayon_metres,
            'url_detail': reverse('marche_detail', kwargs={'pk': marche.pk}),
            'emplacements': [
                {
                    'code': e.code,
                    'libre': e.is_libre(),
                    'support_code': getattr(e, 'support_installe', None).code if not e.is_libre() else None,
                }
                for e in emplacements
            ],
        }
        return JsonResponse(data)


api_marche_popup = ApiMarchePopupView.as_view()


class ApiEmplacementsMarcheView(LoginRequiredMixin, View):
    """Retourne les emplacements d'un marché (JSON), pour le select en cascade du formulaire support.
    Les emplacements n'ont pas de coordonnées propres : on renvoie la position du MARCHÉ lui-même,
    utilisée pour positionner le support sur la carte GPS du formulaire."""
    def get(self, request, pk, *args, **kwargs):
        marche = get_object_or_404(Marche, pk=pk)
        current_support_id = request.GET.get('current_support', '')

        emplacements = []
        for e in marche.emplacements.select_related('support_installe').order_by('code'):
            support_installe = getattr(e, 'support_installe', None)
            occupe_par_autre = (
                support_installe is not None
                and str(support_installe.pk) != str(current_support_id)
            )
            emplacements.append({
                'id': e.pk,
                'code': e.code,
                'notes': e.notes,
                'libre': not occupe_par_autre,
                'support_code': support_installe.code if (support_installe and occupe_par_autre) else None,
            })

        return JsonResponse({
            'marche_latitude': str(marche.latitude) if marche.latitude is not None else None,
            'marche_longitude': str(marche.longitude) if marche.longitude is not None else None,
            'rayon_metres': marche.rayon_metres,
            'emplacements': emplacements,
        })


api_emplacements_marche = ApiEmplacementsMarcheView.as_view()


# ── API GeoJSON pour Leaflet ──────────────────────────────────────────────────

class ApiGeojsonView(View):
    def get(self, request, *args, **kwargs):
        """Retourne tous les supports en GeoJSON avec couleur dynamique."""
        type_filtre = request.GET.get('type', '')
        etat_filtre = request.GET.get('etat', '')
        type_panneau_filtre = request.GET.get('type_panneau', '')
        ville_filtre = request.GET.get('ville', '').strip()
        
        qs = Support.objects.prefetch_related('faces').exclude(
            latitude__isnull=True,
        ).exclude(
            longitude__isnull=True,
        )

        if ville_filtre:
            qs = qs.filter(ville__iexact=ville_filtre)
        
        if type_filtre:
            qs = qs.filter(type_support=type_filtre)  # ✅ corrigé : 'type' au lieu de 'type_support'
            # Les sucettes 1x2 disposent d'un filtre dédié et ne doivent pas
            # apparaître dans la vue générale des panneaux.
            if type_filtre == Support.TYPE_PANNEAU and type_panneau_filtre != '1x2':
                qs = qs.exclude(format='1x2')
        if etat_filtre:
            if etat_filtre == Support.ETAT_BON:
                qs = qs.filter(etat__in=Support.ETATS_OPERATIONNELS)
            else:
                qs = qs.filter(etat=etat_filtre)
        if type_panneau_filtre:
            codes_valides = list(
                FormatSupport.objects.filter(categorie=type_panneau_filtre)
                .values_list('code', flat=True)
            )
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
            'marche':            support.marche.nom if support.is_dans_marche else None,
            'emplacement_code':  support.emplacement.code if support.is_dans_marche else None,
            'url_detail':        f'/gestion/inventory/{support.uuid}/',
            'url_edit':          f'/gestion/inventory/{support.uuid}/modifier/',
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
            from campaigns.models import LigneCampagne, ReservationLigne
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
                    from campaigns.models import ReservationLigne
                    rl = ReservationLigne.objects.filter(
                        face=face,
                        reservation__date_fin__gte=timezone.now(),
                        reservation__statut__in=['en_attente', 'confirmee'],
                    ).select_related('reservation__client').first()
                    reservation = rl.reservation if rl else None

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



# views.py
from django.http import JsonResponse
from .models import Support


def generer_prefixe_code_emplacement(marche):
    """Construit un préfixe lisible à partir du nom du marché."""
    nom_normalise = unicodedata.normalize('NFKD', marche.nom)
    nom_ascii = ''.join(
        caractere for caractere in nom_normalise
        if not unicodedata.combining(caractere)
    )
    mots = re.findall(r'[A-Za-z0-9]+', nom_ascii.upper())
    mots_ignores = {'DE', 'DU', 'DES', 'LA', 'LE', 'LES', 'D', 'L'}
    prefixe = ''.join(
        mot if any(caractere.isdigit() for caractere in mot) else mot[0]
        for mot in mots
        if mot not in mots_ignores
    )
    return prefixe[:10] or 'M'


def prochain_numero_emplacement(prefixe, existing_codes):
    pattern = re.compile(rf'^{re.escape(prefixe)}-(\d+)$', re.IGNORECASE)
    numeros = [
        int(match.group(1))
        for code in existing_codes
        if (match := pattern.match(code or ''))
    ]
    return max(numeros, default=0) + 1

def generer_prochain_code_emplacement(marche, offset=0, existing_codes=None):
    """
    Génère un code préfixé par les initiales du marché, par exemple 'GM-01'.
    """
    existing = (
        list(existing_codes)
        if existing_codes is not None
        else list(marche.emplacements.values_list('code', flat=True))
    )
    prefixe = generer_prefixe_code_emplacement(marche)
    numero = prochain_numero_emplacement(prefixe, existing) + offset
    return f"{prefixe}-{numero:02d}"


def emplacement_next_code(request, marche_pk):
    marche = get_object_or_404(Marche, pk=marche_pk)
    return JsonResponse({'code': generer_prochain_code_emplacement(marche)})


def support_next_code(request):
    prefix = request.GET.get('prefix', 'PAN').upper()[:3]
    
    # Récupérer tous les codes qui commencent par ce préfixe
    existing = Support.objects.filter(
        code__startswith=f"{prefix}-"
    ).values_list('code', flat=True)

    # Extraire les numéros existants (ex: "OUA-007" → 7)
    nums = []
    pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$')
    for code in existing:
        m = pattern.match(code)
        if m:
            nums.append(int(m.group(1)))

    next_num = (max(nums) + 1) if nums else 1
    code = f"{prefix}-{str(next_num).zfill(3)}"  # ex: OUA-001, BOB-042

    return JsonResponse({'code': code})




class CarteView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/carte.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['stats'] = {
            'total': Support.objects.count(),
            'bon': Support.objects.filter(
                etat=Support.ETAT_BON,
            ).count(),
            'maintenance': Support.objects.filter(
                etat__in=(Support.ETAT_MAINTENANCE, Support.ETAT_A_MAINTENIR),
            ).count(),
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

class SupportListView(TechnicienStaffRequiredMixin,SortableListMixin, ListView):
    model = Support
    template_name = 'inventory/support_list.html'
    context_object_name = 'supports'
    SORT_FIELDS = {
        'code':  'code',
        'code_mairie': 'code_mairie',
        'nom':   'nom',
        'type_support': 'type_support',
        'etat':  'etat',
        'occupation':'occupation',
    }
    DEFAULT_SORT = 'code'
    DEFAULT_DIR = 'asc'

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
        category_f = self.request.GET.get('categorie', '')
        etat_f = self.request.GET.get('etat', '')
        ville_f = self.request.GET.get('ville', '')
        occupation_f = self.request.GET.get('occupation', '')
        type_panneau_f = self.request.GET.get('type_panneau', '')
        desactif = self.request.GET.get('desactif', '')

        # Filtre par format (code) — la valeur envoyée par le <select> est bien le code
        if type_panneau_f:
            fmt = FormatSupport.objects.filter(code=type_panneau_f).first()
            if (
                category_f.casefold() == 'ecran'
                or (fmt and fmt.categorie == 'Ecran')
            ):
                queryset = queryset.filter(ecran_info__cellule=type_panneau_f)
            else:
                queryset = queryset.filter(format=type_panneau_f)

        if q:
            queryset = queryset.filter(
                Q(code__icontains=q) | Q(nom__icontains=q)
                | Q(ancien_nom__icontains=q) | Q(adresse__icontains=q)
            )

        if type_f:
            queryset = queryset.filter(type_support=type_f)
            if type_f == Support.TYPE_PANNEAU and type_panneau_f != '1x2':
                queryset = queryset.exclude(format='1x2')

        if category_f:
            if category_f == 'Autre':
                formats_categorie = FormatSupport.hors_ecran().filter(
                    Q(categorie__isnull=True) | Q(categorie=''),
                ).values('code')
            elif category_f.casefold() == '12m2':
                formats_categorie = FormatSupport.objects.filter(
                    Q(categorie__iexact='Standard')
                    | Q(categorie__iexact='12m2')
                    | Q(categorie__iexact='12m²')
                ).values('code')
            else:
                formats_categorie = FormatSupport.objects.filter(
                    categorie__iexact=category_f,
                ).values('code')

            if category_f.casefold() == 'ecran':
                queryset = queryset.filter(type_support=Support.TYPE_ECRAN)
            else:
                queryset = queryset.filter(
                    type_support=Support.TYPE_PANNEAU,
                    format__in=formats_categorie,
                )

        if etat_f:
            if etat_f == Support.ETAT_BON:
                queryset = queryset.filter(etat__in=Support.ETATS_OPERATIONNELS)
            else:
                queryset = queryset.filter(etat=etat_f)

        if ville_f:
            queryset = queryset.filter(ville__iexact=ville_f)

        if occupation_f in ['occupe', 'total_occupe', 'libre', 'reserve', 'total_reserve', 'non_reserve', 'occupe_ou_reserve']:
            if occupation_f == 'occupe':
                queryset = [s for s in queryset if s.is_occupe()]
            elif occupation_f == 'total_occupe':
                queryset = [s for s in queryset if s.is_occupe() and not s.is_libre()]
            elif occupation_f == 'reserve':
                queryset = [s for s in queryset if s.is_reserve()]
            elif occupation_f == 'total_reserve':
                queryset = [s for s in queryset if s.is_reserve() and not s.is_libre() and not s.is_occupe()]
            elif occupation_f == 'non_reserve':
                queryset = [s for s in queryset if not s.is_reserve()]
            elif occupation_f == 'occupe_ou_reserve':
                queryset = [s for s in queryset if s.is_occupe() or s.is_reserve()]
            else:  # libre
                queryset = [s for s in queryset if not s.is_occupe() and not s.is_reserve()]

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        type_f = self.request.GET.get('type', '')
        ville_f = self.request.GET.get('ville', '').strip()
        category_f = self.request.GET.get('categorie', '')
        support_scope = Support.objects.all()
        if ville_f:
            support_scope = support_scope.filter(ville__iexact=ville_f)
        if type_f:
            support_scope = support_scope.filter(type_support=type_f)

        panel_format_codes = set(
            support_scope.filter(type_support=Support.TYPE_PANNEAU)
            .exclude(format='')
            .values_list('format', flat=True)
        )
        screen_format_codes = set(
            support_scope.filter(type_support=Support.TYPE_ECRAN)
            .exclude(ecran_info__cellule__isnull=True)
            .exclude(ecran_info__cellule='')
            .values_list('ecran_info__cellule', flat=True)
        )
        available_format_codes = panel_format_codes | screen_format_codes
        formats_by_code = {
            format_support.code: format_support
            for format_support in FormatSupport.objects.filter(code__in=available_format_codes)
        }

        categories = {
            format_support.categorie
            for format_support in formats_by_code.values()
            if format_support.categorie
        }
        if support_scope.filter(type_support=Support.TYPE_ECRAN).exists():
            categories.add('Ecran')
        if any(
            not formats_by_code[code].categorie
            for code in panel_format_codes
            if code in formats_by_code
        ):
            categories.add('Autre')
        category_choices = sorted(categories, key=str.casefold)

        category_codes = available_format_codes
        if category_f:
            if category_f.casefold() == 'ecran':
                category_codes = screen_format_codes
            elif category_f == 'Autre':
                category_codes = {
                    code for code in panel_format_codes
                    if code in formats_by_code and not formats_by_code[code].categorie
                }
            else:
                category_codes = {
                    code for code in available_format_codes
                    if code in formats_by_code
                    and (formats_by_code[code].categorie or '').casefold() == category_f.casefold()
                }

        type_panneau_choices = []
        for code in sorted(
            category_codes,
            key=lambda value: format_sort_key(formats_by_code.get(value) or value),
        ):
            format_support = formats_by_code.get(code)
            if not format_support:
                if code in screen_format_codes:
                    type_panneau_choices.append((code, code))
                continue
            is_screen_format = code in screen_format_codes and code not in panel_format_codes
            unit = 'Cellules' if is_screen_format else 'm²'
            label = (
                f"{format_support.dimensions} ({format_support.superficie:g} {unit})"
                if format_support.superficie
                else format_support.dimensions
            )
            type_panneau_choices.append((code, label))

        villes = sorted({
            v.strip() for v in Support.objects.values_list('ville', flat=True)
            if v and v.strip()
        })

        context.update({
            'q': self.request.GET.get('q', ''),
            'type_f': type_f,
            'ville_f': ville_f,
            'category_f': category_f,
            'category_choices': category_choices,
            'etat_f': self.request.GET.get('etat', ''),
            'occupation_f': self.request.GET.get('occupation', ''),
            'type_panneau_f': self.request.GET.get('type_panneau', ''),
            'type_panneau_choices': type_panneau_choices,
            'occupation_choices': self.occupation,
            'villes': villes,
            'desactif': self.request.GET.get('desactif', '')
        })
        pending_requests = SupportDeletionRequest.objects.filter(
            status=SupportDeletionRequest.STATUS_PENDING,
            support__isnull=False,
        )
        context['pending_deletion_support_ids'] = set(
            pending_requests.values_list('support_id', flat=True)
        )
        context['pending_deletion_count'] = pending_requests.count()
        return context

class SupportDetailView(ClientStaffRequiredMixin, DetailView):
    model = Support
    template_name = 'inventory/support_detail.html'
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

        if support.is_dans_marche:
            marche_link = mark_safe(
                f'<a href="{reverse("marche_detail", kwargs={"pk": support.marche.pk})}" '
                f'style="color:var(--color-primary);font-weight:700;text-decoration:none">{support.marche.nom}</a>'
            )
            info_rows.append(('Marché', marche_link))
            info_rows.append(('Emplacement', support.emplacement.code))

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
                    f'<span class="badge {status_class}" style="border-radius:4px !important;">'
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
                    ('Cellule', f'{ecran.format_detail["dimensions"]} ({ecran.format_detail["superficie"]}m²)'),
                    ('Type écran',      ecran.get_type_ecran_display()),
                    ('Plage diffusion', plage_str),
                    ('Occupation',      f'{taux}%'),
                ]

                ecran_stats = [
                    ('Résolution', 'bi-aspect-ratio', ecran.get_resolution_display(), 'var(--text)'),
                    ('Cellule',    'bi-tv',           f'{ecran.format_detail["dimensions"]} ({ecran.format_detail["superficie"]}m²)',              'var(--text)'),
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
            'pending_deletion_request': SupportDeletionRequest.objects.filter(
                support=support,
                status=SupportDeletionRequest.STATUS_PENDING,
            ).select_related('requested_by').first(),
        })
        return context
    
from django.views.generic import CreateView
from django.db import transaction

class SupportCreateView(StaffRequiredMixin, CreateView):
    model = Support
    form_class = SupportForm
    template_name = 'inventory/support_form.html'

    def get_type_choice_from_request(self):
        value = (
            self.request.GET.get('type')
            or self.request.GET.get('type_support')
            or self.request.GET.get('type_choice')
        )
        if value in (Support.TYPE_PANNEAU, Support.TYPE_ECRAN, 'marche'):
            return value
        return None

    def get_initial(self):
        # Récupère les coordonnées depuis l'URL (clic sur la carte)
        initial = super().get_initial()
        initial['latitude'] = self.request.GET.get('lat', '')
        initial['longitude'] = self.request.GET.get('lng', '')
        type_choice = self.get_type_choice_from_request()
        if type_choice:
            initial['type_support'] = type_choice
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['selected_marche_id'] = (
            self.request.POST.get('marche', '') if self.request.method == 'POST' else ''
        )
        if self.request.POST:
            context['ecran_form'] = EcranNumeriqueForm(self.request.POST, prefix='ecran')
            if 'marche_form' not in context:
                context['marche_form'] = MarcheForm(self.request.POST, prefix='marche')
        else:
            context['ecran_form'] = EcranNumeriqueForm(prefix='ecran')
            context['marche_form'] = MarcheForm(prefix='marche')
            type_choice = self.get_type_choice_from_request()
            if type_choice:
                context['initial_type_choice'] = type_choice

        context['face_form'] = FacePanneauForm() # Pour le template dynamique
        context['title'] = "Nouveau Support"
        context['marches'] = Marche.objects.filter(actif=True).order_by('nom')
        return context

    def post(self, request, *args, **kwargs):
        type_choice = request.POST.get('type_choice', 'panneau')
        if type_choice == 'marche':
            marche_form = MarcheForm(request.POST, prefix='marche')
            if marche_form.is_valid():
                marche = marche_form.save(commit=False)
                marche.created_by = request.user
                marche.save()
                log_action(request, AuditLog.ACTION_CREATE, 'inventory', obj=marche, detail=f"Création marché: {marche.nom}")
                messages.success(request, f'Marché "{marche.nom}" créé avec succès.')
                return redirect('marche_detail', pk=marche.pk)
            else:
                self.object = None
                return self.render_to_response(self.get_context_data(form=self.get_form(), marche_form=marche_form, initial_type_choice='marche'))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        context = self.get_context_data()
        ecran_form = context['ecran_form']
        market_id = self.request.POST.get('marche', '').strip()
        emplacement_id = self.request.POST.get('emplacement', '').strip()
        emplacement = None

        with transaction.atomic():
            if form.cleaned_data.get('type_support') == Support.TYPE_PANNEAU and market_id:
                marche = Marche.objects.filter(pk=market_id, actif=True).first()
                if marche is None:
                    form.add_error(None, "Le marché sélectionné est invalide ou inactif.")
                    return self.form_invalid(form)

                emplacement = (
                    Emplacement.objects.select_for_update()
                    .filter(pk=emplacement_id, marche=marche)
                    .first()
                    if emplacement_id
                    else None
                )
                if emplacement is None or not emplacement.is_libre():
                    if not marche.emplacements.filter(support_installe__isnull=True).exists():
                        form.add_error(
                            None,
                            "Ce marché n'a plus d'emplacement libre. Choisissez un autre "
                            "marché ou créez le panneau hors marché.",
                        )
                    else:
                        form.add_error(
                            None,
                            "Sélectionnez un emplacement libre de ce marché avant de créer "
                            "le panneau.",
                        )
                    return self.form_invalid(form)

            # 1. Sauvegarde du support
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            if emplacement is not None:
                self.object.emplacement = emplacement
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
                        eclairage=self.request.POST.get(f'form-{i}-eclairage', 'oui'),
                        notes=self.request.POST.get(f'form-{i}-notes', ''),
                        photo=self.request.FILES.get(f'form-{i}-photo'),
                    )
            
            elif type_support == 'ecran' and ecran_form.is_valid():
                ecran = ecran_form.save(commit=False)
                ecran.support = self.object
                ecran.save()

        log_action(self.request, AuditLog.ACTION_CREATE, 'inventory', obj=self.object, detail=f"Création support: {self.object.code}")
        messages.success(self.request, f'Support {self.object.code} créé.')
        return redirect('support_detail', uuid=self.object.uuid)
    


# --- VUE DE MODIFICATION ---
class SupportUpdateView(StaffRequiredMixin, UpdateView):
    model = Support
    form_class = SupportForm
    template_name = 'inventory/support_form.html'
    pk_url_kwarg = 'uuid'
    
    def get_object(self, queryset=None):
        """
        Force Django à récupérer l'objet en filtrant explicitement 
        le champ 'uuid' du modèle avec la valeur reçue dans l'URL.
        """
        uuid_val = self.kwargs.get(self.pk_url_kwarg)
        return get_object_or_404(Support, uuid=uuid_val)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        support = self.object  # UpdateView définit déjà self.object
        ecran_instance = getattr(support, 'ecran_info', None)
        
        # Initialisation du formulaire écran avec préfixe
        if self.request.POST:
            context['ecran_form'] = EcranNumeriqueForm(
                self.request.POST, 
                self.request.FILES, 
                prefix='ecran', 
                instance=ecran_instance
            )
        else:
            context['ecran_form'] = EcranNumeriqueForm(prefix='ecran', instance=ecran_instance)
        
        context['face_form'] = FacePanneauForm()
        context['title'] = f'Modifier — {support.code}'
        context['existing_faces'] = support.faces.all()
        context['marches'] = Marche.objects.filter(actif=True).order_by('nom')
        return context

    def form_valid(self, form):
        support = self.object
        ecran_instance = getattr(support, 'ecran_info', None)
        
        # Récupération/Validation directe du formulaire écran sans réévaluer tout le contexte
        if support.type_support == 'ecran':
            ecran_form = EcranNumeriqueForm(self.request.POST, self.request.FILES, prefix='ecran', instance=ecran_instance)
        else:
            ecran_form = None

        with transaction.atomic():
            # 1. Sauvegarde du support principal
            support = form.save()

            # 1bis. Rattachement / détachement d'un emplacement de marché
            emplacement_id = self.request.POST.get('emplacement')
            if emplacement_id:
                emplacement = Emplacement.objects.filter(pk=emplacement_id).first()
                occupant = getattr(emplacement, 'support_installe', None) if emplacement else None
                if emplacement and (occupant is None or occupant.pk == support.pk):
                    support.emplacement = emplacement
                    support.save()
            elif support.emplacement_id:
                # Le champ a été vidé côté formulaire → on détache le support du marché
                support.emplacement = None
                support.save()

            # 2. Cas ÉCRAN : Validation et sauvegarde
            if support.type_support == 'ecran' and ecran_form:
                if ecran_form.is_valid():
                    e = ecran_form.save(commit=False)
                    e.support = support
                    e.save()
                else:
                    # En cas d'erreur, on repeuple le contexte pour afficher les erreurs du formulaire écran
                    return self.form_invalid(form)
            
            # 3. Cas PANNEAU : Gestion dynamique des faces
            elif support.type_support == 'panneau':
                nb_faces = form.cleaned_data.get('nb_faces', 1)

                for i in range(nb_faces):
                    label = chr(65 + i)  # A, B, C...
                    data = {
                        'eclairage': self.request.POST.get(f'form-{i}-eclairage', 'non'),
                        'notes': self.request.POST.get(f'form-{i}-notes', '')
                    }
                    # On ne touche à la photo QUE si une nouvelle a été envoyée,
                    # sinon on écraserait la photo existante avec None à chaque update.
                    photo = self.request.FILES.get(f'form-{i}-photo')
                    if photo:
                        data['photo'] = photo

                    FacePanneau.objects.update_or_create(
                        support=support,
                        label=label,
                        defaults=data
                    )

                # Nettoyage des faces obsolètes
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
        return redirect('support_detail', uuid=support.uuid)

    def form_invalid(self, form):
        messages.error(self.request, "Veuillez corriger les erreurs dans le formulaire.")
        return self.render_to_response(self.get_context_data(form=form))


# --- VUE DE SUPPRESSION ---
class SupportDeleteView(StaffRequiredMixin, DeleteView):
    model = Support
    template_name = 'inventory/support_confirm_delete.html'
    context_object_name = 'obj'
    pk_url_kwarg = 'uuid'
    
    def get_object(self, queryset=None):
        """
        Force Django à récupérer l'objet en filtrant explicitement 
        le champ 'uuid' du modèle avec la valeur reçue dans l'URL.
        """
        uuid_val = self.kwargs.get(self.pk_url_kwarg)
        return get_object_or_404(Support, uuid=uuid_val)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return self.render_to_response(self.get_context_data())

    def post(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                support = Support.objects.select_for_update().get(
                    uuid=self.kwargs[self.pk_url_kwarg],
                )
                self.object = support
                if self._has_campaign_links(support):
                    messages.error(
                        request,
                        f'Impossible de supprimer {support.code} : ce support est lié '
                        'à une campagne ou à une réservation.',
                    )
                    return redirect('support_detail', uuid=support.uuid)

                if request.user.is_admin:
                    SupportDeletionRequest.objects.filter(
                        support=support,
                        status=SupportDeletionRequest.STATUS_PENDING,
                    ).update(
                        status=SupportDeletionRequest.STATUS_CANCELLED,
                        decided_at=timezone.now(),
                    )
                    transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
                    return self._delete_support(request, support)

                pending = SupportDeletionRequest.objects.select_for_update().filter(
                    support=support,
                    status=SupportDeletionRequest.STATUS_PENDING,
                ).first()
                if pending is None:
                    pending = SupportDeletionRequest.objects.create(
                        support=support,
                        support_code=support.code,
                        requested_by=request.user,
                        requester_name=request.user.get_full_name() or request.user.username,
                    )
                    transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
                    log_action(
                        request,
                        AuditLog.ACTION_CREATE,
                        'inventory',
                        obj=pending,
                        detail=f"Demande de suppression du support {support.code}",
                    )
                    messages.success(
                        request,
                        'Demande enregistrée. Un autre utilisateur doit l’approuver '
                        'depuis la liste des demandes de suppression.',
                    )
                    return redirect('support_detail', uuid=support.uuid)

                if pending.requested_by_id == request.user.pk:
                    messages.error(
                        request,
                        'Vous ne pouvez pas approuver votre propre demande. '
                        'Un autre utilisateur doit la confirmer.',
                    )
                    return redirect('support_deletion_requests')

                pending.status = SupportDeletionRequest.STATUS_APPROVED
                pending.approved_by = request.user
                pending.approver_name = request.user.get_full_name() or request.user.username
                pending.decided_at = timezone.now()
                pending.save(update_fields=[
                    'status', 'approved_by', 'approver_name', 'decided_at',
                ])
                transaction.on_commit(lambda: cache.delete('dashboard_alertes'))
                log_action(
                    request,
                    AuditLog.ACTION_UPDATE,
                    'inventory',
                    obj=pending,
                    detail=f"Approbation de suppression du support {support.code}",
                )
                return self._delete_support(request, support)
        except Support.DoesNotExist:
            messages.error(request, 'Ce support a déjà été supprimé ou n’existe plus.')
            return redirect('support_list')
        except ProtectedError:
            messages.error(
                request,
                'La suppression est bloquée car ce support possède encore des données liées.',
            )
            return redirect('support_detail', uuid=self.object.uuid)

    @staticmethod
    def _has_campaign_links(support):
        return (
            support.lignes_campagne.exists()
            or support.lignes_reservation.exists()
        )

    def _delete_support(self, request, support):
        code = support.code
        support.delete()
        log_action(
            request,
            AuditLog.ACTION_DELETE,
            'inventory',
            detail=f"Suppression support: {code}",
        )
        messages.success(request, f'Le support {code} a été supprimé avec succès.')
        return redirect('support_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        support = self.object
        pending = SupportDeletionRequest.objects.filter(
            support=support,
            status=SupportDeletionRequest.STATUS_PENDING,
        ).select_related('requested_by').first()
        has_campaign_links = self._has_campaign_links(support)
        context.update({
            'title': 'Supprimer le support',
            'header': 'Suppression de support',
            'message_title': 'Supprimer ce support ?',
            'message_body': f"Vous êtes sur le point de supprimer le support {support.code}.",
            'hint': 'Les panneaux et écrans liés à une campagne ne peuvent pas être supprimés.',
            'support': support,
            'pending_request': pending,
            'has_campaign_links': has_campaign_links,
            'is_requester': bool(pending and pending.requested_by_id == self.request.user.pk),
            'can_delete_alone': self.request.user.is_admin,
        })
        return context


class SupportDeletionRequestsView(StaffRequiredMixin, ListView):
    model = SupportDeletionRequest
    template_name = 'inventory/support_deletion_requests.html'
    context_object_name = 'deletion_requests'

    def get_queryset(self):
        return SupportDeletionRequest.objects.filter(
            status=SupportDeletionRequest.STATUS_PENDING,
            support__isnull=False,
        ).select_related('support', 'requested_by').order_by('created_at')


from core.mixins import SortableListMixin


from core.mixins import SortableListMixin


class MaintenanceListView(LoginRequiredMixin, SortableListMixin, ListView):
    model               = Maintenance
    template_name       = 'inventory/maintenance_list.html'
    context_object_name = 'maintenances'

    SORT_FIELDS = {
        'support':           'support__code',
        'face':              'face__label',
        'technicien':        'effectue_par__username',
        'date_intervention': 'date_intervention',
        'etat_apres':        'etat_apres',
    }
    DEFAULT_SORT = 'date_intervention'
    DEFAULT_DIR  = 'desc'
    paginate_by  = 20

    def get_queryset(self):
        qs = Maintenance.objects.select_related('support', 'face', 'effectue_par')

        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)

        support_id = self.request.GET.get('support')
        etat_apres = self.request.GET.get('etat')
        technicien = self.request.GET.get('technicien')

        if support_id: qs = qs.filter(support_id=support_id)
        if etat_apres: qs = qs.filter(etat_apres=etat_apres)
        if technicien:
            qs = qs.filter(effectue_par_id=technicien)

        qs = self.apply_sort(qs)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_qs = Maintenance.objects.all()
        if self.request.user.is_technicien:
            base_qs = base_qs.filter(effectue_par=self.request.user)

        context['kpi_total'] = base_qs.count()
        context['kpi_operationnels'] = base_qs.filter(
            etat_apres__in=Support.ETATS_OPERATIONNELS,
        ).count()
        context['kpi_en_maintenance'] = base_qs.filter(etat_apres='maintenance').count()
        context['kpi_en_panne'] = base_qs.filter(etat_apres='panne').count()
        context['kpi_pannes_actives'] = Support.objects.filter(actif=True, etat='panne').count()

        context['etat_choices'] = Maintenance.ETAT_CHOICES
        context['supports']     = Support.objects.filter(actif=True).order_by('code')
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
        if self.request.user.is_technicien:
            qs = qs.filter(effectue_par=self.request.user)
        return qs


import json

class MaintenanceCreateView(LoginRequiredMixin, CreateView):
    model         = Maintenance
    form_class    = MaintenanceForm
    template_name = 'inventory/maintenance_form.html'
    success_url   = reverse_lazy('maintenance_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        support_id = self.kwargs.get('pk') or self.request.GET.get('support')
        if support_id:
            kwargs['support_pk'] = support_id
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
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

        appliquer_tout = form.cleaned_data.get('appliquer_tout_le_support', False)

        if appliquer_tout:
            support = form.cleaned_data['support'] or form.instance.support
            maintenances = Maintenance.creer_pour_support_complet(
                support=support,
                etat_apres=form.instance.etat_apres,
                effectue_par=form.instance.effectue_par,
                description=form.instance.description,
                photo=self.request.FILES.get('photo'),
            )
            messages.success(
                self.request,
                f"{len(maintenances)} face(s) mise(s) à jour pour {support.code}."
            )
            return redirect(self.success_url)

        response = super().form_valid(form)
        messages.success(self.request, f'Maintenance enregistrée pour {self.object.support.code}.')
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Veuillez corriger les erreurs dans le formulaire.")
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Nouvelle maintenance'
        context['support_types_json'] = json.dumps({
            s.pk: s.type_support
            for s in Support.objects.filter(actif=True).only('pk', 'type_support')
        })
        return context


class MaintenanceUpdateView(LoginRequiredMixin, UpdateView):
    model         = Maintenance
    form_class    = MaintenanceForm
    template_name = 'inventory/maintenance_form.html'
    success_url   = reverse_lazy('maintenance_list')

    def get_queryset(self):
        qs = Maintenance.objects.select_related('support', 'effectue_par')
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
        context['support_types_json'] = json.dumps({
            s.pk: s.type_support
            for s in Support.objects.filter(actif=True).only('pk', 'type_support')
        })
        return context


class MaintenanceQuickRepairView(LoginRequiredMixin, View):
    """Enregistre automatiquement une réparation pour un support ou une face en panne."""

    def post(self, request, pk):
        maintenance = get_object_or_404(Maintenance.objects.select_related('support', 'face'), pk=pk)
        support = maintenance.support
        face = maintenance.face

        description = request.POST.get('description', '').strip()
        if not description:
            face_info = f" (Face {face.label})" if face else ""
            description = f"Réparation effectuée — Remise en service opérationnel suite à l'intervention #{maintenance.pk}{face_info}"

        photo = request.FILES.get('photo')
        date_str = request.POST.get('date_intervention')
        if date_str:
            try:
                date_intervention = datetime.fromisoformat(date_str)
                if timezone.is_naive(date_intervention):
                    date_intervention = timezone.make_aware(date_intervention)
            except Exception:
                date_intervention = timezone.now()
        else:
            date_intervention = timezone.now()

        nouvelle = Maintenance.objects.create(
            support=support,
            face=face,
            effectue_par=request.user,
            date_intervention=date_intervention,
            etat_apres=Maintenance.ETAT_BON,
            description=description,
            photo=photo,
        )

        messages.success(
            request,
            f"Réparation enregistrée avec succès pour {support.code}{f' (Face {face.label})' if face else ''} ! Le statut est maintenant opérationnel."
        )
        return redirect('maintenance_detail', pk=nouvelle.pk)


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


class PeriodePanneDetailView(LoginRequiredMixin, View):
    template_name = 'inventory/periode_panne_detail.html'

    def get(self, request, pk):
        panne = get_object_or_404(
            Maintenance.objects.select_related('support', 'face', 'effectue_par'),
            pk=pk,
            etat_apres=ETAT_PANNE,
        )
        maintenances = Maintenance.objects.filter(
            support=panne.support,
            face=panne.face,
            date_intervention__gte=panne.date_intervention,
        ).select_related('effectue_par').order_by('date_intervention')

        resolution = None
        for maintenance in maintenances:
            if maintenance.etat_apres == ETAT_BON:
                resolution = maintenance
                break

        fin = resolution.date_intervention if resolution else None
        duree = (fin - panne.date_intervention) if fin else timezone.now() - panne.date_intervention

        return render(request, self.template_name, {
            'periode': {
                'support': panne.support,
                'face': panne.face,
                'panne': panne,
                'resolution': resolution,
                'fin': fin,
                'duree': duree,
                'resolue': resolution is not None,
            },
            'title': f'Période de panne — {panne.support.code}',
        })