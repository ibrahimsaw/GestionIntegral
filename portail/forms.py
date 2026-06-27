# portail/forms.py
import json
from django import forms
from django.utils import timezone
from inventory.models import FacePanneau, Support


# ── Étape 1 — Sélection des emplacements ─────────────────────────────────────

class Etape1Form(forms.Form):
    """
    Stocke les UUIDs des faces (panneaux) et supports (écrans) sélectionnés
    via la carte Leaflet interactive.
    Champ caché alimenté par le JS.
    """
    faces_selectionnees = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
        help_text="JSON list of face UUIDs",
    )
    supports_selectionnes = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
        help_text="JSON list of support UUIDs (écrans)",
    )

    def clean(self):
        cleaned = super().clean()
        faces_json    = cleaned.get('faces_selectionnees', '[]')
        supports_json = cleaned.get('supports_selectionnes', '[]')

        try:
            faces_ids    = json.loads(faces_json)    if faces_json    else []
            supports_ids = json.loads(supports_json) if supports_json else []
            # Convertir en int, ignorer les valeurs invalides
            faces_ids    = [int(x) for x in faces_ids    if str(x).strip().isdigit()]
            supports_ids = [int(x) for x in supports_ids if str(x).strip().isdigit()]
        except (json.JSONDecodeError, ValueError):
            raise forms.ValidationError("Sélection invalide. Veuillez réessayer.")

        if not faces_ids and not supports_ids:
            raise forms.ValidationError(
                "Veuillez sélectionner au moins un emplacement sur la carte."
            )

        # Recherche par pk au lieu de uuid
        faces = list(
            FacePanneau.objects.filter(pk__in=faces_ids)
            .select_related('support')
        )
        supports = list(
            Support.objects.filter(pk__in=supports_ids, type_support='ecran', actif=True)
        )

        cleaned['faces_objects']    = faces
        cleaned['supports_objects'] = supports
        # On garde des strings pour la session
        cleaned['faces_uuids']      = [str(f.pk) for f in faces]
        cleaned['supports_uuids']   = [str(s.pk) for s in supports]
        return cleaned

# ── Étape 2 — Période et projet ───────────────────────────────────────────────

class Etape2Form(forms.Form):
    date_debut = forms.DateField(
        label="Date de début",
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'min': str(timezone.now().date()),
        }),
    )
    date_fin = forms.DateField(
        label="Date de fin",
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
        }),
    )
    nom_campagne = forms.CharField(
        label="Nom de la campagne / projet",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ex : Lancement produit Juin 2026',
        }),
    )
    message = forms.CharField(
        label="Message complémentaire",
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Précisez votre projet, vos attentes…',
        }),
    )

    def clean(self):
        cleaned = super().clean()
        debut = cleaned.get('date_debut')
        fin   = cleaned.get('date_fin')

        if debut and fin:
            if fin <= debut:
                raise forms.ValidationError(
                    "La date de fin doit être postérieure à la date de début."
                )
            if debut < timezone.now().date():
                raise forms.ValidationError(
                    "La date de début ne peut pas être dans le passé."
                )
            if (fin - debut).days > 365:
                raise forms.ValidationError(
                    "La durée de réservation ne peut pas dépasser 1 an."
                )
        return cleaned


# ── Étape 3 — Coordonnées visiteur ───────────────────────────────────────────

class Etape3Form(forms.Form):
    nom_contact = forms.CharField(
        label="Nom complet *",
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Jean Dupond',
            'autocomplete': 'name',
        }),
    )
    societe = forms.CharField(
        label="Société / Organisation",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'ABC Corp (optionnel)',
            'autocomplete': 'organization',
        }),
    )
    email = forms.EmailField(
        label="Email *",
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'vous@exemple.com',
            'autocomplete': 'email',
        }),
    )
    telephone = forms.CharField(
        label="Téléphone *",
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+226 70 00 00 00',
            'autocomplete': 'tel',
        }),
    )
    accepte_contact = forms.BooleanField(
        label="J'accepte d'être recontacté par l'équipe commerciale de la Régie INTEGRAL",
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_telephone(self):
        tel = self.cleaned_data.get('telephone', '').strip()
        # Nettoyage basique : on garde chiffres, +, espaces, tirets
        cleaned = ''.join(c for c in tel if c.isdigit() or c in '+- ')
        if len(cleaned.replace(' ', '').replace('-', '').replace('+', '')) < 8:
            raise forms.ValidationError("Veuillez saisir un numéro de téléphone valide.")
        return tel

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        return email


# ── Formulaire de contact ─────────────────────────────────────────────────────

class ContactForm(forms.Form):
    nom = forms.CharField(
        label="Nom *",
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Votre nom',
        }),
    )
    email = forms.EmailField(
        label="Email *",
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'vous@exemple.com',
        }),
    )
    telephone = forms.CharField(
        label="Téléphone",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+226 70 00 00 00',
        }),
    )
    objet = forms.CharField(
        label="Objet *",
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Demande de renseignements…',
        }),
    )
    message = forms.CharField(
        label="Message *",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 6,
            'placeholder': 'Votre message…',
        }),
    )

    def clean_email(self):
        return self.cleaned_data.get('email', '').strip().lower()
