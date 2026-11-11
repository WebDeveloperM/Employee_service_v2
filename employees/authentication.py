from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions


class ServiceApiUser(AnonymousUser):
    @property
    def is_authenticated(self):
        return True


class ServiceApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = 'Bearer'
    header_name = 'HTTP_X_EMPLOYEE_SERVICE_KEY'

    def authenticate(self, request):
        configured_keys = set(getattr(settings, 'EMPLOYEE_SERVICE_API_KEYS', []))
        if not configured_keys:
            return None

        api_key = request.META.get(self.header_name, '').strip()
        if not api_key:
            auth_header = authentication.get_authorization_header(request).decode('utf-8')
            if auth_header.startswith(f'{self.keyword} '):
                api_key = auth_header[len(self.keyword) + 1:].strip()

        if not api_key:
            return None

        if api_key not in configured_keys:
            raise exceptions.AuthenticationFailed('Недействительный API-ключ сервиса сотрудников.')

        return ServiceApiUser(), api_key