from django import forms
from django.contrib.auth.forms import UserCreationForm, SetPasswordForm
from django.db.models import Q
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, Field
from campaigns.models import Client
from .models import User

W = {'class': 'form-control'}
S = {'class': 'form-select'}


class ClientProfileFormMixin:
    def configure_client_profile_field(self):
        self.fields['client_profile'].label = 'Profil client associé'
        self.fields['client_profile'].help_text = (
            "Lors de l’association, les coordonnées du compte sont reprises de la fiche client. "
            "Un profil client ne peut être associé qu'à un seul compte."
        )
        clients = Client.objects.filter(user_account__isnull=True)
        if self.instance.pk and self.instance.client_profile_id:
            clients = Client.objects.filter(
                Q(user_account__isnull=True)
                | Q(user_account__pk=self.instance.pk)
            )
        self.fields['client_profile'].queryset = clients.order_by('nom')
        self.fields['client_profile'].initial = self.instance.client_profile_id

    def clean(self):
        cleaned_data = super().clean()
        client_profile = cleaned_data.get('client_profile')
        if cleaned_data.get('role') != User.ROLE_CLIENT:
            cleaned_data['client_profile'] = None
        elif (
            client_profile
            and client_profile.pk != self.instance.client_profile_id
        ):
            first_name, separator, last_name = client_profile.nom.strip().partition(' ')
            cleaned_data['first_name'] = first_name
            cleaned_data['last_name'] = last_name if separator else ''
            cleaned_data['email'] = client_profile.email
            cleaned_data['telephone'] = client_profile.telephone
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.client_profile = self.cleaned_data.get('client_profile')
        if commit:
            user.save()
            self.save_m2m()
        return user


class LoginForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={**W, 'placeholder': 'Identifiant', 'autofocus': True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={**W, 'placeholder': 'Mot de passe'}))


class UserCreateForm(ClientProfileFormMixin, UserCreationForm):
    client_profile = forms.ModelChoiceField(
        queryset=Client.objects.none(),
        required=False,
        label='Profil client associé',
        empty_label='Aucun profil client',
        widget=forms.Select(attrs=S),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'role', 'telephone', 'avatar', 'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs=W),
            'first_name': forms.TextInput(attrs=W),
            'last_name': forms.TextInput(attrs=W),
            'email': forms.EmailInput(attrs=W),
            'role': forms.Select(attrs=S),
            'telephone': forms.TextInput(attrs=W),
            'avatar': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_client_profile_field()
        self.fields['password1'].widget.attrs.update(W)
        self.fields['password2'].widget.attrs.update(W)


class UserEditForm(ClientProfileFormMixin, forms.ModelForm):
    client_profile = forms.ModelChoiceField(
        queryset=Client.objects.none(),
        required=False,
        label='Profil client associé',
        empty_label='Aucun profil client',
        widget=forms.Select(attrs=S),
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'role', 'telephone', 'avatar', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs=W),
            'first_name': forms.TextInput(attrs=W),
            'last_name': forms.TextInput(attrs=W),
            'email': forms.EmailInput(attrs=W),
            'role': forms.Select(attrs=S),
            'telephone': forms.TextInput(attrs=W),
            'avatar': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_client_profile_field()

class ProfilForm(forms.ModelForm):
    """Formulaire pour que l'utilisateur modifie son propre profil."""

    class Meta:
        model  = User
        fields = ('first_name', 'last_name', 'email', 'telephone', 'avatar')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Row(
                Column('first_name', css_class='col-md-6'),
                Column('last_name',  css_class='col-md-6'),
            ),
            Row(
                Column('email',     css_class='col-md-6'),
                Column('telephone', css_class='col-md-6'),
            ),
            'avatar',
            Submit('submit', 'Enregistrer', css_class='btn btn-primary mt-3')
        )


class AdminPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update(W)