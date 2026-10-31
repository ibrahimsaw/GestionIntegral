import io
import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.cache import cache
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import ListView, DetailView, DeleteView, TemplateView, FormView, View

from accounts.decorators import *
from accounts.models import AuditLog
from accounts.audit import log_action, log_changes
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


def _creer_version_horaire(ecran, form, utilisateur):
    date_debut = form.cleaned_data['date_debut']
    if EcranProgramme.objects.filter(
        ecran=ecran,
        date_debut=date_debut,
    ).exists():
        form.add_error(
            'date_debut',
            "Un programme existe déjà à cette date. Choisissez une autre date.",
        )
        return False

    programme = EcranProgramme.objects.create(
        ecran=ecran,
        date_debut=date_debut,
        cree_par=utilisateur,
    )
    programme.plages.bulk_create([
        EcranPlageHoraire(
            programme=programme,
            jour_semaine=jour,
            heure_debut=debut,
            heure_fin=fin,
        )
        for jour, debut, fin in form.cleaned_data['plages_validees']
    ])

    precedent = EcranProgramme.objects.filter(
        ecran=ecran,
        date_debut__lt=date_debut,
    ).order_by('-date_debut').first()
    suivant = EcranProgramme.objects.filter(
        ecran=ecran,
        date_debut__gt=date_debut,
    ).order_by('date_debut').first()

    if precedent:
        precedent.date_fin = date_debut - timedelta(days=1)
        precedent.save(update_fields=['date_fin'])
    programme.date_fin = (
        suivant.date_debut - timedelta(days=1) if suivant else None
    )
    programme.save(update_fields=['date_fin'])
    return True


