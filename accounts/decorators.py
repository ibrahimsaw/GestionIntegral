from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def role_required(*roles):
    """Décore une vue pour exiger un ou plusieurs rôles."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser or request.user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied
        return _wrapped
    return decorator


def admin_required(view_func):
    return role_required('admin')(view_func)


def staff_required(view_func):
    return role_required('admin', 'staff')(view_func)


def technicien_required(view_func):
    return role_required('admin', 'staff', 'technicien')(view_func)





from django.contrib.auth.mixins import AccessMixin

class RoleRequiredMixin(AccessMixin):
    allowed_roles = []
    def dispatch(self, request, *args, **kwargs):
        # 1. Vérifie si connecté
        if not request.user.is_authenticated:
            return redirect('login')  # ← redirige vers la page de connexion
        # 2. Vérifie le rôle
        if not self._has_role(request.user):
            raise PermissionDenied  # ← affiche une page 403
        return super().dispatch(request, *args, **kwargs)
    def _has_role(self, user):
        if user.is_superuser:
            return True
        return user.role in self.allowed_roles


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin']

class StaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'staff']

class TechnicienStaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'staff', 'technicien']
    
class TechnicienRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin','technicien']

class ClientRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'client']
    
class ClientStaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'staff', 'client']

# Class pour l'authentification

class LoginRequiredMixin(AccessMixin):
    """Mélange pour exiger que l'utilisateur soit connecté."""
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')  # ← redirige vers la page de connexion
        return super().dispatch(request, *args, **kwargs)








