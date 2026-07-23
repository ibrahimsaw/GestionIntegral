from django import forms
from .models import *

W = {'class': 'form-control'}
S = {'class': 'form-select'}
CODE_STYLE = {
    'style': "background-color: #dbeafe; color: var(--color-primary); border-left: 3px solid var(--color-primary); border-width:3px solid var(--color-primary);"
}


class SupportForm(forms.ModelForm):
    nb_faces = forms.IntegerField(
        min_value=1, max_value=10, initial=2,
        required=False,
        label="Nombre de faces",
        widget=forms.NumberInput(attrs={**W, 'min': 1, 'max': 10}),
        help_text="Nombre de faces du panneau (généralement 2).",
    )

    class Meta:
        model  = Support
        fields = [
            'code','code_ext','nom', 'type_support',
            # ← format ici, affiché uniquement pour les panneaux via JS
            'format',
            'latitude', 'longitude',
            'adresse', 'ville', 'quartier',
            'etat', 'photo_principale', 'notes',
        ]
        widgets = {
            'code': forms.TextInput(attrs={
                **W, **CODE_STYLE,
                'placeholder': 'Généré automatiquement',
                'readonly': True,
                'data-isauto': 'true',
            }),
            'code_ext': forms.TextInput(attrs={
                **W, 
                'placeholder': 'XXXXX',
            }),
            'nom':          forms.TextInput(attrs=W),
            'type_support': forms.Select(attrs={**S, 'id': 'id_type_support'}),
            # format — affiché/masqué en JS selon type_support
            'format':       forms.Select(attrs={
                **S,
                'id': 'id_format',
                'data-panneau-only': 'true',  # utilisé par le JS pour show/hide
            }),
            'latitude':  forms.NumberInput(attrs={**W, 'step': '0.0000001', 'id': 'id_latitude'}),
            'longitude': forms.NumberInput(attrs={**W, 'step': '0.0000001', 'id': 'id_longitude'}),
            'adresse':   forms.TextInput(attrs=W),
            'ville':     forms.TextInput(attrs=W),
            'quartier':  forms.TextInput(attrs=W),
            'etat':      forms.Select(attrs=S),
            'photo_principale': forms.FileInput(attrs={'class': 'form-control'}),
            'notes':     forms.Textarea(attrs={**W, 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ── Cas ÉDITION ──────────────────────────────────────────────────
        if self.instance and self.instance.pk:
            # Le type ne peut plus changer
            self.fields['type_support'].disabled = True
            self.fields['type_support'].help_text = (
                "Le type ne peut plus être modifié après création."
            )

            if self.instance.type_support == Support.TYPE_ECRAN:
                # Écran : on retire format et nb_faces, inutiles
                del self.fields['nb_faces']
                del self.fields['format']
            else:
                # Panneau : nb_faces initialisé au nombre de faces existantes
                self.fields['nb_faces'].initial = self.instance.faces.count()
                self.fields['format'].required  = True

        # ── Cas CRÉATION ─────────────────────────────────────────────────
        else:
            self.fields['nb_faces'].initial = 2
            # format non obligatoire à la création (validé dans clean())
            self.fields['format'].required = False

    # ── Validation ───────────────────────────────────────────────────────
    def clean(self):
        cleaned = super().clean()
        type_support = cleaned.get('type_support')
        fmt          = cleaned.get('format')
        nb_faces     = cleaned.get('nb_faces')

        if type_support == Support.TYPE_PANNEAU:
            if not fmt:
                self.add_error('format', "Le format est obligatoire pour un panneau.")
            if not nb_faces:
                self.add_error('nb_faces', "Le nombre de faces est obligatoire pour un panneau.")

        if type_support == Support.TYPE_ECRAN and fmt:
            # On efface silencieusement le format si l'utilisateur l'a renseigné
            # par erreur pour un écran
            cleaned['format'] = ''

        return cleaned

    def clean_nb_faces(self):
        nb_faces     = self.cleaned_data.get('nb_faces')
        type_support = self.cleaned_data.get('type_support')
        if type_support == Support.TYPE_PANNEAU and not nb_faces:
            raise forms.ValidationError("Nombre de faces requis pour les panneaux.")
        return nb_faces


class FacePanneauForm(forms.ModelForm):
    """
    Formulaire d'une face de panneau.
    Le format est porté par le Support — on ne le répète pas ici.
    On gère uniquement l'éclairage et les notes propres à chaque face.
    """

    class Meta:
        model  = FacePanneau
        fields = ['label', 'eclairage', 'notes','photo']
        widgets = {
            'label':    forms.Select(attrs=S),
            'eclairage': forms.Select(attrs=S),
            'notes':    forms.Textarea(attrs={**W, 'rows': 2}),
            'photo':   forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Si la face est déjà créée, le label A/B ne peut plus changer
        if self.instance and self.instance.pk:
            self.fields['label'].disabled   = True
            self.fields['label'].help_text  = "Le label ne peut plus être modifié."

class EcranNumeriqueForm(forms.ModelForm):
    class Meta:
        model = EcranNumerique
        # Utilisation des nouveaux noms de champs plus explicites
        fields = ['type_ecran', 'resolution', 'cellule', 'heure_allumage', 'heure_extinction']
        
        widgets = {
            'type_ecran': forms.Select(attrs=S),
            'resolution': forms.Select(attrs=S),
            'cellule': forms.Select(attrs=S),
            # Utilisation de widgets de type TimeInput pour une saisie propre
            'heure_allumage': forms.TimeInput(attrs={**W, 'type': 'time'}),
            'heure_extinction': forms.TimeInput(attrs={**W, 'type': 'time'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        h_allumage = cleaned_data.get("heure_allumage")
        h_extinction = cleaned_data.get("heure_extinction")

        if h_allumage and h_extinction:
            if h_allumage >= h_extinction:
                raise forms.ValidationError(
                    "L'heure d'allumage doit être antérieure à l'heure d'extinction."
                )
        return cleaned_data

from collections import defaultdict
from django import forms
from django.contrib.auth import get_user_model
from inventory.models import Maintenance, Support, FacePanneau


class MaintenanceForm(forms.ModelForm):
    appliquer_tout_le_support = forms.BooleanField(
        required=False,
        label="Appliquer cet état à tout le support (toutes les faces)",
        help_text="Si coché, l'état sera appliqué à toutes les faces du panneau, "
                   "sans avoir besoin de sélectionner une face précise.",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_appliquer_tout'}),
    )

    class Meta:
        model  = Maintenance
        fields = ['support', 'face', 'effectue_par', 'date_intervention', 'etat_apres', 'description', 'photo']
        widgets = {
            'support'           : forms.Select(attrs={'class': 'form-select', 'id': 'id_support'}),
            'effectue_par'      : forms.Select(attrs={'class': 'form-select'}),
            'date_intervention' : forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M'
            ),
            'face'        : forms.Select(attrs={'class': 'form-select', 'id': 'id_face'}),
            'etat_apres'  : forms.RadioSelect(),
            'description' : forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'photo'       : forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'face'              : 'Face (panneau)',
            'support'           : 'Support',
            'effectue_par'      : 'Technicien',
            'date_intervention' : "Date d'intervention",
            'etat_apres'        : 'État enregistré',
            'description'       : 'Description',
            'photo'             : 'Photo',
        }

    def __init__(self, *args, **kwargs):
        user       = kwargs.pop('user', None)
        support_pk = kwargs.pop('support_pk', None)
        super().__init__(*args, **kwargs)

        # ── État enregistré : uniquement Bon / Panne ──────────────────────
        self.fields['etat_apres'].choices = [
            (Maintenance.ETAT_BON, 'Bon état'),
            (Maintenance.ETAT_PANNE, 'En panne'),
        ]
        self.fields['etat_apres'].initial = Maintenance.ETAT_BON

        if self.instance.pk and self.instance.date_intervention:
            self.initial['date_intervention'] = self.instance.date_intervention.strftime('%Y-%m-%dT%H:%M')

        # ── Support : queryset + regroupement par ville/quartier ──────────
        supports_qs = Support.objects.filter(actif=True).order_by('ville', 'quartier', 'code')
        self.fields['support'].queryset = supports_qs

        grouped = defaultdict(list)
        for s in supports_qs:
            ville = s.ville or 'Non renseignée'
            quartier_txt = f" — {s.quartier}" if s.quartier else ""
            group_label = f"{ville}{quartier_txt}"
            grouped[group_label].append((s.pk, f"{s.code} — {s.nom}"))

        self.fields['support'].choices = [('', '---------')] + [
            (group_label, options) for group_label, options in grouped.items()
        ]

        self.fields['effectue_par'].queryset = get_user_model().objects.filter(
            role__in=['technicien', 'admin']
        ).order_by('first_name', 'last_name')

        active_support_id = None
        if support_pk:
            active_support_id = support_pk
        elif self.data.get('support'):
            active_support_id = self.data.get('support')
        elif self.instance.pk and self.instance.support_id:
            active_support_id = self.instance.support_id

        if active_support_id:
            self.fields['face'].queryset = FacePanneau.objects.filter(
                support_id=active_support_id
            ).order_by('label')
        else:
            self.fields['face'].queryset = FacePanneau.objects.none()

        self.fields['face'].required = False

        if support_pk:
            self.initial['support']                         = support_pk
            self.fields['support'].initial                  = support_pk
            self.fields['support'].widget.attrs['class']    = 'form-select bg-light text-muted'
            self.fields['support'].widget.attrs['disabled'] = 'disabled'
            self.fields['support'].required                 = False
            self.instance.support_id                        = support_pk

        if user:
            self.initial['effectue_par']                         = user
            self.fields['effectue_par'].initial                  = user
            self.fields['effectue_par'].widget.attrs['class']    = 'form-select bg-light text-muted'
            self.fields['effectue_par'].widget.attrs['disabled'] = 'disabled'
            self.fields['effectue_par'].required                 = False

        # ── Sur une mise à jour, la case "appliquer à tout" n'a pas de sens
        #    (on modifie une maintenance existante liée à une face précise) ──
        if self.instance.pk:
            del self.fields['appliquer_tout_le_support']

    def clean(self):
        cleaned_data   = super().clean()
        face           = cleaned_data.get('face')
        support        = cleaned_data.get('support')
        appliquer_tout = cleaned_data.get('appliquer_tout_le_support', False)

        # Le support peut être désactivé dans le widget (disabled) → il faut le retrouver manuellement
        if not support:
            support_id = self.data.get('support') or self.instance.support_id
            if not support_id and face:
                support_id = face.support_id
            if support_id:
                support = Support.objects.filter(pk=support_id).first()
                if support:
                    cleaned_data['support'] = support
                    self.instance.support_id = support.pk

        if not appliquer_tout:
            if support and support.type_support == Support.TYPE_PANNEAU and not face:
                self.add_error('face', "Sélectionnez une face, ou cochez « Appliquer à tout le support ».")

        if face and support:
            if face.support_id != support.pk:
                self.add_error('face', "La face doit appartenir au support sélectionné.")

        return cleaned_data
            
            