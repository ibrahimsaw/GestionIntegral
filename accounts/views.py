from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.core.paginator import Paginator
from django.urls import reverse_lazy
from .decorators import *
from .models import User, AuditLog
from .forms import LoginForm, UserCreateForm, UserEditForm, AdminPasswordForm
from .audit import log_action, log_changes
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView, View, TemplateView


# ══════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════

class LoginView(View):
    template_name = 'accounts/login.html'

    def get(self, request):
        if request.user.is_authenticated:
            if request.user.is_client:
                return redirect('client_detail', pk=request.user.client_profile.pk)
            return redirect('carte')
        return render(request, self.template_name, {'form': LoginForm()})

    def post(self, request):
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            user = authenticate(request, username=username, password=form.cleaned_data['password'])

            if user:
                if not user.is_active:
                    messages.error(request, "Votre compte est désactivé.")
                    log_action(request, AuditLog.ACTION_LOGIN_FAILED, AuditLog.MODULE_AUTH,
                               detail=f"Compte désactivé : {username}")
                    return render(request, self.template_name, {'form': form})

                login(request, user)
                log_action(request, AuditLog.ACTION_LOGIN, AuditLog.MODULE_AUTH, obj=user,
                           detail=f"Connexion : {user.get_full_name() or user.username}")

                next_url = request.GET.get('next')
                if next_url:
                    return redirect(next_url)
                # if user.is_admin:
                #     return redirect('dashboard')
                # elif user.is_client_role:
                #     return redirect('client_espace')
                if user.is_client:
                    return redirect('client_detail', pk=user.client_profile.pk)
                return redirect('carte')

            log_action(request, AuditLog.ACTION_LOGIN_FAILED, AuditLog.MODULE_AUTH,
                       detail=f"Tentative échouée : {username}")
            messages.error(request, "Identifiants incorrects.")

        return render(request, self.template_name, {'form': form})


class LogoutView(View):
    def get(self, request):
        if request.user.is_authenticated:
            log_action(request, AuditLog.ACTION_LOGOUT, AuditLog.MODULE_AUTH,
                       detail=f"Déconnexion : {request.user.get_full_name() or request.user.username}")
        logout(request)
        messages.info(request, "Vous avez été déconnecté.")
        return redirect('login')


# ══════════════════════════════════════════
# USERS
# ══════════════════════════════════════════

