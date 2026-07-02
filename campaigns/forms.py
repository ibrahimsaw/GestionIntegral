from django import forms
from .models import Client, Campagne, Contrat, LigneCampagne, CampagneVisuel
from inventory.models import Support, FacePanneau, FormatSupport

W = {'class': 'form-control'}
S = {'class': 'form-select'}
D = {'class': 'form-control', 'type': 'date'}


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['nom', 'contact_nom', 'telephone', 'email', 'adresse', 'logo', 'notes', 'actif']
        widgets = {
            'nom': forms.TextInput(attrs=W),
            'contact_nom': forms.TextInput(attrs=W),
            'telephone': forms.TextInput(attrs=W),
            'email': forms.EmailInput(attrs=W),
            'adresse': forms.Textarea(attrs={**W, 'rows': 2}),
            'logo': forms.FileInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={**W, 'rows': 3}),
            'actif': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ContratForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Si client est déjà défini (édition ou création pour client spécifique), masquer le champ
        if self.instance and self.instance.pk and self.instance.client:
            self.fields['client'].widget = forms.HiddenInput()
        elif self.initial.get('client'):
            self.fields['client'].widget = forms.HiddenInput()

    class Meta:
        model = Contrat
        fields = ['client', 'nom', 'type_contrat', 'date_debut', 'date_fin', 'nb_spots', 'actif', 'notes']
        widgets = {
            'client': forms.Select(attrs=S),
            'nom': forms.TextInput(attrs=W),
            'type_contrat': forms.Select(attrs=S),
            'date_debut': forms.DateInput(attrs=D),
            'date_fin': forms.DateInput(attrs=D),
            'nb_spots': forms.NumberInput(attrs={**W, 'min': 0}),
            'notes': forms.Textarea(attrs={**W, 'rows': 3}),
            'actif': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    """FileField qui accepte plusieurs fichiers."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        # Si aucun fichier soumis et champ non requis → OK
        if not data:
            return []
        # Si un seul fichier, on le met dans une liste
        if not isinstance(data, (list, tuple)):
            data = [data]
        return [super(MultipleFileField, self).clean(d, initial) for d in data]


class CampagneForm(forms.ModelForm):
    # Champ de choix dynamique pour le type / format de campagne
    type_support = forms.ChoiceField(
        required=False,
        choices=[],
        widget=forms.Select(attrs=S),
        label="Type / format"
    )

    # Ajout du champ virtuel pour la sélection multiple
    visuels_multiples = MultipleFileField(
        widget=MultipleFileInput(attrs={
            'multiple': True, 
            'class': 'form-control',
            'accept': 'image/*,video/*'
        }),
        label="Visuels / Affiches (Sélectionnez plusieurs fichiers)",
        required=False,
        help_text="Vous pouvez sélectionner plusieurs photos ou vidéos à la fois."
    )

    class Meta:
        model = Campagne
        fields = [
            'client', 'nom', 'prix', 'campagne_parente', 'est_mere', 'date_debut', 'date_fin', 'statut',
            'type_support', 'duree_passage', 'frequence',
            'tranches_horaires', 'notes', 'contrat'
        ]
        widgets = {
            'client': forms.Select(attrs=S),
            'nom': forms.TextInput(attrs=W),
            'prix': forms.NumberInput(attrs={**W, 'step': '0.01', 'min': '0'}),
            'campagne_parente': forms.Select(attrs=S),
            'est_mere': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'date_debut': forms.DateInput(attrs=D),
            'date_fin': forms.DateInput(attrs=D),
            'statut': forms.Select(attrs=S),
            'type_support': forms.Select(attrs=S),
            'duree_passage': forms.Select(attrs=S),
            'frequence': forms.Select(attrs=S),
            'tranches_horaires': forms.TextInput(attrs=W),
            'notes': forms.Textarea(attrs={**W, 'rows': 3}),
            'contrat': forms.Select(attrs=S),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. Filtrage dynamique du champ 'contrat'
        client_id = self.data.get('client') or (self.instance.client_id if self.instance.pk else None)
        
        if client_id:
            qs = Contrat.objects.filter(client_id=client_id, actif=True)
            self.fields['contrat'].queryset = qs
            self.fields['campagne_parente'].queryset = Campagne.objects.filter(
                client_id=client_id,
                est_mere=True,
            ).exclude(pk=self.instance.pk if self.instance.pk else None)
        else:
            self.fields['contrat'].queryset = Contrat.objects.none()
            self.fields['campagne_parente'].queryset = Campagne.objects.none()

        support_choices = [('', '--- Sélectionner un type / format ---')] + list(Campagne.TYPE_SUPPORT_CHOICES)
        self.fields['type_support'].choices = support_choices
    
    def clean_visuels_multiples(self):
        return self.cleaned_data.get('visuels_multiples', [])
    def clean(self):
        cleaned = super().clean()
        d1 = cleaned.get('date_debut')
        d2 = cleaned.get('date_fin')
        type_support = cleaned.get('type_support')
        contrat = cleaned.get('contrat')

        # 1. Validation des dates de campagne
        if d1 and d2 and d1 > d2:
            raise forms.ValidationError("La date de fin doit être après la date de début.")

        # 1.5 Validation de la hiérarchie
        if cleaned.get('campagne_parente') and cleaned.get('campagne_parente').client_id != cleaned.get('client').id:
            raise forms.ValidationError("La campagne mère doit appartenir au même client.")

        if cleaned.get('est_mere') and cleaned.get('campagne_parente'):
            raise forms.ValidationError("Une campagne mère ne peut pas être rattachée à une autre campagne mère.")

        if cleaned.get('campagne_parente') and not cleaned.get('campagne_parente').est_mere:
            raise forms.ValidationError("La campagne sélectionnée comme campagne mère doit être marquée comme campagne mère.")

        if cleaned.get('campagne_parente') and not cleaned.get('type_support'):
            parent = cleaned.get('campagne_parente')
            if parent and parent.type_support:
                cleaned['type_support'] = parent.type_support
            else:
                raise forms.ValidationError(
                    "Le type de support est obligatoire pour une sous-campagne lorsque la campagne mère n'a pas de type défini."
                )

        # 2. Validation spécifique au support écran (en utilisant la valeur brute 'ecran')
        if type_support == 'ecran':
            # Vérifier que les dates sont dans la période du contrat si un contrat est lié
            if d1 and d2 and contrat:
                if d1 < contrat.date_debut or d2 > contrat.date_fin:
                    raise forms.ValidationError(
                        f"Les dates de la campagne doivent être comprises dans la période du contrat "
                        f"({contrat.date_debut.strftime('%d/%m/%Y')} au {contrat.date_fin.strftime('%d/%m/%Y')})."
                    )
        
        return cleaned





class LigneCampagneForm(forms.ModelForm):
    TYPE_SUPPORT_CHOICES = [
        ('', '--- Sélectionner un type ---'),
        (Support.TYPE_PANNEAU, 'Panneau Statique'),
        (Support.TYPE_ECRAN, 'Écran Numérique'),
    ]

    type_support = forms.ChoiceField(
        choices=TYPE_SUPPORT_CHOICES,
        widget=forms.Select(attrs=S),
        required=True,
        label="Type d'affichage"
    )

    class Meta:
        model = LigneCampagne
        fields = [
            'type_support', 'support', 'face',
            'ordre_dans_boucle', 'visuel', 'notes',
            # ✅ NOUVEAU — champs spécifiques par écran
            'date_debut', 'date_fin',
            'duree_passage', 'frequence', 'tranches_horaires',
        ]
        widgets = {
            'support':           forms.Select(attrs={**S, 'id': 'id_support'}),
            'face':              forms.Select(attrs={**S, 'id': 'id_face'}),
            'ordre_dans_boucle': forms.NumberInput(attrs={**W, 'min': 0}),
            'visuel':            forms.FileInput(attrs={'class': 'form-control'}),
            'notes':             forms.Textarea(attrs={**W, 'rows': 2}),
            # ✅ NOUVEAU
            'date_debut':        forms.DateInput(attrs={**W, 'type': 'date'}),
            'date_fin':          forms.DateInput(attrs={**W, 'type': 'date'}),
            'duree_passage':     forms.Select(attrs=S),
            'frequence':         forms.Select(attrs=S),
            'tranches_horaires': forms.TextInput(attrs={**W, 'placeholder': 'Ex: 08:00-12:00, 14:00-18:00'}),
        }

    def __init__(self, *args, campagne=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.campagne = campagne

        # Filtrage initial des supports en bon état
        supports_qs = Support.objects.filter(etat='bon').order_by('code')

        # Gestion du type sélectionné (via POST ou instance existante)
        selected_type = self.data.get('type_support')
        if not selected_type and self.instance.pk:
            selected_type = self.instance.support.type_support

        if selected_type:
            self.fields['support'].queryset = supports_qs.filter(type_support=selected_type)
        else:
            self.fields['support'].queryset = supports_qs

        # Gestion dynamique des faces pour les panneaux
        self.fields['face'].queryset = FacePanneau.objects.none()
        support_id = self.data.get('support') or (self.instance.support_id if self.instance.pk else None)
        if support_id:
            try:
                self.fields['face'].queryset = FacePanneau.objects.filter(support_id=support_id)
            except (ValueError, TypeError):
                pass

        # ✅ Les champs écran sont optionnels (ils héritent de la campagne si vides)
        for field in ['date_debut', 'date_fin', 'duree_passage', 'frequence', 'tranches_horaires']:
            self.fields[field].required = False

        # ✅ Afficher les valeurs par défaut de la campagne en placeholder/help_text
        if campagne:
            self.fields['date_debut'].widget.attrs['placeholder'] = str(campagne.date_debut)
            self.fields['date_fin'].widget.attrs['placeholder'] = str(campagne.date_fin)
            self.fields['tranches_horaires'].widget.attrs['placeholder'] = (
                f"Par défaut : {campagne.tranches_horaires or 'non défini'}"
            )
            self.fields['duree_passage'].help_text = (
                f"Laisser vide pour hériter de la campagne ({campagne.get_duree_passage_display() if campagne.duree_passage else 'non défini'})"
            )
            self.fields['frequence'].help_text = (
                f"Laisser vide pour hériter de la campagne ({campagne.get_frequence_display() if campagne.frequence else 'non défini'})"
            )

        # ✅ Masquer les champs écran si type panneau sélectionné (via CSS/JS côté template)
        if selected_type == Support.TYPE_PANNEAU:
            for field in ['duree_passage', 'frequence', 'tranches_horaires', 'date_debut', 'date_fin']:
                self.fields[field].widget.attrs['class'] = self.fields[field].widget.attrs.get('class', '') + ' d-none'

    def clean(self):
        cleaned = super().clean()
        type_support    = cleaned.get('type_support')
        support         = cleaned.get('support')
        face            = cleaned.get('face')
        date_debut      = cleaned.get('date_debut')
        date_fin        = cleaned.get('date_fin')
        duree           = cleaned.get('duree_passage') or (self.campagne.duree_passage if self.campagne else None)
        frequence       = cleaned.get('frequence') or (self.campagne.frequence if self.campagne else None)
        tranches        = cleaned.get('tranches_horaires') or (self.campagne.tranches_horaires if self.campagne else None)

        # Dates effectives (ligne ou campagne)
        date_debut_eff  = date_debut or (self.campagne.date_debut if self.campagne else None)
        date_fin_eff    = date_fin or (self.campagne.date_fin if self.campagne else None)

        # 1. Cohérence Type vs Support
        if support and type_support and support.type_support != type_support:
            raise forms.ValidationError("Le support sélectionné ne correspond pas au type choisi.")

        # 2. Validation des dates si fournies sur la ligne
        if date_debut and date_fin:
            if date_debut >= date_fin:
                raise forms.ValidationError("La date de début doit être antérieure à la date de fin.")
            # Vérifier que les dates de la ligne sont dans la période de la campagne
            if self.campagne:
                if date_debut < self.campagne.date_debut:
                    self.add_error('date_debut', "La date ne peut pas être avant le début de la campagne.")
                if date_fin > self.campagne.date_fin:
                    self.add_error('date_fin', "La date ne peut pas dépasser la fin de la campagne.")

        # 3. Validation Panneau
        if support and support.type_support == Support.TYPE_PANNEAU:
            if not face:
                self.add_error('face', "Veuillez choisir une face pour ce panneau.")

        # 4. Validation Écran
        if support and support.type_support == Support.TYPE_ECRAN:
            if not duree or not frequence:
                raise forms.ValidationError(
                    "Durée et fréquence sont obligatoires pour un écran "
                    "(définissez-les sur la ligne ou sur la campagne)."
                )
            if not tranches:
                raise forms.ValidationError(
                    "Les tranches horaires sont obligatoires pour un écran."
                )
            if not date_debut_eff or not date_fin_eff:
                raise forms.ValidationError(
                    "Les dates de diffusion sont obligatoires pour un écran."
                )

            # Disponibilité réelle sur l'écran
            if hasattr(support, 'ecran_info'):
                ecran = support.ecran_info
                is_ok, message = ecran.peut_accueillir_spot(
                    duree_sec=duree,
                    frequence_min=(frequence / 60),
                    date_debut=date_debut_eff,
                    date_fin=date_fin_eff
                )
                if not is_ok:
                    raise forms.ValidationError(f"Disponibilité insuffisante : {message}")

        return cleaned
