"""Request-scoped collection of ORM writes for the audit journal."""

from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field


@dataclass
class AuditState:
    request: object
    events: list = field(default_factory=list)
    event_indexes: dict = field(default_factory=dict)
    explicit_actions: set = field(default_factory=set)
    flushing: bool = False


current_audit_state = ContextVar('current_audit_state', default=None)


def object_key(obj):
    if obj is None:
        return None
    meta = getattr(obj, '_meta', None)
    if meta is None:
        return None
    pk = getattr(obj, 'pk', None) or getattr(obj, '_audit_deleted_pk', None)
    if pk is None:
        return None
    return meta.label_lower, pk


def record_model_write(obj, action, before=None, after=None):
    """Keep the first and last values for repeated writes in one request."""
    state = current_audit_state.get()
    if state is None or state.flushing:
        return

    key = object_key(obj)
    if key is None:
        return

    event_key = (key, action)
    if event_key in state.explicit_actions:
        return
    frozen_obj = copy(obj)
    index = state.event_indexes.get(event_key)
    if index is None:
        state.event_indexes[event_key] = len(state.events)
        state.events.append({
            'key': key,
            'action': action,
            'obj': frozen_obj,
            'before': before,
            'after': after,
        })
    else:
        event = state.events[index]
        event['obj'] = frozen_obj
        event['after'] = after


def suppress_object_events(obj, action):
    """An explicit log_action for this object supersedes its signal entry."""
    state = current_audit_state.get()
    key = object_key(obj)
    if state is None or state.flushing or key is None:
        return None
    audit_action = {
        'create': 'create',
        'delete': 'delete',
    }.get(action, 'update')
    event_key = (key, audit_action)
    state.explicit_actions.add(event_key)
    superseded = [
        event for event in state.events
        if (event['key'], event['action']) == event_key
    ]
    state.events = [
        event for event in state.events
        if (event['key'], event['action']) != event_key
    ]
    state.event_indexes = {
        (event['key'], event['action']): index
        for index, event in enumerate(state.events)
    }
    if not superseded:
        return None
    before = superseded[0]['before'] or {}
    after = superseded[-1]['after'] or {}
    return {
        field: {
            'avant': before.get(field, ''),
            'apres': after.get(field, ''),
        }
        for field in set(before) | set(after)
        if before.get(field, '') != after.get(field, '')
    } or None
