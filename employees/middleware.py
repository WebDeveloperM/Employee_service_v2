from django.contrib.auth import login
from django.utils import timezone

from .models import EmployeeCertificate
from .pki import normalize_serial_hex


class ClientCertificateAuthenticationMiddleware:
    """Logs the request in automatically when nginx has already verified a
    client certificate (mTLS) issued by employees.pki for this employee.

    nginx sets ssl_client_certificate to our CA and forwards the handshake
    result via X-SSL-Client-Verify / X-SSL-Client-Serial headers (see
    nginx.standalone.conf). Must run after AuthenticationMiddleware so
    request.user is already resolved from the session, if any.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._authenticate_via_client_certificate(request)
        return self.get_response(request)

    def _authenticate_via_client_certificate(self, request):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            return

        if request.META.get('HTTP_X_SSL_CLIENT_VERIFY') != 'SUCCESS':
            return

        serial_number = normalize_serial_hex(request.META.get('HTTP_X_SSL_CLIENT_SERIAL', ''))
        if not serial_number or serial_number == '0':
            return

        certificate = EmployeeCertificate.objects.select_related('employee__user').filter(
            serial_number=serial_number,
            revoked=False,
            expires_at__gt=timezone.now(),
        ).first()
        if certificate is None:
            return

        user = certificate.employee.user
        if user is None or not user.is_active:
            return

        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