class UserListView(AdminRequiredMixin, ListView):
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    ordering = ['role', 'username']

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get('q', '')
        if q:
            qs = qs.filter(
                Q(username__icontains=q)   |
                Q(first_name__icontains=q) |
                Q(email__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['q'] = self.request.GET.get('q', '')
        return context


class UserCreateView(AdminRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('user_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Nouvel utilisateur'
        return context

    def form_valid(self, form):
        response = super().form_valid(form)  # ✅ définit self.object
        user = self.object                   # ✅ au lieu de self.get_object()
        log_action(self.request, AuditLog.ACTION_CREATE, AuditLog.MODULE_USERS, obj=user)
        messages.success(self.request, f'Utilisateur {user.username} créé.')
        return response


class UserEditView(AdminRequiredMixin, UpdateView):
    model = User
    form_class = UserEditForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('user_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Modifier {self.object.username}'
        context['obj'] = self.object
        return context

    def form_valid(self, form):
        old = User.objects.get(pk=self.object.pk)
        response = super().form_valid(form)
        changes = log_changes(old, self.object, ['role', 'first_name', 'last_name', 'email', 'telephone', 'is_active'])
        action = AuditLog.ACTION_ROLE_CHANGE if (changes and 'role' in changes) else AuditLog.ACTION_UPDATE
        log_action(self.request, action, AuditLog.MODULE_USERS, obj=self.object, changes=changes)
        messages.success(self.request, 'Utilisateur mis à jour.')
        return response


class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    template_name = 'accounts/user_detail.html'
    context_object_name = 'target_user'

    def dispatch(self, request, *args, **kwargs):
        user = self.get_object()
        if not (request.user.is_superuser or request.user.is_admin or request.user.pk == user.pk):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['password_form'] = AdminPasswordForm(self.object)
        return context

    def post(self, request, pk):
        target_user = self.get_object()

        # ── Activer / Bloquer ─────────────────────────────────────────────────
        if 'toggle_active' in request.POST:
            if not (request.user.is_superuser or request.user.is_admin):
                raise PermissionDenied
            ancien = target_user.is_active
            target_user.is_active = not target_user.is_active
            target_user.save()
            log_action(
                request, AuditLog.ACTION_UPDATE, AuditLog.MODULE_USERS,
                obj=target_user,
                changes={'is_active': {'avant': str(ancien), 'apres': str(target_user.is_active)}},
                detail=f"Compte {'activé' if target_user.is_active else 'bloqué'}",
            )
            messages.success(request, f"Utilisateur {'activé' if target_user.is_active else 'bloqué'}.")
            return redirect('user_detail', pk=pk)

        # ── Changement de mot de passe ────────────────────────────────────────
        if 'change_password' in request.POST:
            password_form = AdminPasswordForm(target_user, request.POST)
            if password_form.is_valid():
                password_form.save()
                log_action(request, AuditLog.ACTION_PASSWORD_CHANGE, AuditLog.MODULE_USERS, obj=target_user)
                messages.success(request, 'Mot de passe mis à jour.')
                return redirect('user_detail', pk=pk)
            return render(request, self.template_name, {
                'target_user': target_user,
                'password_form': password_form,
            })

        return redirect('user_detail', pk=pk)


class UserDeleteView(AdminRequiredMixin, DeleteView):
    model = User
    template_name = 'partials/confirm_delete.html'
    success_url = reverse_lazy('user_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': 'Supprimer l’utilisateur',
            'header': 'Suppression d’utilisateur',
            'message_title': 'Supprimer cet utilisateur ?',
            'message_body': 'Vous êtes sur le point de supprimer l’utilisateur',
            'hint': 'L’accès et les données de ce compte seront supprimés définitivement.',
            'confirm_label': 'Supprimer l’utilisateur',
            'cancel_url': reverse_lazy('user_detail', kwargs={'pk': self.object.pk}),
        })
        return context

    def form_valid(self, form):
        log_action(
            self.request, AuditLog.ACTION_DELETE, AuditLog.MODULE_USERS,
            obj=self.object,
            detail=f"Suppression : {self.object.username} ({self.object.get_role_display()})",
        )
        messages.success(self.request, 'Utilisateur supprimé.')
        return super().form_valid(form)


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/profile.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user'] = self.request.user
        return context


# ══════════════════════════════════════════
# AUDIT
# ══════════════════════════════════════════

class AuditLogView(AdminRequiredMixin, ListView):
    model = AuditLog
    template_name = 'accounts/audit_list.html'
    context_object_name = 'logs'
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related('user').order_by('-created_at')

        q         = self.request.GET.get('q', '')
        action    = self.request.GET.get('action', '')
        module    = self.request.GET.get('module', '')
        level     = self.request.GET.get('level', '')
        user_id   = self.request.GET.get('user', '')
        date_from = self.request.GET.get('date_from', '')
        date_to   = self.request.GET.get('date_to', '')

        if q:
            qs = qs.filter(
                Q(object_repr__icontains=q)      |
                Q(detail__icontains=q)           |
                Q(ip_address__icontains=q)       |
                Q(user__username__icontains=q)   |
                Q(user__first_name__icontains=q)
            )
        if action:    qs = qs.filter(action=action)
        if module:    qs = qs.filter(module=module)
        if level:     qs = qs.filter(level=level)
        if user_id:   qs = qs.filter(user_id=user_id)
        if date_from: qs = qs.filter(created_at__date__gte=date_from)
        if date_to:   qs = qs.filter(created_at__date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title':            "Journal d'audit",
            'total':            self.get_queryset().count(),
            'q':                self.request.GET.get('q', ''),
            'filter_action':    self.request.GET.get('action', ''),
            'filter_module':    self.request.GET.get('module', ''),
            'filter_level':     self.request.GET.get('level', ''),
            'filter_user':      self.request.GET.get('user', ''),
            'filter_date_from': self.request.GET.get('date_from', ''),
            'filter_date_to':   self.request.GET.get('date_to', ''),
            'action_choices':   AuditLog.ACTION_CHOICES,
            'module_choices':   AuditLog.MODULE_CHOICES,
            'level_choices':    AuditLog.LEVEL_CHOICES,
            'users_list':       User.objects.filter(
                                    audit_logs__isnull=False
                                ).distinct().order_by('username'),
        })
        return context