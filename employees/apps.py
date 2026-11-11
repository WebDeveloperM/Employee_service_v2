import logging

from django.apps import AppConfig


class EmployeesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'employees'

    def ready(self):
        from . import schema
        self._bootstrap_pki()

    def _bootstrap_pki(self):
        # nginx's ssl_client_certificate must point to an existing ca.crt as
        # soon as it starts, so the CA is created eagerly here instead of
        # lazily on first "Download key" click.
        try:
            from .pki import get_or_create_ca
            get_or_create_ca()
        except Exception:
            logging.getLogger(__name__).exception('Could not bootstrap the employee client-certificate CA')