# accounts/apps.py
from django.apps import AppConfig

class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        import accounts.signals  # ✅ charge les deux signaux
        import accounts.audit_signals
        from accounts.audit_queryset import install_queryset_audit_hooks
        install_queryset_audit_hooks()
