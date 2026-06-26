# campaigns/apps.py
from django.apps import AppConfig


class StaffConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'campaigns'
    verbose_name = 'Back-office Staff'