"""Кириш хавфсизлиги: паролни танлаб топишдан ҳимоя, Face ID уринишлари ва хавфсизлик журнали.

Ҳисоблагичлар Django кэшида сақланади (CACHES). Битта жараён (runserver ёки gunicorn --workers 1) учун
стандарт LocMemCache етарли; бир нечта worker бўлса, умумий кэш (Redis ёки DatabaseCache) созланг,
акс ҳолда ҳар бир worker ўз ҳисобини юритади ва чегара worker'лар сонига кўпаяди.
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import ScopedRateThrottle


security_log = logging.getLogger('employees.security')

PENDING_FACE_AUTH_STARTED_AT = 'pending_face_auth_started_at'
PENDING_FACE_AUTH_ATTEMPTS = 'pending_face_auth_attempts'


def client_ip(request):
    """Мижоз IP манзили. nginx (ташқи чегара) X-Real-IP ни ўзи $remote_addr билан ёзади, мижоз юборгани ўчади.

    Django контейнер ичида фақат nginx орқали очилади, шунинг учун REMOTE_ADDR — nginx контейнерининг манзили.
    """
    if getattr(settings, 'TRUST_X_REAL_IP', True):
        real_ip = str(request.META.get('HTTP_X_REAL_IP', '') or '').strip()
        if real_ip:
            return real_ip
    return str(request.META.get('REMOTE_ADDR', '') or '').strip()


def _login_keys(username, ip):
    username = str(username or '').strip().casefold()
    return f'login-fail:user-ip:{username}:{ip}', f'login-fail:ip:{ip}'


def _limit(name, default):
    return int(getattr(settings, name, default))


def is_login_locked(username, ip):
    """Логин+IP бўйича ёки фақат IP бўйича нотўғри уринишлар чегарадан ошганми."""
    user_key, ip_key = _login_keys(username, ip)
    return (
        cache.get(user_key, 0) >= _limit('LOGIN_FAILURE_LIMIT', 5)
        or cache.get(ip_key, 0) >= _limit('LOGIN_IP_FAILURE_LIMIT', 30)
    )


def _increment(key, timeout):
    # add() калит йўқ бўлса яратади; incr() атомар (LocMemCache, Redis, DatabaseCache).
    if not cache.add(key, 1, timeout):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout)


def register_login_failure(username, ip):
    timeout = _limit('LOGIN_LOCKOUT_SECONDS', 900)
    for key in _login_keys(username, ip):
        _increment(key, timeout)
    security_log.warning('login failed: username=%r ip=%s', str(username or '')[:150], ip)


def reset_login_failures(username, ip):
    cache.delete(_login_keys(username, ip)[0])


def start_pending_face_auth(session):
    """Face ID босқичи бошланди: вақт ва уринишлар ҳисоби нолдан."""
    session[PENDING_FACE_AUTH_STARTED_AT] = int(time.time())
    session[PENDING_FACE_AUTH_ATTEMPTS] = 0


def pending_face_auth_problem(session):
    """Face ID сеанси муддати ўтган ёки уринишлар тугаган бўлса — хато матни, акс ҳолда ''."""
    started_at = session.get(PENDING_FACE_AUTH_STARTED_AT)
    if started_at is None:
        # Янгилашдан олдин бошланган сеанс: вақтни биринчи уринишдан ҳисоблаймиз (парол аллақачон текширилган).
        started_at = int(time.time())
        session[PENDING_FACE_AUTH_STARTED_AT] = started_at
        session.modified = True
    ttl = _limit('FACE_LOGIN_PENDING_TTL_SECONDS', 300)
    if int(time.time()) - int(started_at) > ttl:
        return 'Face ID тасдиқлаш вақти тугади. Логин ва паролни қайта киритинг.'
    if int(session.get(PENDING_FACE_AUTH_ATTEMPTS, 0)) >= _limit('FACE_LOGIN_MAX_ATTEMPTS', 5):
        return 'Face ID уринишлари тугади. Логин ва паролни қайта киритинг.'
    return ''


def register_face_attempt(session):
    session[PENDING_FACE_AUTH_ATTEMPTS] = int(session.get(PENDING_FACE_AUTH_ATTEMPTS, 0)) + 1
    session.modified = True


class ScopedThrottleExceptServiceKey(ScopedRateThrottle):
    """DRF ScopedRateThrottle (view.throttle_scope), but not for the service API key.

    Another system (TB) calls Face ID for many employees from one address with one key:
    a common per-IP limit would stop its legitimate checkouts. Web users and anonymous
    requests (login Face ID) are limited per user / per client IP.
    """

    def allow_request(self, request, view):
        from .permissions import is_service_api_request

        if is_service_api_request(request):
            return True
        return super().allow_request(request, view)
