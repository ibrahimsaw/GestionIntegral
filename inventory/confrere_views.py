from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.utils.http import content_disposition_header
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.db import connections, router, transaction
from django.db.models import Count, Max, Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from campaigns.mixins import StaffRequiredMixin
from campaigns.models import DemandeReservation

from .models import Confrere, DocumentDossierConfrere, DossierConfrere, FormatSupport, SupportConfrere


class CatalogForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            control_class = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs['class'] = f'{control_class} rounded-3'


class ConfrereForm(CatalogForm):
    class Meta:
        model = Confrere
        fields = ('nom', 'nom_contact', 'prenom_contact', 'telephone', 'whatsapp', 'email')
        widgets = {
            'telephone': forms.TextInput(attrs={'type': 'tel'}),
            'whatsapp': forms.TextInput(attrs={'type': 'tel'}),
        }


class FormatConfrereSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            option['attrs']['data-categorie'] = value.instance.categorie
        return option


class SupportConfrereForm(CatalogForm):
    face_codes = ('A', 'B', 'C', 'D')
    categorie = forms.ChoiceField(label='Catégorie')
    faces = forms.MultipleChoiceField(label='Faces', widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = SupportConfrere
        fields = ('nom', 'categorie', 'format', 'ville', 'emplacement', 'faces', 'notes')
        widgets = {
            'format': FormatConfrereSelect,
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected_faces = list(self.instance.codes_faces) if self.instance.pk else ['A']
        self.legacy_faces = [code for code in selected_faces if code not in self.face_codes]
        self.fields['faces'].choices = [(code, f'Face {code}') for code in self.face_codes]
        self.fields['faces'].required = not self.legacy_faces
        self.initial['faces'] = [code for code in selected_faces if code in self.face_codes]
        formats = FormatSupport.objects.exclude(categorie='')
        categories = sorted(set(formats.order_by().values_list('categorie', flat=True)), key=str.casefold)
        current_category = self.instance.categorie_affichee
        default_category = next(
            (category for category in categories if category.replace(' ', '').casefold() in ('12m²', '12m2')),
            categories[0] if categories else '',
        )
        category_field = self.fields['categorie']
        category_field.choices = [(category, category) for category in categories]
        self.initial['categorie'] = current_category if current_category in categories else default_category

        format_field = self.fields['format']
        format_field.required = True
        format_field.empty_label = None
        format_field.queryset = formats
        format_field.label_from_instance = lambda format_support: str(format_support) or format_support.code
        if not self.instance.format_id:
            category_formats = formats.filter(categorie=self.initial['categorie'])
            default_format = category_formats.filter(code='4x3').first() or category_formats.first()
            if default_format:
                self.initial['format'] = default_format.pk

    def clean(self):
        cleaned = super().clean()
        format_support = cleaned.get('format')
        category = cleaned.get('categorie')
        if format_support and category and format_support.categorie != category:
            self.add_error('format', 'Choisissez un format de la catégorie sélectionnée.')
        elif format_support:
            cleaned['categorie'] = ''
        return cleaned

    def clean_faces(self):
        selected = set(self.cleaned_data['faces'])
        return [code for code in self.face_codes if code in selected] + self.legacy_faces


class ConfrereListView(StaffRequiredMixin, ListView):
    template_name = 'inventory/confrere_list.html'
    context_object_name = 'confreres'
    queryset = Confrere.objects.annotate(
        total_supports=Count('supports'),
        total_faces=Sum('supports__nombre_faces', default=0),
    )


class ConfrereDetailView(StaffRequiredMixin, DetailView):
    queryset = Confrere.objects.prefetch_related(
        Prefetch('supports', queryset=SupportConfrere.objects.select_related('format')),
    )
    template_name = 'inventory/confrere_detail.html'
    context_object_name = 'confrere'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['dossiers'] = self.object.dossiers.select_related('demande').order_by('-demande__created_at')
        return context


class ModeConfirmationConfrereView(StaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        mode = request.POST.get('mode')
        if mode not in ('automatique', 'lien'):
            return HttpResponse(status=400)

        confrere = get_object_or_404(Confrere.objects.select_for_update(), pk=pk)
        automatique = mode == 'automatique'
        confrere.confirmation_automatique = automatique
        confrere.save(update_fields=['confirmation_automatique'])
        demandes_ids = confrere.dossiers.filter(actif=True).values('demande_id')
        demandes = {
            demande.pk: demande
            for demande in DemandeReservation.objects.select_for_update()
            .filter(pk__in=demandes_ids).order_by('pk')
        }
        dossiers = confrere.dossiers.select_for_update().filter(
            demande_id__in=demandes, actif=True,
        )
        for dossier in dossiers:
            dossier.demande = demandes[dossier.demande_id]
            if dossier.mode_confirmation_modifiable:
                dossier.appliquer_mode_confirmation(automatique)

        messages.success(request, 'Mode de confirmation du confrère enregistré.')
        return redirect('confrere_detail', pk=pk)


class DocumentConfrereForm(forms.Form):
    document = forms.FileField(label='Document', widget=forms.ClearableFileInput(attrs={'class': 'form-control'}))

    def clean_document(self):
        document = self.cleaned_data['document']
        if document.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Le document doit faire au maximum 10 Mo.')
        return document


class DossierConfrereView(StaffRequiredMixin, View):
    def get_dossier(self, pk):
        return get_object_or_404(DossierConfrere.objects.select_related('confrere', 'demande'), pk=pk)

    def render_dossier(self, request, dossier, form):
        return render(request, 'inventory/confrere_dossier.html', {
            'dossier': dossier,
            'form': form,
            'documents': dossier.documents.defer('contenu').order_by('-ajoute_le'),
            'lien_validation': request.build_absolute_uri(reverse('confrere_validation', args=[dossier.token_validation])),
        })

    def get(self, request, pk):
        return self.render_dossier(request, self.get_dossier(pk), DocumentConfrereForm())

    def post(self, request, pk):
        dossier = self.get_dossier(pk)
        form = DocumentConfrereForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_dossier(request, dossier, form)
        document = form.cleaned_data['document']
        dossier.documents.create(nom=document.name, taille=document.size, contenu=document.read())
        messages.success(request, 'Document ajouté au dossier.')
        return redirect('confrere_dossier', pk=dossier.pk)


class DocumentDossierConfrereView(StaffRequiredMixin, View):
    def get(self, request, pk, document_pk):
        document = get_object_or_404(DocumentDossierConfrere, dossier_id=pk, pk=document_pk)
        response = HttpResponse(bytes(document.contenu), content_type='application/octet-stream')
        response.headers['Content-Disposition'] = content_disposition_header(True, document.nom)
        response.headers['Cache-Control'] = 'private, no-store'
        return response


class ConfirmationAutomatiqueConfrereView(StaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        mode = request.POST.get('mode')
        if mode not in ('automatique', 'lien'):
            return HttpResponse(status=400)

        demande_id = get_object_or_404(DossierConfrere.objects.only('demande_id'), pk=pk).demande_id
        demande = get_object_or_404(DemandeReservation.objects.select_for_update(), pk=demande_id)
        dossier = get_object_or_404(DossierConfrere.objects.select_for_update(), pk=pk, demande=demande)
        dossier.demande = demande
        if not dossier.mode_confirmation_modifiable:
            messages.error(request, 'Le mode de confirmation ne peut plus être modifié pour ce dossier.')
            return redirect('confrere_dossier', pk=pk)

        dossier.appliquer_mode_confirmation(mode == 'automatique')
        messages.success(request, 'Mode de confirmation enregistré.')
        return redirect('confrere_dossier', pk=pk)


class BonCommandeConfrereView(StaffRequiredMixin, View):
    def get(self, request, pk):
        dossier = get_object_or_404(DossierConfrere.objects.select_related('confrere', 'demande'), pk=pk, actif=True)
        return render(request, 'inventory/confrere_bon_commande.html', {'dossier': dossier})


@method_decorator(never_cache, name='dispatch')
class ValidationConfrereView(View):
    def get_dossier(self, token):
        return get_object_or_404(
            DossierConfrere.objects.select_related('confrere', 'demande'),
            token_validation=token, actif=True,
        )

    def get(self, request, token):
        response = render(request, 'inventory/confrere_validation.html', {'dossier': self.get_dossier(token)})
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    def post(self, request, token):
        dossier = self.get_dossier(token)
        decision = request.POST.get('decision')
        if decision in (DossierConfrere.Reponse.CONFIRMEE, DossierConfrere.Reponse.REFUSEE):
            DossierConfrere.objects.filter(
                pk=dossier.pk, actif=True, token_validation=token,
                confirmation_automatique=False,
                reponse_confrere=DossierConfrere.Reponse.EN_ATTENTE,
            ).update(reponse_confrere=decision, repondu_le=timezone.now())
        return redirect('confrere_validation', token=token)


class ConfrereFormMixin(StaffRequiredMixin):
    model = Confrere
    form_class = ConfrereForm
    template_name = 'inventory/confrere_form.html'
    back_url = reverse_lazy('confrere_list')

    def get_success_url(self):
        return reverse('confrere_detail', args=[self.object.pk])

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Le confrère a été enregistré.')
        return response


class ConfrereCreateView(ConfrereFormMixin, CreateView):
    page_title = 'Ajouter un confrère'


class ConfrereUpdateView(ConfrereFormMixin, UpdateView):
    page_title = 'Modifier le confrère'

    @property
    def back_url(self):
        return self.get_success_url()


class SupportConfrereFormMixin(StaffRequiredMixin):
    model = SupportConfrere
    form_class = SupportConfrereForm
    template_name = 'inventory/confrere_form.html'
    show_support_reference = True

    def get_confrere(self):
        if not hasattr(self, 'confrere'):
            self.confrere = get_object_or_404(Confrere, pk=self.kwargs['confrere_pk'])
        return self.confrere

    def get_queryset(self):
        return super().get_queryset().filter(confrere=self.get_confrere())

    def get_context_data(self, **kwargs):
        confrere = self.get_confrere()
        context = super().get_context_data(confrere=confrere, **kwargs)
        if not self.object:
            database = router.db_for_write(SupportConfrere, instance=context['form'].instance)
            connection = connections[database]
            if connection.vendor == 'sqlite':
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT seq FROM sqlite_sequence WHERE name = %s',
                        [SupportConfrere._meta.db_table],
                    )
                    sequence = cursor.fetchone()
                dernier_numero = sequence[0] if sequence else 0
            else:
                dernier_numero = SupportConfrere.objects.using(database).aggregate(Max('pk'))['pk__max'] or 0
            context['reference_apercu'] = SupportConfrere.construire_reference(
                confrere.nom, '', dernier_numero + 1,
            )
        return context

    def get_success_url(self):
        return reverse('confrere_detail', args=[self.get_confrere().pk])

    @property
    def back_url(self):
        return self.get_success_url()

    def form_valid(self, form):
        form.instance.confrere = self.get_confrere()
        form.instance.faces_selectionnees = form.cleaned_data['faces']
        form.instance.nombre_faces = len(form.instance.faces_selectionnees)
        if form.instance.format_id:
            form.instance.format_libre = ''
        response = super().form_valid(form)
        messages.success(self.request, 'Le support a été enregistré.')
        return response


class SupportConfrereCreateView(SupportConfrereFormMixin, CreateView):
    page_title = 'Ajouter un support'

    def get_initial(self):
        return {**super().get_initial(), 'ville': self.get_confrere().ville}


class SupportConfrereUpdateView(SupportConfrereFormMixin, UpdateView):
    page_title = 'Modifier le support'
