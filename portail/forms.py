# portail/forms.py
import json
from django import forms
from django.utils import timezone
from inventory.models import FacePanneau, Support


import json
from datetime import date

from django import forms
from django.utils import timezone

from inventory.models import FacePanneau, Support


class Etape1Form(forms.Form):
    """
    Étape 1 — Sélection des emplacements sur la carte + période souhaitée.

    Les champs `faces_selectionnees` et `supports_selectionnees` sont des
    champs cachés alimentés en JS (JSON.stringify d'une liste d'IDs) à chaque
    clic sur un marqueur de la carte. Adapte les `name=` des inputs côté
    template si tes noms diffèrent.
    """

    faces_selectionnees = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )
    supports_selectionnees = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )
    date_debut = forms.DateField(
        label="Date de début souhaitée",
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    date_fin = forms.DateField(
        label="Date de fin souhaitée",
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    # ── Parsing des sélections JSON ────────────────────────────────────────
    def _parse_ids(self, raw, field_name):
        if not raw:
            return []
        try:
            ids = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise forms.ValidationError("Sélection invalide.")
        if not isinstance(ids, list):
            raise forms.ValidationError("Sélection invalide.")
        return ids

    def clean_faces_selectionnees(self):
        return self._parse_ids(self.cleaned_data.get('faces_selectionnees'), 'faces_selectionnees')

    def clean_supports_selectionnees(self):
        return self._parse_ids(self.cleaned_data.get('supports_selectionnees'), 'supports_selectionnees')

    def clean(self):
        cleaned = super().clean()
        faces_ids    = cleaned.get('faces_selectionnees') or []
        supports_ids = cleaned.get('supports_selectionnees') or []

        if not faces_ids and not supports_ids:
            raise forms.ValidationError(
                "Veuillez sélectionner au moins un emplacement sur la carte."
            )

        date_debut = cleaned.get('date_debut')
        date_fin   = cleaned.get('date_fin')

        if date_debut and date_fin:
            if date_fin <= date_debut:
                self.add_error('date_fin', "La date de fin doit être postérieure à la date de début.")
            if date_debut < timezone.now().date():
                self.add_error('date_debut', "La date de début ne peut pas être dans le passé.")

        # ── Vérification d'existence ───────────────────────────────────────
        faces = list(FacePanneau.objects.filter(pk__in=faces_ids).select_related('support'))
        if len(faces) != len(set(faces_ids)):
            raise forms.ValidationError("Une ou plusieurs faces sélectionnées sont introuvables.")

        supports = list(Support.objects.filter(pk__in=supports_ids))
        if len(supports) != len(set(supports_ids)):
            raise forms.ValidationError("Un ou plusieurs écrans sélectionnés sont introuvables.")

        # ── Vérification de disponibilité réelle sur la période ───────────
        if date_debut and date_fin and not self.errors:
            indisponibles = [
                f"{face.support.code} Face {face.label}"
                for face in faces
                if not face.is_disponibles(date_debut, date_fin)
            ]
            if indisponibles:
                raise forms.ValidationError(
                    "Les emplacements suivants ne sont plus disponibles sur cette période : "
                    + ", ".join(indisponibles)
                )

        cleaned['faces_uuids']    = faces_ids
        cleaned['supports_uuids'] = supports_ids
        return cleaned


class Etape2Form(forms.Form):
    """Étape 2 — Détails complémentaires du projet (facultatif)."""

    nom_campagne = forms.CharField(
        label="Nom de la campagne / projet",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optionnel'}),
    )
    message = forms.CharField(
        label="Message / précisions",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Optionnel'}),
    )


"""
Patch à fusionner dans portail/forms.py (ou l'app contenant Etape3Form).
⚠️ Je n'ai pas le fichier forms.py original — j'ai reconstitué les champs
existants d'après leur usage dans ReserverEtape3View (d['nom_contact'],
d.get('societe'), d['email'], d['telephone'], d.get('accepte_contact')).
Vérifie que les champs ci-dessous matchent bien tes vrais widgets/labels,
et ne garde que les ajouts (type_client, reference_client_saisie, clean())
si le reste diffère chez toi.
"""
from django import forms
from django.core.exceptions import ValidationError

from campaigns.models import DemandeReservation


class Etape3Form(forms.Form):
    # ⚠️ required=False ici : la vraie obligation est conditionnelle
    # (uniquement en mode "nouveau client"), gérée dans clean() ci-dessous.
    nom_contact = forms.CharField(
        max_length=200,
        required=False,
        label="Nom complet",
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    societe = forms.CharField(
        max_length=200,
        required=False,
        label="Société / Organisation",
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    email = forms.EmailField(
        required=False,
        label="Email",
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    telephone = forms.CharField(
        max_length=30,
        required=False,
        label="Téléphone",
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    accepte_contact = forms.BooleanField(
        required=False,
        label="J'accepte d'être recontacté(e)",
    )

    type_client = forms.ChoiceField(
        choices=DemandeReservation.TYPE_CLIENT_CHOICES,
        initial=DemandeReservation.TYPE_CLIENT_NOUVEAU,
        widget=forms.RadioSelect,
        label="Vous êtes",
    )
    reference_client_saisie = forms.CharField(
        max_length=20,
        required=False,
        label="Votre référence client",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'CLI-2026-A3F9K2',
        }),
    )

    def clean(self):
        cleaned_data = super().clean()
        type_client = cleaned_data.get('type_client')
        reference = cleaned_data.get('reference_client_saisie', '').strip()

        if type_client == DemandeReservation.TYPE_CLIENT_EXISTANT:
            if not reference:
                self.add_error(
                    'reference_client_saisie',
                    "Merci de renseigner votre référence client (ex: CLI-2026-A3F9K2)."
                )
            # Pas besoin de nom/email/téléphone ici : ils seront récupérés
            # depuis le Client rattaché par référence (voir DemandeReservation.save()).
        else:
            # Nouveau client : ces coordonnées sont la seule source d'info, obligatoires.
            for field, msg in [
                ('nom_contact', "Ce champ est obligatoire."),
                ('email', "Ce champ est obligatoire."),
                ('telephone', "Ce champ est obligatoire."),
            ]:
                if not cleaned_data.get(field):
                    self.add_error(field, msg)

        return cleaned_data

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