def _initial_programme_horaire(ecran=None):
    source_programme = None
    demain = timezone.localdate() + timedelta(days=1)
    initial = {'date_debut': demain}
    if ecran:
        dernier = ecran.programmes.order_by('-date_debut').first()
        if dernier:
            source_programme = dernier
            if dernier.date_debut >= demain:
                initial['date_debut'] = dernier.date_debut + timedelta(days=1)
        else:
            heure_debut = ecran.heure_allumage
            heure_fin = ecran.heure_extinction

    if source_programme:
        plages_par_jour = {jour: [] for _, jour, _ in EcranProgrammeForm.JOUR_FIELDS}
        for plage in source_programme.plages.all():
            plages_par_jour[plage.jour_semaine].append(
                f'{plage.heure_debut:%H:%M}-{plage.heure_fin:%H:%M}'
            )
        for field_name, jour, _ in EcranProgrammeForm.JOUR_FIELDS:
            initial[field_name] = ', '.join(plages_par_jour[jour])
    else:
        heure_debut = (
            heure_debut if ecran else time(6, 0)
        )
        heure_fin = (
            heure_fin if ecran else time(23, 0)
        )
        horaire_standard = f'{heure_debut:%H:%M}-{heure_fin:%H:%M}'
        for field_name, _, _ in EcranProgrammeForm.JOUR_FIELDS:
            initial[field_name] = horaire_standard
    return initial

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
        nb_maintenance = etats.get(Support.ETAT_A_MAINTENIR, 0)
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
        
        qs = Support.objects.select_related(
            'emplacement__marche',
        ).prefetch_related('faces').filter(
            Q(latitude__isnull=False, longitude__isnull=False)
            | Q(
                emplacement__marche__latitude__isnull=False,
                emplacement__marche__longitude__isnull=False,
            )
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
            latitude = s.latitude
            longitude = s.longitude
            if s.emplacement_id:
                d['marche_id'] = s.emplacement.marche_id
                if latitude is None or longitude is None:
                    latitude = s.emplacement.marche.latitude
                    longitude = s.emplacement.marche.longitude
            
            # ✅ S'assurer que type_panneau est bien exposé dans les properties
            if 'type_panneau' not in d:
                d['type_panneau'] = s.type_panneau if hasattr(s, 'type_panneau') else None

            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [float(longitude), float(latitude)]},
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
                    ).filter(
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
            plages_actives = ecran.plages_actives(today)
            total_j = ecran.secondes_disponibles_jour(today)
            hours_j = total_j / 3600 if total_j > 0 else 1
            libres_1h = max(0, round(3600 - ecran.calculer_occupation_reelle(today) / hours_j))

            data['ecran'] = {
                'resolution':         ecran.get_resolution_display(),
                'cellule':            ecran.get_cellule_display(),
                'taux_occupation':    taux,
                'secondes_libres_1h': libres_1h,
                'heure_debut':        plages_actives[0][0].strftime('%H:%M') if plages_actives else None,
                'heure_fin':          plages_actives[-1][1].strftime('%H:%M') if plages_actives else None,
                'plages_actives': [
                    {
                        'debut': debut.strftime('%H:%M'),
                        'fin': fin.strftime('%H:%M'),
                    }
                    for debut, fin in plages_actives
                ],
            }

            spots_qs = LigneCampagne.objects.filter(
                support=support,
            ).filter(
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
                etat=Support.ETAT_A_MAINTENIR,
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
    faces = FacePanneau.objects.filter(
        support_id=support_id,
    ).order_by('label').values('id', 'label', 'etat')
    data = []
    for face in faces:
        latest_repair = Maintenance.objects.filter(
            face_id=face['id'],
            etat_apres=face['etat'],
        ).order_by('-date_intervention', '-pk').first()
        data.append({
            'id': face['id'],
            'label': face['label'],
            'etat': face['etat'],
            'repair_id': latest_repair.pk if latest_repair else None,
        })
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

        today = timezone.localdate()
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
        panneau_stats = []
        spots_data  = []
        ecran_programme_data = []

        # ── Logique spécifique PANNEAU ────────────────────────────────────
        if support.type_support == 'panneau':
            occupancy_stats = self._get_panel_occupancy_stats(support, today)
            faces = list(support.faces.all())
            for face in faces:
                face_stats = occupancy_stats['taux_par_face'].get(
                    face.pk, {'taux_actuel': 0.0, 'taux_annuel': 0.0}
                )
                face.taux_actuel = face_stats['taux_actuel']
                face.taux_annuel = face_stats['taux_annuel']
            panneau_stats = [
                ('Taux actuel', 'bi-speedometer2', f'{occupancy_stats["taux_actuel"]}%', 'var(--color-primary)'),
                (f'Taux annuel {today.year}', 'bi-graph-up', f'{occupancy_stats["taux_annuel"]}%', 'var(--color-primary)'),
            ]
            info_rows += [
                ('Taux actuel', f'{occupancy_stats["taux_actuel"]}%'),
                (f'Taux annuel {today.year}', f'{occupancy_stats["taux_annuel"]}%'),
            ]
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
                plage_str = ecran.horaires_actifs_display(today)
                stats_spots = self._get_screen_spot_stats(ecran, today)

                info_rows += [
                    ('Résolution',      ecran.get_resolution_display()),
                    ('Cellule', f'{ecran.format_detail["dimensions"]} ({ecran.format_detail["superficie"]}m²)'),
                    ('Type écran',      ecran.get_type_ecran_display()),
                    ('Plage diffusion', plage_str),
                    ('Taux actuel',     f'{stats_spots["taux_actuel"]}%' if stats_spots['taux_actuel'] is not None else 'Objectif non défini'),
                    (f'Taux annuel {today.year} (01/01 à aujourd’hui)', f'{stats_spots["taux_annuel"]}%' if stats_spots['taux_annuel'] is not None else 'Objectif non défini'),
                ]

                ecran_stats = [
                    ('Résolution', 'bi-aspect-ratio', ecran.get_resolution_display(), 'var(--text)'),
                    ('Cellule',    'bi-tv',           f'{ecran.format_detail["dimensions"]} ({ecran.format_detail["superficie"]}m²)',              'var(--text)'),
                    ('Taux actuel', 'bi-speedometer2', f'{stats_spots["taux_actuel"]}%' if stats_spots['taux_actuel'] is not None else '—', 'var(--color-primary)'),
                    (f'Taux annuel {today.year} (01/01 à aujourd’hui)', 'bi-graph-up', f'{stats_spots["taux_annuel"]}%' if stats_spots['taux_annuel'] is not None else '—', 'var(--color-primary)'),
                    ('Spots cumulés des campagnes', 'bi-broadcast', f'{stats_spots["spots_realises"]:,}'.replace(',', ' '), 'var(--text)'),
                    ('Objectif annuel', 'bi-bullseye', f'{stats_spots["objectif_spots_annuel"]:,}'.replace(',', ' ') if stats_spots['objectif_spots_annuel'] is not None else 'Non défini', 'var(--text)'),
                    ('Diffusion',  'bi-clock',        plage_str,                       'var(--color-primary)'),
                ]

                date_fin_telemetrie = today + timedelta(days=30)
                lignes_telemetrie = LigneCampagne.objects.filter(
                    support=support,
                    campagne__statut__in=[
                        Campagne.STATUT_EN_COURS,
                        Campagne.STATUT_A_VENIR,
                    ],
                    campagne__date_debut__lte=date_fin_telemetrie,
                    campagne__date_fin__gte=today,
                ).filter(
                    Q(date_debut__lte=date_fin_telemetrie)
                    | Q(date_debut__isnull=True)
                ).filter(
                    Q(date_fin__gte=today) | Q(date_fin__isnull=True)
                ).select_related(
                    'campagne__client', 'campagne', 'support'
                ).prefetch_related('campagne__pauses')

                for ligne in lignes_telemetrie:
                    spots_data.append({
                        'id':       ligne.pk,
                        'name':     f"{ligne.campagne.client.nom} — {ligne.campagne.nom}",
                        'dur':      EcranNumerique.DUREE_SPOT_SECONDES,
                        'interval': ligne.get_frequence_effective() or 0,
                        'windows':  ligne.get_tranches_effective() or '',
                        'start':    max(
                            ligne.date_debut or ligne.campagne.date_debut,
                            ligne.campagne.date_debut,
                        ).isoformat(),
                        'end':      min(
                            ligne.date_fin or ligne.campagne.date_fin,
                            ligne.campagne.date_fin,
                        ).isoformat(),
                        'visuals':  ligne.campagne.get_nombre_visuels_effective(),
                        'pauses':   [
                            {
                                'start': pause.date_debut.isoformat(),
                                'end': (pause.date_fin or date_fin_telemetrie).isoformat(),
                            }
                            for pause in ligne.campagne.pauses.all()
                            if pause.date_debut <= date_fin_telemetrie
                            and (pause.date_fin is None or pause.date_fin >= today)
                        ],
                    })
                programmes = list(
                    ecran.programmes.prefetch_related('plages').order_by(
                        'date_debut', 'pk'
                    )
                )
                for offset in range(32):
                    jour = today + timedelta(days=offset)
                    programmes_actifs = [
                        programme for programme in programmes
                        if programme.date_debut <= jour
                        and (
                            programme.date_fin is None
                            or programme.date_fin >= jour
                        )
                    ]
                    programme = (
                        max(
                            programmes_actifs,
                            key=lambda item: (item.date_debut, item.pk),
                        )
                        if programmes_actifs else None
                    )
                    plages = (
                        [
                            (plage.heure_debut, plage.heure_fin)
                            for plage in programme.plages.all()
                            if plage.jour_semaine == jour.weekday()
                        ]
                        if programme else
                        [(ecran.heure_allumage, ecran.heure_extinction)]
                    )
                    ecran_programme_data.append({
                        'date': jour.isoformat(),
                        'windows': [
                            [debut.strftime('%H:%M'), fin.strftime('%H:%M')]
                            for debut, fin in plages
                        ],
                    })

        # ── Contexte final ────────────────────────────────────────────────
        context.update({
            'support':        support,
            'annee_taux_occupation': today.year,
            'lignes_actives': lignes_actives,
            'historique':     historique,
            'maintenances':   maintenances,
            'ecran':          ecran,
            'ecran_stats':    ecran_stats,
            'info_rows':      info_rows,
            'panneau_stats': panneau_stats,
            'faces': faces if support.type_support == 'panneau' else [],
            'spots_data': spots_data,
            'ecran_programme_data': ecran_programme_data,
            'reservations':   reservations,
            'pending_deletion_request': SupportDeletionRequest.objects.filter(
                support=support,
                status=SupportDeletionRequest.STATUS_PENDING,
            ).select_related('requested_by').first(),
        })
        return context

    @staticmethod
    def _count_occupied_days(intervals, period_start, period_end):
        clipped = sorted(
            (max(start, period_start), min(end, period_end))
            for start, end in intervals
            if start <= period_end and end >= period_start
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
    def _get_panel_occupancy_stats(cls, support, today):
        year_start = today.replace(month=1, day=1)
        year_end = today.replace(month=12, day=31)
        year_days = (year_end - year_start).days + 1
        elapsed_days = (today - year_start).days + 1
        face_ids = list(support.faces.values_list('pk', flat=True))
        if not face_ids:
            return {
                'taux_actuel': 0.0,
                'taux_annuel': 0.0,
                'taux_par_face': {},
            }

        campaign_intervals = {}
        lines = LigneCampagne.objects.filter(
            face_id__in=face_ids,
            campagne__date_debut__lte=year_end,
            campagne__date_fin__gte=year_start,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_A_VENIR,
                Campagne.STATUT_EN_PAUSE,
                Campagne.STATUT_TERMINEE,
            ],
        ).values_list('face_id', 'campagne__date_debut', 'campagne__date_fin')
        for face_id, start, end in lines:
            campaign_intervals.setdefault(face_id, []).append((start, end))

        annual_intervals = {
            face_id: list(intervals)
            for face_id, intervals in campaign_intervals.items()
        }
        current_intervals = {
            face_id: [
                (start, min(end, today))
                for start, end in intervals
                if start <= today
            ]
            for face_id, intervals in campaign_intervals.items()
        }

        tomorrow = today + timedelta(days=1)
        reservations = ReservationLigne.objects.filter(
            face_id__in=face_ids,
            reservation__date_fin__gte=timezone.make_aware(
                datetime.combine(tomorrow, time.min)
            ),
            reservation__date_debut__lt=timezone.make_aware(
                datetime.combine(year_end + timedelta(days=1), time.min)
            ),
            reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
        ).values_list(
            'face_id', 'reservation__date_debut', 'reservation__date_fin'
        )
        for face_id, start, end in reservations:
            start = timezone.localtime(start).date() if timezone.is_aware(start) else start.date()
            end = timezone.localtime(end).date() if timezone.is_aware(end) else end.date()
            annual_intervals.setdefault(face_id, []).append(
                (max(start, tomorrow), end)
            )

        occupied_days_by_face = {
            face_id: {
                'actuels': cls._count_occupied_days(
                    current_intervals.get(face_id, []), year_start, today
                ),
                'annuels': cls._count_occupied_days(
                    annual_intervals.get(face_id, []), year_start, year_end
                ),
            }
            for face_id in face_ids
        }
        taux_par_face = {
            face_id: {
                'taux_actuel': round(
                    stats['actuels'] / elapsed_days * 100, 1
                ),
                'taux_annuel': round(
                    stats['annuels'] / year_days * 100, 1
                ),
            }
            for face_id, stats in occupied_days_by_face.items()
        }
        occupied_days_current = sum(
            stats['actuels'] for stats in occupied_days_by_face.values()
        )
        occupied_days_annual = sum(
            stats['annuels'] for stats in occupied_days_by_face.values()
        )
        return {
            'taux_actuel': round(
                occupied_days_current / (len(face_ids) * elapsed_days) * 100,
                1,
            ),
            'taux_annuel': round(
                occupied_days_annual / (len(face_ids) * year_days) * 100,
                1,
            ),
            'taux_par_face': taux_par_face,
        }

    @staticmethod
    def _get_screen_spot_stats(ecran, today):
        """Calcule le taux quotidien et le cumul annuel d'occupation de l'écran."""
        year_start = today.replace(month=1, day=1)
        year_end = today.replace(month=12, day=31)
        target = ecran.calculer_objectif_spots_annuel(today.year, today)
        campaign_lines = LigneCampagne.objects.filter(
            support=ecran.support,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_EN_PAUSE,
                Campagne.STATUT_A_VENIR,
                Campagne.STATUT_TERMINEE,
            ],
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=year_start,
        ).select_related('campagne', 'support')
        realized = sum(
            line.calculer_spots_sur_periode(year_start, today)
            for line in campaign_lines
        )

        todays_lines = LigneCampagne.objects.filter(
            support=ecran.support,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_A_VENIR,
            ],
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
        ).select_related('campagne', 'support')
        spots_today = sum(
            line.calculer_spots_sur_periode(today, today)
            for line in todays_lines
        )
        available_seconds_today = ecran.secondes_disponibles_jour(today)
        reserved_seconds_today = (
            spots_today * EcranNumerique.DUREE_SPOT_SECONDES
        )
        available_seconds_year_to_date = (
            target * EcranNumerique.DUREE_SPOT_SECONDES
        )
        reserved_seconds_year_to_date = (
            realized * EcranNumerique.DUREE_SPOT_SECONDES
        )

        future_spots = 0
        for line in LigneCampagne.objects.filter(
            support=ecran.support,
            campagne__statut__in=[
                Campagne.STATUT_EN_COURS,
                Campagne.STATUT_A_VENIR,
            ],
        ).select_related('campagne', 'support'):
            future_spots += line.calculer_spots_sur_periode(
                today + timedelta(days=1), year_end
            )

        forecast = realized + future_spots
        return {
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


class EcranObjectifsAnnuelsView(StaffRequiredMixin, View):
    template_name = 'inventory/ecran_objectifs_annuels.html'

    def get_ecran(self):
        support = get_object_or_404(Support, uuid=self.kwargs['uuid'])
        if support.type_support != Support.TYPE_ECRAN:
            raise Http404
        return get_object_or_404(
            EcranNumerique.objects.select_related('support').prefetch_related(
                'programmes__plages', 'programmes__cree_par'
            ),
            support=support,
        )

    @staticmethod
    def _historique_par_annee(ecran, today):
        created_on = timezone.localtime(ecran.support.created_at).date()
        fin_annee_courante = date(today.year, 12, 31)
        programmes = list(
            ecran.programmes.prefetch_related('plages', 'cree_par')
            .filter(date_debut__lte=fin_annee_courante)
            .order_by('date_debut', 'pk')
        )
        historique = []
        for annee in range(created_on.year, today.year + 1):
            debut_annee = max(date(annee, 1, 1), created_on)
            fin_annee = date(annee, 12, 31)
            if debut_annee > fin_annee:
                continue

            periodes = []
            curseur = debut_annee
            programmes_annee = [
                programme for programme in programmes
                if programme.date_debut <= fin_annee
                and (programme.date_fin is None or programme.date_fin >= debut_annee)
            ]
            for programme in programmes_annee:
                debut_programme = max(programme.date_debut, debut_annee)
                fin_programme = min(
                    programme.date_fin or fin_annee,
                    fin_annee,
                )
                if debut_programme > fin_programme:
                    continue
                if curseur < debut_programme:
                    periodes.append({
                        'debut': curseur,
                        'fin': debut_programme - timedelta(days=1),
                        'programme': None,
                    })
                periodes.append({
                    'debut': debut_programme,
                    'fin': fin_programme,
                    'programme': programme,
                })
                curseur = max(curseur, fin_programme + timedelta(days=1))

            if curseur <= fin_annee:
                periodes.append({
                    'debut': curseur,
                    'fin': fin_annee,
                    'programme': None,
                })

            historique.append({
                'annee': annee,
                'objectif': ecran.calculer_objectif_spots_annuel(
                    annee, fin_annee
                ),
                'periodes': periodes,
                'est_annee_courante': annee == today.year,
            })
        return historique

    def get_context_data(self, ecran, programme_form=None):
        today = timezone.localdate()
        annee = today.year
        if programme_form is None:
            programme_form = EcranProgrammeForm(
                prefix='programme',
                initial={
                    'date_debut': timezone.localdate() + timedelta(days=1),
                },
            )
        periode_debut = max(
            date(annee, 1, 1),
            timezone.localtime(ecran.support.created_at).date(),
        )
        return {
            'support': ecran.support,
            'ecran': ecran,
            'annee_courante': annee,
            'programme_form': programme_form,
            'objectif_calcule': ecran.calculer_objectif_spots_annuel(annee, today),
            'periode_debut': periode_debut,
            'periode_fin': today if periode_debut <= today else None,
            'historique_par_annee': self._historique_par_annee(ecran, today),
        }

    def get(self, request, *args, **kwargs):
        ecran = self.get_ecran()
        return render(request, self.template_name, self.get_context_data(ecran))

    def post(self, request, *args, **kwargs):
        ecran = self.get_ecran()
        programme_form = EcranProgrammeForm(
            request.POST,
            prefix='programme',
        )
        if programme_form.is_valid():
            if (
                programme_form.cleaned_data.get('a_enregistrer')
                and EcranProgramme.objects.filter(
                    ecran=ecran,
                    date_debut=programme_form.cleaned_data['date_debut'],
                ).exists()
            ):
                programme_form.add_error(
                    'date_debut',
                    "Un programme existe déjà à cette date. Choisissez une autre date.",
                )
            else:
                with transaction.atomic():
                    if programme_form.cleaned_data.get('a_enregistrer'):
                        if not _creer_version_horaire(
                            ecran, programme_form, request.user
                        ):
                            return render(
                                request,
                                self.template_name,
                                self.get_context_data(
                                    ecran,
                                    programme_form=programme_form,
                                ),
                            )
                        ecran.refresh_from_db()
                    annee = timezone.localdate().year
                    nombre_spots = ecran.calculer_objectif_spots_annuel(annee)
                    ObjectifSpotsEcranAnnuel.objects.update_or_create(
                        ecran=ecran,
                        annee=annee,
                        defaults={'nombre_spots': nombre_spots},
                    )
                messages.success(
                    request,
                    (
                        "Le programme a été enregistré et l’objectif recalculé."
                        if programme_form.cleaned_data.get('a_enregistrer')
                        else "L’objectif a été recalculé automatiquement."
                    ),
                )
                return redirect(
                    'ecran_objectifs_annuels',
                    uuid=ecran.support.uuid,
                )
        return render(
            request,
            self.template_name,
            self.get_context_data(ecran, programme_form=programme_form),
        )
    
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
        if (
            form.cleaned_data.get('type_support') == Support.TYPE_ECRAN
            and not ecran_form.is_valid()
        ):
            return self.form_invalid(form)
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
            
            elif type_support == Support.TYPE_ECRAN:
                ecran = ecran_form.save(commit=False)
                ecran.support = self.object
                ecran.save()
                annee = timezone.localdate().year
                ObjectifSpotsEcranAnnuel.objects.update_or_create(
                    ecran=ecran,
                    annee=annee,
                    defaults={
                        'nombre_spots': ecran.calculer_objectif_spots_annuel(
                            annee
                        ),
                    },
                )

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
            if not ecran_form.is_valid():
                return self.form_invalid(form)
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
                e = ecran_form.save(commit=False)
                e.support = support
                e.save()
            
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
            obj=support,
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
        qs = Maintenance.objects.select_related(
            'support',
            'face',
            'effectue_par',
            'reparation_commencee_par',
        )

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

        context['kpi_total'] = base_qs.count()
        context['kpi_operationnels'] = base_qs.filter(
            etat_apres__in=Support.ETATS_OPERATIONNELS,
        ).count()
        context['kpi_en_maintenance'] = base_qs.filter(
            etat_apres=Support.ETAT_A_MAINTENIR,
        ).count()
        context['kpi_en_panne'] = base_qs.filter(etat_apres='panne').count()
        context['kpi_pannes_actives'] = Support.objects.filter(actif=True, etat='panne').count()

        context['etat_choices'] = Maintenance.ETAT_CHOICES
        context['supports']     = Support.objects.filter(actif=True).order_by('code')
        context['techniciens'] = User.objects.filter(
            maintenances__isnull=False
        ).distinct()
        context['maintenance_can_manage_all'] = self.request.user.is_staff_regie_role
        context['maintenance_can_manage_ids'] = (
            set(_maintenance_management_queryset(self.request.user).values_list('pk', flat=True))
            if self.request.user.is_technicien
            else set()
        )
        return context


def _maintenance_management_queryset(user):
    queryset = Maintenance.objects.all()
    if user.is_staff_regie_role:
        return queryset
    if user.is_technicien:
        return queryset.filter(
            Q(effectue_par=user) | Q(pk__in=_open_incident_root_ids())
        )
    return queryset.none()


def _open_incident_root_ids():
    """IDs des interventions qui sont encore à réparer pour leur support/face."""
    roots_by_element = {}
    events = Maintenance.objects.order_by(
        'support_id',
        'face_id',
        'date_intervention',
        'pk',
    ).values_list('support_id', 'face_id', 'pk', 'etat_apres')
    for support_id, face_id, maintenance_id, etat_apres in events:
        element_key = (support_id, face_id)
        if etat_apres in (Maintenance.ETAT_A_MAINTENIR, Maintenance.ETAT_PANNE):
            roots_by_element.setdefault(element_key, maintenance_id)
        elif etat_apres == Maintenance.ETAT_BON:
            roots_by_element.pop(element_key, None)
    return set(roots_by_element.values())


class MaintenanceDetailView(LoginRequiredMixin, DetailView):
    model               = Maintenance
    template_name       = 'inventory/maintenance_detail.html'
    context_object_name = 'maintenance'

    def get_queryset(self):
        qs = Maintenance.objects.select_related(
            'support',
            'face',
            'effectue_par',
            'reparation_commencee_par',
        )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        maintenance = self.object
        context['maintenance_can_manage'] = _maintenance_management_queryset(
            self.request.user
        ).filter(pk=maintenance.pk).exists()
        context['maintenance_can_quick_repair'] = (
            context['maintenance_can_manage']
            and maintenance.etat_apres in (
                Maintenance.ETAT_PANNE,
                Maintenance.ETAT_A_MAINTENIR,
            )
        )
        element = maintenance.face or maintenance.support
        context['etat_actuel'] = element.etat
        context['etat_actuel_label'] = element.get_etat_display()
        context['etat_choices'] = Maintenance.ETAT_CHOICES
        incident_root = _get_open_incident_root(maintenance)
        context['maintenance_is_open_incident'] = (
            incident_root is not None and incident_root.pk == maintenance.pk
        )
        context['maintenance_can_start_repair'] = (
            incident_root
            and incident_root.pk == maintenance.pk
            and not incident_root.reparation_commencee_le
            and context['maintenance_can_manage']
            and (
                self.request.user.is_staff_regie_role
                or self.request.user.is_technicien_role
            )
        )
        return context


def _get_open_incident_root(maintenance):
    root = None
    events = Maintenance.objects.filter(
        support_id=maintenance.support_id,
        face_id=maintenance.face_id,
    ).order_by('date_intervention', 'pk')
    for event in events:
        if event.etat_apres in (Maintenance.ETAT_A_MAINTENIR, Maintenance.ETAT_PANNE):
            if root is None:
                root = event
        elif event.etat_apres == Maintenance.ETAT_BON:
            root = None
    return root


def _log_maintenance_creation(request, maintenance, detail):
    log_action(
        request,
        AuditLog.ACTION_CREATE,
        AuditLog.MODULE_INVENTORY,
        obj=maintenance,
        detail=detail,
        changes={
            'support': {'avant': '', 'apres': maintenance.support.code},
            'face': {
                'avant': '',
                'apres': maintenance.face.label if maintenance.face else '',
            },
            'effectue_par': {
                'avant': '',
                'apres': str(maintenance.effectue_par or ''),
            },
            'date_intervention': {
                'avant': '',
                'apres': maintenance.date_intervention.isoformat(),
            },
            'etat_apres': {'avant': '', 'apres': maintenance.etat_apres},
            'description': {'avant': '', 'apres': maintenance.description},
            'photo': {
                'avant': '',
                'apres': maintenance.photo.name if maintenance.photo else '',
            },
        },
    )


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

        support = form.cleaned_data['support'] or form.instance.support
        if support.type_support == Support.TYPE_PANNEAU:
            states_by_face = form.cleaned_data.get('states_by_face', {})
            faces = [
                (face, states_by_face[face.pk])
                for face in form.faces_queryset
                if states_by_face.get(face.pk)
            ]
            if faces:
                photo = self.request.FILES.get('photo')
                with transaction.atomic():
                    for index, (face, etat_apres) in enumerate(faces):
                        maintenance = Maintenance.objects.create(
                            support=support,
                            face=face,
                            effectue_par=form.instance.effectue_par,
                            date_intervention=form.instance.date_intervention,
                            etat_apres=etat_apres,
                            description=form.instance.description,
                            photo=photo if index == 0 else None,
                        )
                        _log_maintenance_creation(
                            self.request,
                            maintenance,
                            f"Création d'une intervention pour {support.code}, face {face.label}.",
                        )
                messages.success(
                    self.request,
                    f"{len(faces)} face(s) mise(s) à jour pour {support.code}."
                )
                return redirect(self.success_url)
            if form.faces_queryset.exists():
                return self.form_invalid(form)

        response = super().form_valid(form)
        _log_maintenance_creation(
            self.request,
            self.object,
            f"Création d'une intervention pour {self.object.support.code}.",
        )
        messages.success(self.request, f'Maintenance enregistrée pour {self.object.support.code}.')
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Veuillez corriger les erreurs dans le formulaire.")
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Nouvelle maintenance'
        support_id = (
            self.kwargs.get('pk')
            or context['form'].data.get('support')
            or context['form'].initial.get('support')
        )
        if hasattr(support_id, 'pk'):
            support_id = support_id.pk
        context['blocked_support'] = None
        context['blocked_support_repair_id'] = None
        supports = list(
            Support.objects.filter(actif=True).only('pk', 'type_support', 'etat')
        )
        support_ids_with_faces = set(
            FacePanneau.objects.filter(
                support_id__in=[s.pk for s in supports],
            ).values_list('support_id', flat=True)
        )
        if support_id:
            support = Support.objects.filter(pk=support_id).first()
            if (
                support
                and (
                    support.type_support != Support.TYPE_PANNEAU
                    or support.pk not in support_ids_with_faces
                )
                and support.etat in (
                    Support.ETAT_A_MAINTENIR,
                    Support.ETAT_PANNE,
                )
            ):
                context['blocked_support'] = support

        context['support_types_json'] = json.dumps({
            s.pk: s.type_support
            for s in supports
        })
        blocked_support_ids = [
            s.pk for s in supports
            if s.etat in (Support.ETAT_A_MAINTENIR, Support.ETAT_PANNE)
            and (
                s.type_support != Support.TYPE_PANNEAU
                or s.pk not in support_ids_with_faces
            )
        ]
        support_repair_ids = {}
        for repair in Maintenance.objects.filter(
            support_id__in=blocked_support_ids,
            face__isnull=True,
            etat_apres__in=(Support.ETAT_A_MAINTENIR, Support.ETAT_PANNE),
        ).order_by('support_id', '-date_intervention', '-pk'):
            support_repair_ids.setdefault(repair.support_id, repair.pk)
        context['support_states_json'] = json.dumps({
            s.pk: {
                'state': s.etat,
                'repair_id': support_repair_ids.get(s.pk),
            }
            for s in supports
        })
        if context['blocked_support']:
            context['blocked_support_repair_id'] = support_repair_ids.get(
                context['blocked_support'].pk,
            )
        form = context['form']
        context['all_faces_blocked'] = bool(form.faces_queryset.exists()) and all(
            face.etat in (Support.ETAT_A_MAINTENIR, Support.ETAT_PANNE)
            for face in form.faces_queryset
        )
        context['maintenance_states_json'] = json.dumps([
            {'value': state, 'label': label}
            for state, label in MaintenanceForm.ETAT_CHOICES
        ])
        return context


class MaintenanceUpdateView(LoginRequiredMixin, UpdateView):
    model         = Maintenance
    form_class    = MaintenanceForm
    template_name = 'inventory/maintenance_form.html'
    success_url   = reverse_lazy('maintenance_list')

    def get_queryset(self):
        qs = Maintenance.objects.select_related('support', 'effectue_par')
        allowed_ids = _maintenance_management_queryset(self.request.user).values('pk')
        return qs.filter(pk__in=allowed_ids)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        old_maintenance = Maintenance.objects.get(pk=self.object.pk)
        response = super().form_valid(form)
        changes = log_changes(
            old_maintenance,
            self.object,
            [
                'support',
                'face',
                'effectue_par',
                'date_intervention',
                'etat_apres',
                'description',
                'photo',
                'reparation_commencee_le',
                'reparation_commencee_par',
            ],
        )
        action = (
            AuditLog.ACTION_ETAT_CHANGE
            if changes and 'etat_apres' in changes
            else AuditLog.ACTION_UPDATE
        )
        log_action(
            self.request,
            action,
            AuditLog.MODULE_INVENTORY,
            obj=self.object,
            detail=f"Modification de l'intervention de maintenance #{self.object.pk}.",
            changes=changes,
        )
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
        context['maintenance_states_json'] = json.dumps([
            {'value': state, 'label': label}
            for state, label in MaintenanceForm.ETAT_CHOICES
        ])
        return context


class MaintenanceQuickRepairView(LoginRequiredMixin, View):
    """Enregistre automatiquement une réparation pour un support ou une face en panne."""

    def post(self, request, pk):
        queryset = _maintenance_management_queryset(request.user).select_related(
            'support',
            'face',
        )
        maintenance = get_object_or_404(queryset, pk=pk)
        support = maintenance.support
        face = maintenance.face

        etat_actuel = face.etat if face else support.etat
        etat_apres = request.POST.get('etat_apres', Maintenance.ETAT_BON)
        if etat_apres not in {
            Maintenance.ETAT_BON,
            Maintenance.ETAT_A_MAINTENIR,
            Maintenance.ETAT_PANNE,
        }:
            etat_apres = Maintenance.ETAT_BON
        if etat_apres == etat_actuel:
            messages.error(request, "L'état choisi est déjà l'état actuel de cet élément.")
            return redirect('maintenance_detail', pk=maintenance.pk)

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
            etat_apres=etat_apres,
            description=description,
            photo=photo,
        )
        log_action(
            request,
            AuditLog.ACTION_ETAT_CHANGE,
            AuditLog.MODULE_INVENTORY,
            obj=nouvelle,
            detail=(
                f"Avancement enregistré pour l'intervention #{maintenance.pk} "
                f"sur {support.code}."
            ),
            changes={
                'intervention_source': {
                    'avant': '',
                    'apres': str(maintenance.pk),
                },
                'support': {
                    'avant': '',
                    'apres': support.code,
                },
                'face': {
                    'avant': '',
                    'apres': face.label if face else '',
                },
                'effectue_par': {
                    'avant': '',
                    'apres': str(request.user),
                },
                'date_intervention': {
                    'avant': '',
                    'apres': date_intervention.isoformat(),
                },
                'etat_element': {
                    'avant': etat_actuel,
                    'apres': etat_apres,
                },
                'description': {
                    'avant': '',
                    'apres': description,
                },
                'photo': {
                    'avant': '',
                    'apres': nouvelle.photo.name if nouvelle.photo else '',
                },
            },
        )

        label_etat = {
            Maintenance.ETAT_BON: 'opérationnel',
            Maintenance.ETAT_A_MAINTENIR: 'signalé',
            Maintenance.ETAT_PANNE: 'en panne',
        }.get(etat_apres, 'opérationnel')
        messages.success(
            request,
            f"Avancement enregistré avec succès pour {support.code}{f' (Face {face.label})' if face else ''} ! Le statut est maintenant {label_etat}."
        )
        return redirect('maintenance_detail', pk=nouvelle.pk)


