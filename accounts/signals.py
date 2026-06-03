# accounts/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from campaigns.models import Client

_syncing_users   = set()
_syncing_clients = set()


@receiver(post_save, sender=Client)
def client_to_user(sender, instance, created, **kwargs):
    if instance.pk in _syncing_clients:
        return

    _syncing_clients.add(instance.pk)
    try:
        from accounts.models import User
        user = User.objects.filter(client_profile=instance).first()

        if user:
            if user.pk in _syncing_users:
                return

            parts     = instance.nom.strip().split(' ', 1)
            new_first = parts[0]
            new_last  = parts[1] if len(parts) > 1 else ''

            update = {}
            if user.first_name != new_first:                                    update['first_name'] = new_first
            if user.last_name  != new_last:                                     update['last_name']  = new_last
            if instance.email     and user.email     != instance.email:         update['email']      = instance.email
            if instance.telephone and user.telephone != instance.telephone:     update['telephone']  = instance.telephone

            if update:
                _syncing_users.add(user.pk)
                try:
                    User.objects.filter(pk=user.pk).update(**update)
                finally:
                    _syncing_users.discard(user.pk)

        else:
            # ── Pas de User → création ──
            import re
            base_username = re.sub(r'\s+', '_', instance.nom.strip().lower())
            base_username = re.sub(r'[^a-z0-9_]', '', base_username)[:30] or 'client'

            username = base_username
            counter  = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}_{counter}"
                counter += 1

            parts    = instance.nom.strip().split(' ', 1)
            new_user = User(
                username       = username,
                email          = instance.email or '',
                first_name     = parts[0],
                last_name      = parts[1] if len(parts) > 1 else '',
                role           = User.ROLE_CLIENT,
                telephone      = instance.telephone or '',
                client_profile = instance,
            )
            new_user.set_password('0000')

            _syncing_users.add('new')
            try:
                new_user.save()
            finally:
                _syncing_users.discard('new')
                if new_user.pk:
                    _syncing_users.discard(new_user.pk)

    finally:
        _syncing_clients.discard(instance.pk)


@receiver(post_save, sender='accounts.User')
def user_to_client(sender, instance, created, **kwargs):
    if instance.pk in _syncing_users or 'new' in _syncing_users:
        return
    if instance.role != 'client':
        return

    _syncing_users.add(instance.pk)
    try:
        if instance.client_profile_id:
            client = instance.client_profile
            if client.pk in _syncing_clients:
                return

            update    = {}
            full_name = instance.get_full_name() or instance.username

            if client.nom != full_name:                                         update['nom']       = full_name
            if instance.email     and client.email     != instance.email:       update['email']     = instance.email
            if instance.telephone and client.telephone != instance.telephone:   update['telephone'] = instance.telephone

            if update:
                _syncing_clients.add(client.pk)
                try:
                    Client.objects.filter(pk=client.pk).update(**update)
                finally:
                    _syncing_clients.discard(client.pk)

        else:
            # ── Pas de Client → création ──
            client = Client(
                nom       = instance.get_full_name() or instance.username,
                email     = instance.email or '',
                telephone = instance.telephone or '',
            )
            _syncing_clients.add('new')
            try:
                client.save()
            finally:
                _syncing_clients.discard('new')
                if client.pk:
                    _syncing_clients.discard(client.pk)

            _syncing_users.add(instance.pk)
            try:
                from accounts.models import User
                User.objects.filter(pk=instance.pk).update(client_profile=client)
            finally:
                _syncing_users.discard(instance.pk)

    finally:
        _syncing_users.discard(instance.pk)