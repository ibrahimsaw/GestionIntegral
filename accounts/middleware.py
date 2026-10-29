"""Attach the authenticated requester to model-write audit events."""

from django.db import connection, transaction

from .audit_context import current_audit_state, suppress_object_events
from .models import AuditLog


MODULE_BY_APP = {
    'accounts': AuditLog.MODULE_USERS,
    'auth': AuditLog.MODULE_AUTH,
    'inventory': AuditLog.MODULE_INVENTORY,
    'campaigns': AuditLog.MODULE_CAMPAIGNS,
    'planning': AuditLog.MODULE_PLANNING,
    'reports': AuditLog.MODULE_REPORTS,
    'piges': 'piges',
    'portail': 'portail',
    'core': 'core',
}


def _module_for(event):
    app_label, model_name = event['key'][0].split('.', 1)
    if app_label == 'campaigns' and model_name == 'client':
        return AuditLog.MODULE_CLIENTS
    return MODULE_BY_APP.get(app_label, app_label)


def _changes(event):
    before, after = event['before'] or {}, event['after'] or {}
    fields = set(before) | set(after)
    changed = {
        field: {
            'avant': before.get(field, ''),
            'apres': after.get(field, ''),
        }
        for field in fields
        if before.get(field, '') != after.get(field, '')
    }
    return changed or None


def _flush_events(state):
    if not state.events:
        return
    state.flushing = True
    try:
        from .audit import log_action

        for event in state.events:
            log_action(
                state.request,
                event['action'],
                _module_for(event),
                obj=event['obj'],
                changes=_changes(event),
                detail=f"{event['action'].capitalize()} via l’application.",
            )
    finally:
        state.flushing = False
        state.events.clear()


class AuditRequestMiddleware:
    """Collect ORM writes for a request and persist them after successful commit."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .audit_context import AuditState

        state = AuditState(request=request)
        token = current_audit_state.set(state)
        try:
            return self.get_response(request)
        finally:
            current_audit_state.reset(token)
            if connection.in_atomic_block:
                transaction.on_commit(lambda: _flush_events(state))
            else:
                _flush_events(state)