class MaintenanceStartRepairView(LoginRequiredMixin, View):
    """Enregistre le démarrage effectif de la réparation d'un incident ouvert."""

    def post(self, request, pk):
        maintenance = get_object_or_404(
            _maintenance_management_queryset(request.user).select_related(
                'support',
                'face',
            ),
            pk=pk,
        )
        incident_root = _get_open_incident_root(maintenance)
        if incident_root is None or incident_root.pk != maintenance.pk:
            messages.error(request, "Cet incident n'est plus ouvert.")
            return redirect('maintenance_detail', pk=maintenance.pk)
        if incident_root.reparation_commencee_le:
            messages.info(request, "La réparation de cet incident a déjà commencé.")
            return redirect('maintenance_detail', pk=maintenance.pk)

        incident_root.reparation_commencee_le = timezone.now()
        incident_root.reparation_commencee_par = request.user
        date_debut = incident_root.reparation_commencee_le
        incident_root.save(update_fields=[
            'reparation_commencee_le',
            'reparation_commencee_par',
        ])
        log_action(
            request,
            AuditLog.ACTION_UPDATE,
            AuditLog.MODULE_INVENTORY,
            obj=incident_root,
            detail=f"Démarrage de la réparation de l'intervention #{incident_root.pk}.",
            changes={
                'reparation_commencee_le': {
                    'avant': '',
                    'apres': date_debut.isoformat(),
                },
                'reparation_commencee_par': {
                    'avant': '',
                    'apres': str(request.user),
                },
            },
        )
        messages.success(
            request,
            f"La réparation de {maintenance.support.code} est maintenant en cours.",
        )
        return redirect('maintenance_detail', pk=maintenance.pk)


