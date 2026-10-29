from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.forms import UserCreateForm, UserEditForm
from campaigns.models import Client


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='users-admin',
            password='test-password',
            role='admin',
        )
        self.client.force_login(self.admin)

    def create_user(self, username, role='staff', is_active=True, **kwargs):
        return get_user_model().objects.create_user(
            username=username,
            password='test-password',
            role=role,
            is_active=is_active,
            **kwargs,
        )

    def test_user_list_filters_by_role_status_and_search(self):
        self.create_user(
            'technician-moussa',
            role='technicien',
            first_name='Moussa',
        )
        self.create_user(
            'staff-moussa-inactive',
            role='staff',
            first_name='Moussa',
            is_active=False,
        )
        self.create_user('other-staff', role='staff')

        response = self.client.get(
            reverse('user_list'),
            {'q': 'Moussa', 'role': 'technicien', 'statut': 'actif'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user.username for user in response.context['users']],
            ['technician-moussa'],
        )
        self.assertEqual(response.context['total_users'], 4)
        self.assertEqual(response.context['active_users'], 3)
        self.assertEqual(response.context['inactive_users'], 1)

    def test_user_list_is_paginated_and_preserves_filters(self):
        for index in range(22):
            self.create_user(f'pagination-user-{index:02}')

        response = self.client.get(
            reverse('user_list'),
            {'q': 'pagination-user', 'role': 'staff', 'statut': 'actif'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['users']), 20)
        self.assertContains(response, 'page=2')
        self.assertContains(response, 'role=staff')
        self.assertContains(response, 'statut=actif')

    def test_admin_can_toggle_another_users_access_but_not_own(self):
        target = self.create_user('toggle-target')

        response = self.client.post(
            reverse('user_list'),
            {'user_id': target.pk},
        )

        self.assertRedirects(response, reverse('user_list'))
        target.refresh_from_db()
        self.assertFalse(target.is_active)

        response = self.client.post(
            reverse('user_list'),
            {'user_id': self.admin.pk},
        )

        self.assertRedirects(response, reverse('user_list'))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_client_edit_form_keeps_its_linked_profile_and_can_unlink_role(self):
        client_profile = Client.objects.create(
            nom='Entreprise Utilisateur',
            contact_nom='Contact Client',
            telephone='70000000',
            email='contact@example.com',
        )
        client_user = client_profile.user_account
        form_data = {
            'username': client_user.username,
            'first_name': client_user.first_name,
            'last_name': client_user.last_name,
            'email': client_user.email,
            'role': 'staff',
            'telephone': client_user.telephone,
            'is_active': 'on',
            'client_profile': str(client_profile.pk),
        }

        form = UserEditForm(data=form_data, instance=client_user)

        self.assertTrue(form.is_valid(), form.errors)
        updated_user = form.save()
        self.assertEqual(updated_user.role, 'staff')
        self.assertIsNone(updated_user.client_profile)

    def test_creating_client_user_creates_only_one_linked_client_profile(self):
        form = UserCreateForm(data={
            'username': 'new-client-account',
            'first_name': 'Nouveau',
            'last_name': 'Client',
            'email': 'new-client@example.com',
            'role': 'client',
            'telephone': '71000000',
            'password1': 'Secure-test-Pass890!',
            'password2': 'Secure-test-Pass890!',
            'client_profile': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(user.client_profile.nom, 'Nouveau Client')
        self.assertEqual(user.client_profile.user_account, user)

    def test_user_detail_exposes_password_reset_action_anchor(self):
        target = self.create_user('password-target')

        response = self.client.get(reverse('user_detail', args=[target.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="admin-actions"')
        self.assertContains(response, 'id="password-reset-form"')

    def test_anonymous_user_is_redirected_before_user_detail_permission_check(self):
        target = self.create_user('anonymous-detail-target')
        self.client.logout()

        response = self.client.get(reverse('user_detail', args=[target.pk]))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('user_detail', args=[target.pk])}",
        )
