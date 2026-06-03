# mon_app/mixins.py
from django.shortcuts import redirect
from django.contrib import messages

class BaseRoleRequiredMixin:
    """Mixin de base pour gérer la vérification des rôles de manière sécurisée."""
    required_roles = []  # Liste des attributs à vérifier (ex: ['is_technicien_role'])
    
    def dispatch(self, request, *args, **kwargs):
        # 1. Sécurité : On vérifie si l'utilisateur est authentifié
        if not request.user.is_authenticated:
            messages.error(request, "Vous devez être connecté pour accéder à cette page.")
            return redirect('login')
        
        # 2. Vérification dynamique des rôles (Logique OR : il suffit d'un seul rôle valide)
        has_permission = any(getattr(request.user, role, False) for role in self.required_roles)
        
        if not has_permission:
            # Optionnel : Rediriger vers une page 403 ou le login avec un message personnalisé
            messages.warning(request, "Vous n'avez pas les permissions nécessaires pour accéder à cette ressource.")
            return redirect('login') # Ou redirect('dashboard') selon ta logique
            
        return super().dispatch(request, *args, **kwargs)


# Tes mixins deviennent extrêmement simples et faciles à maintenir :

class StaffRequiredMixin(BaseRoleRequiredMixin):
    required_roles = ['is_staff_regie_role']

class AdminRequiredMixin(BaseRoleRequiredMixin):
    required_roles = ['is_admin']

class ClientRoleRequiredMixin(BaseRoleRequiredMixin):
    required_roles = ['is_client_role']

class TechnicienRequiredMixin(BaseRoleRequiredMixin):
    required_roles = ['is_technicien_role']

class StaffTechnicienRequiredMixin(BaseRoleRequiredMixin):
    required_roles = ['is_staff_regie_role', 'is_technicien_role']

class StaffClientRoleRequiredMixin(BaseRoleRequiredMixin):
    required_roles = ['is_staff_regie_role', 'is_client_role']