class SupportPeriodesVanneView(LoginRequiredMixin, View):
    template_name = 'inventory/support_periodes_panne.html'

    def get(self, request, pk):
        support  = get_object_or_404(Support, pk=pk)
        periodes = support.get_periodes_panne()
        periodes.sort(key=lambda p: p['debut'], reverse=True)
        periodes.sort(key=lambda p: p['etat_incident'] != Support.ETAT_PANNE)
        periodes.sort(key=lambda p: p['resolue'])

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
        page_obj = Paginator(periodes, 20).get_page(request.GET.get('page'))

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
            'periodes'      : page_obj,
            'page_obj'      : page_obj,
            'paginator'     : page_obj.paginator,
            'is_paginated'  : page_obj.has_other_pages(),
            'nb_total'      : nb_total,
            'nb_resolues'   : nb_resolues,
            'nb_en_cours'   : nb_en_cours,
            'duree_totale'  : duree_totale,
            'duree_moyenne' : duree_moyenne,
            'stats_par_face': stats_par_face,
            'title'         : f'Périodes de signalement et de panne — {support.code}',
        }
        return render(request, self.template_name, context)
    
class PeriodesParVueView(LoginRequiredMixin, View):
    template_name = 'inventory/periodes_panne_liste.html'

    def get(self, request):
        filters = {
            'support': request.GET.get('support', ''),
            'face': request.GET.get('face', ''),
            'statut': request.GET.get('statut', ''),
            'date_debut': request.GET.get('date_debut', ''),
            'date_fin': request.GET.get('date_fin', ''),
            'duree_mois': request.GET.get('duree_mois', ''),
        }
        from reports.pannes_report_view import build_panne_periods
        try:
            periodes, _, date_fin = build_panne_periods(filters)
        except ValueError as error:
            messages.error(request, str(error) or 'Les dates du rapport sont invalides.')
            return redirect('periodes_panne_liste')

        if filters['duree_mois']:
            filters['date_fin'] = date_fin.isoformat()

        # ── KPI globaux ───────────────────────────────────────────
        nb_total    = len(periodes)
        nb_resolues = sum(1 for p in periodes if p['resolue'])
        nb_en_cours = nb_total - nb_resolues
        duree_totale = sum(
            (p['duree'] for p in periodes if p['duree']),
            timedelta()
        )
        duree_moyenne = duree_totale / nb_total if nb_total else timedelta()
        page_obj = Paginator(periodes, 20).get_page(request.GET.get('page'))

        # ── Données pour les filtres ──────────────────────────────
        supports_liste = Support.objects.filter(
            maintenances__isnull=False
        ).distinct().order_by('code')
        export_params = request.GET.copy()
        export_params.pop('page', None)
        export_params.pop('format', None)
        if filters['duree_mois']:
            export_params['date_fin'] = filters['date_fin']

        context = {
            'title'         : 'Périodes de signalement et de panne',
            'periodes'      : page_obj,
            'page_obj'      : page_obj,
            'paginator'     : page_obj.paginator,
            'is_paginated'  : page_obj.has_other_pages(),
            'nb_total'      : nb_total,
            'nb_resolues'   : nb_resolues,
            'nb_en_cours'  : nb_en_cours,
            'duree_totale'  : duree_totale,
            'duree_moyenne' : duree_moyenne,
            'supports_liste': supports_liste,
            'report_preview_url': f'{reverse("pannes_report_preview")}?{export_params.urlencode()}',
            'export_pdf_url': f'{reverse("pannes_report_pdf")}?{export_params.urlencode()}',
            'export_excel_url': f'{reverse("pannes_report_excel")}?{export_params.urlencode()}',
            'filters'       : {
                **filters,
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