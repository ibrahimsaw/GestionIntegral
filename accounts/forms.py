from django import forms
from django.contrib.auth.forms import UserCreationForm, SetPasswordForm
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, Field
from .models import User

W = {'class': 'form-control'}
S = {'class': 'form-select'}


class LoginForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={**W, 'placeholder': 'Identifiant', 'autofocus': True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={**W, 'placeholder': 'Mot de passe'}))


class UserCreateForm(UserCreationForm):
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
        self.fields['password1'].widget.attrs.update(W)
        self.fields['password2'].widget.attrs.update(W)


class UserEditForm(forms.ModelForm):
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