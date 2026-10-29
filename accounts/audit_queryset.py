"""Cover QuerySet mutations, which intentionally bypass Django model signals."""

from django.db.models.query import QuerySet

from .audit_context import current_audit_state, record_model_write
from .audit_signals import AUDITED_APPS, _snapshot
from .models import AuditLog


def _is_tracked(queryset):
    return (
        current_audit_state.get() is not None
        and queryset.model._meta.app_label in AUDITED_APPS
        and queryset.model is not AuditLog
    )


def install_queryset_audit_hooks():
    if getattr(QuerySet, '_integral_audit_hooks', False):
        return

    original_update = QuerySet.update
    original_bulk_create = QuerySet.bulk_create
    original_bulk_update = QuerySet.bulk_update

    def update(queryset, **kwargs):
        if not _is_tracked(queryset):
            return original_update(queryset, **kwargs)
        ids = list(queryset.values_list('pk', flat=True))
        old_objects = {
            obj.pk: obj for obj in queryset.model._base_manager.filter(pk__in=ids)
        }
        count = original_update(queryset, **kwargs)
        new_objects = {
            obj.pk: obj for obj in queryset.model._base_manager.filter(pk__in=ids)
        }
        for pk, old_obj in old_objects.items():
            new_obj = new_objects.get(pk)
            if new_obj is not None:
                before, after = _snapshot(old_obj), _snapshot(new_obj)
                if before != after:
                    record_model_write(
                        new_obj, AuditLog.ACTION_UPDATE, before=before, after=after
                    )
        return count

    def bulk_create(queryset, objs, *args, **kwargs):
        objs = list(objs)
        result = original_bulk_create(queryset, objs, *args, **kwargs)
        if _is_tracked(queryset):
            for obj in objs:
                if obj.pk is not None:
                    record_model_write(
                        obj, AuditLog.ACTION_CREATE, after=_snapshot(obj)
                    )
        return result

    def bulk_update(queryset, objs, fields, *args, **kwargs):
        objs = list(objs)
        if not _is_tracked(queryset):
            return original_bulk_update(queryset, objs, fields, *args, **kwargs)
        ids = [obj.pk for obj in objs if obj.pk is not None]
        old_objects = {
            obj.pk: obj for obj in queryset.model._base_manager.filter(pk__in=ids)
        }
        count = original_bulk_update(queryset, objs, fields, *args, **kwargs)
        new_objects = {
            obj.pk: obj for obj in queryset.model._base_manager.filter(pk__in=ids)
        }
        for pk, old_obj in old_objects.items():
            new_obj = new_objects.get(pk)
            if new_obj is not None:
                before, after = _snapshot(old_obj), _snapshot(new_obj)
                if before != after:
                    record_model_write(
                        new_obj, AuditLog.ACTION_UPDATE, before=before, after=after
                    )
        return count

    QuerySet.update = update
    QuerySet.bulk_create = bulk_create
    QuerySet.bulk_update = bulk_update
    QuerySet._integral_audit_hooks = True
