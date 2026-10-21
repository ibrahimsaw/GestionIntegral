import warnings
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from PIL import Image

from campaigns.models import LigneCampagne
from .models import PigeFichier

MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 20
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    widget = MultipleFileInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={
            'multiple': True,
            'accept': '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp',
            'class': 'form-control',
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            if self.required:
                raise ValidationError(self.error_messages['required'], code='required')
            return []

        files = list(data) if isinstance(data, (list, tuple)) else [data]
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise ValidationError(
                f"Vous pouvez envoyer au maximum {MAX_FILES_PER_UPLOAD} photos par envoi."
            )

        cleaned_files = []
        for uploaded_file in files:
            cleaned_file = super().clean(uploaded_file, initial)
            self._validate_image(cleaned_file)
            cleaned_files.append(cleaned_file)
        return cleaned_files

    @staticmethod
    def _validate_image(uploaded_file):
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValidationError(
                "Formats acceptés : JPG, JPEG, PNG et WEBP."
            )
        if uploaded_file.size > MAX_IMAGE_SIZE:
            raise ValidationError(
                f"Chaque photo doit faire 10 Mo maximum : {uploaded_file.name}."
            )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                uploaded_file.seek(0)
                with Image.open(uploaded_file) as image:
                    if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                        raise ValidationError(
                            f"Le fichier {uploaded_file.name} n'est pas une image JPG, PNG ou WEBP valide."
                        )
                    image.verify()
                uploaded_file.seek(0)
                with Image.open(uploaded_file) as image:
                    image.load()
        except ValidationError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError) as exc:
            raise ValidationError(
                f"Impossible de lire l'image {uploaded_file.name}. Vérifiez le fichier."
            ) from exc
        finally:
            uploaded_file.seek(0)


class PigesUploadForm(forms.Form):
    ligne = forms.ModelChoiceField(
        queryset=LigneCampagne.objects.none(),
        label="Support ou emplacement de la campagne",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    date_prise = forms.DateField(
        label="Date de prise de vue",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    photos = MultipleImageField(
        label="Photos de preuve",
        help_text="JPG, JPEG, PNG ou WEBP ; 10 Mo maximum par photo, 20 photos par envoi.",
    )

    def __init__(self, *args, campagne, **kwargs):
        super().__init__(*args, **kwargs)
        campagne_ids = [campagne.pk]
        if campagne.est_mere:
            campagne_ids.extend(campagne.sous_campagnes.values_list('pk', flat=True))
        lignes = (
            LigneCampagne.objects.filter(campagne_id__in=campagne_ids)
            .select_related('support', 'face__support', 'emplacement__marche')
        )
        piges_existantes = PigeFichier.objects.filter(campagne_id__in=campagne_ids)
        lignes_couvertes = piges_existantes.exclude(ligne_id__isnull=True).values_list(
            'ligne_id', flat=True,
        )
        supports_couverts = set(piges_existantes.exclude(support_id__isnull=True).values_list(
            'support_id', flat=True,
        ))
        supports_couverts.update(
            piges_existantes.exclude(ligne__support_id__isnull=True).values_list(
                'ligne__support_id', flat=True,
            )
        )
        supports_couverts.update(
            piges_existantes.exclude(ligne__face__support_id__isnull=True).values_list(
                'ligne__face__support_id', flat=True,
            )
        )
        lignes = lignes.exclude(pk__in=lignes_couvertes)
        if supports_couverts:
            lignes = lignes.exclude(
                Q(support_id__in=supports_couverts)
                | Q(face__support_id__in=supports_couverts)
            )
        self.fields['ligne'].queryset = lignes.order_by(
            'campagne__nom', 'support__ville', 'support__nom', 'emplacement__code',
        )
        self.fields['ligne'].label_from_instance = self._label_ligne

    @staticmethod
    def _label_ligne(ligne):
        if ligne.support_id:
            support = ligne.support
            label = f"{support.ville or 'Ville non renseignée'} · {support.code} · {support.nom}"
            if ligne.face_id:
                label += f" — Face {ligne.face.label}"
            return label
        if ligne.emplacement_id:
            return (
                f"{ligne.emplacement.marche.ville or 'Ville non renseignée'} · "
                f"{ligne.emplacement.marche.nom} · {ligne.emplacement.code}"
            )
        return str(ligne)


class PigeFichierMetadataForm(forms.ModelForm):
    class Meta:
        model = PigeFichier
        fields = ['date_prise']
        widgets = {
            'date_prise': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
        }
        labels = {'date_prise': "Date de prise de vue"}
