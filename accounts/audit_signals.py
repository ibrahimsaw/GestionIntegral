"""Capture model writes performed during a Django request."""

from django.db.models import Model
from django.db.models.signals import pre_delete, pre_save, post_save
from django.dispatch import receiver

from .audit_context import current_audit_state, record_model_write
from .models import AuditLog


AUDITED_APPS = {
    'accounts', 'auth', 'inventory', 'campaigns', 'planning', 'reports',
    'piges', 'portail', 'core',
}
SENSITIVE_FIELDS = {'password', 'password1', 'password2', 'secret', 'token'}


def _is_audited_model(sender):
    return (
        issubclass(sender, Model)
        and sender._meta.app_label in AUDITED_APPS
        and sender is not AuditLog
    )


def _snapshot(instance):
    values = {}
    for model_field in instance._meta.concrete_fields:
        field_name = model_field.name.lower()
        if any(sensitive in field_name for sensitive in SENSITIVE_FIELDS):
            continue
        value = getattr(instance, model_field.attname, None)
        values[model_field.name] = '' if value is None else str(value)
    return values


@receiver(pre_save, dispatch_uid='accounts.audit.capture_pre_save')
def capture_pre_save(sender, instance, raw=False, **kwargs):
    if raw or not _is_audited_model(sender) or current_audit_state.get() is None:
        return
    if instance._state.adding or instance.pk is None:
        instance._audit_before = None
        return
    previous = sender._base_manager.filter(pk=instance.pk).first()
    instance._audit_before = _snapshot(previous) if previous is not None else None


@receiver(post_save, dispatch_uid='accounts.audit.capture_post_save')
def capture_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not _is_audited_model(sender):
        return
    before = getattr(instance, '_audit_before', None)
    persisted = sender._base_manager.filter(pk=instance.pk).first()
    after = _snapshot(persisted or instance)
    if created or before is None:
        record_model_write(instance, AuditLog.ACTION_CREATE, after=after)
        return
    if before != after:
        record_model_write(instance, AuditLog.ACTION_UPDATE, before=before, after=after)


@receiver(pre_delete, dispatch_uid='accounts.audit.capture_delete')
def capture_pre_delete(sender, instance, **kwargs):
    if _is_audited_model(sender):
        instance._audit_deleted_pk = instance.pk
        record_model_write(instance, AuditLog.ACTION_DELETE, before=_snapshot(instance))
