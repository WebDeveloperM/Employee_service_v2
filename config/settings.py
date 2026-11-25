from pathlib import Path
import os
import json
import sys
import tempfile
from urllib.parse import urlparse
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue

        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / '.env')


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name: str, default: str = '') -> list:
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


# Placeholder-like values that must never be used once DEBUG is off (they are in the repo / docs).
INSECURE_SECRET_MARKERS = ('your', 'change', 'dev-', 'insecure', 'example', 'secret-key')


def resolve_db_host(raw_host: str) -> str:
    host = (raw_host or 'localhost').strip()
    running_in_docker = Path('/.dockerenv').exists() or os.environ.get('DOTNET_RUNNING_IN_CONTAINER') == 'true'
    if host == 'host.docker.internal' and not running_in_docker:
        return '127.0.0.1'
    return host


def normalize_public_base_url(raw_value: str, fallback: str) -> str:
    value = (raw_value or fallback).strip().rstrip('/')
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        raise ImproperlyConfigured('PUBLIC_BASE_URL must include scheme and hostname, for example https://192.168.101.6')
    return value


def build_public_url(base_url: str, port: int) -> str:
    parsed = urlparse(base_url)
    return f'{parsed.scheme}://{parsed.hostname}:{port}'


PUBLIC_BASE_URL = normalize_public_base_url(
    os.environ.get('PUBLIC_BASE_URL', ''),
    'https://192.168.101.6',
)
TB_PROJECT_BACKEND_URL = build_public_url(PUBLIC_BASE_URL, 8050)
TB_PROJECT_FRONTEND_URL = build_public_url(PUBLIC_BASE_URL, 6060)
EMPLOYEE_SERVICE_PUBLIC_URL = build_public_url(PUBLIC_BASE_URL, 5000)
PUBLIC_HOST = urlparse(PUBLIC_BASE_URL).hostname
LEGACY_PUBLIC_HOSTS = ['192.168.101.6']
INTERNAL_ALLOWED_HOSTS = ['host.docker.internal', 'employee-service', *LEGACY_PUBLIC_HOSTS]

# Secure by default: DEBUG is on only when the environment says so explicitly.
DEBUG = env_bool('EMPLOYEE_SERVICE_DEBUG', False)


def looks_insecure(value: str, min_length: int) -> bool:
    lowered = (value or '').lower()
    return len(value or '') < min_length or any(marker in lowered for marker in INSECURE_SECRET_MARKERS)


SECRET_KEY = os.environ.get('SECRET_KEY', '').strip()
if not DEBUG and looks_insecure(SECRET_KEY, 40):
    # SECRET_KEY signs bnpzID codes and sessions: with the key from the repo anyone can forge a bnpzID login.
    raise ImproperlyConfigured(
        'SECRET_KEY is missing or is a placeholder. Generate one: '
        'python -c "import secrets; print(secrets.token_urlsafe(64))" and put it into .env.'
    )
SECRET_KEY = SECRET_KEY or 'employee-service-dev-secret-key'
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')
if PUBLIC_HOST and PUBLIC_HOST not in ALLOWED_HOSTS and '*' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(PUBLIC_HOST)
if '*' not in ALLOWED_HOSTS:
    for host in INTERNAL_ALLOWED_HOSTS:
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'drf_spectacular',
    'corsheaders',
    'employees',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Static files (admin) under gunicorn without DEBUG; added below only if whitenoise is installed.
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'employees.middleware.ClientCertificateAuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

try:
    import whitenoise  # noqa: F401
except ImportError:   # old image without the new requirements: static files only under DEBUG, as before
    pass
else:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'employees' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

db_engine = os.environ.get('EMPLOYEE_SERVICE_DB_ENGINE', '').strip()
db_name = os.environ.get('EMPLOYEE_SERVICE_DB_NAME', '').strip()

if not db_engine or not db_name:
    raise ImproperlyConfigured(
        'EMPLOYEE_SERVICE_DB_ENGINE and EMPLOYEE_SERVICE_DB_NAME must be set in environment or .env.'
    )

if db_engine == 'django.db.backends.sqlite3':
    raise ImproperlyConfigured('SQLite is disabled for employee_service. Configure PostgreSQL in .env.')

DATABASES = {
    'default': {
        'ENGINE': db_engine,
        'NAME': db_name,
        'USER': os.environ.get('EMPLOYEE_SERVICE_DB_USER', '').strip(),
        'PASSWORD': os.environ.get('EMPLOYEE_SERVICE_DB_PASSWORD', '').strip(),
        'HOST': resolve_db_host(os.environ.get('EMPLOYEE_SERVICE_DB_HOST', 'localhost')),
        'PORT': os.environ.get('EMPLOYEE_SERVICE_DB_PORT', '5432').strip(),
        # Persistent connections: before, every request opened a new PostgreSQL connection (TCP + auth).
        # The health check replaces a stale connection (e.g. after a PostgreSQL restart) before it is used.
        'CONN_MAX_AGE': int(os.environ.get('EMPLOYEE_SERVICE_DB_CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
    }
}

# Applies to createsuperuser and the admin; the web forms use the same rules in Uzbek (serializers.password_problems).
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('EMPLOYEE_SERVICE_TIME_ZONE', 'Asia/Tashkent')
USE_I18N = True
USE_TZ = True

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 500

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Default: open, as before. The TB project (ppe_product) shows employee photos straight from
# https://<host>:5000/media/... in its pages (<img>) and its media proxy fetches them without the API key,
# so a login check here breaks its photos. Turn on (true) only after TB sends X-Employee-Service-Key.
MEDIA_REQUIRE_AUTH = env_bool('MEDIA_REQUIRE_AUTH', False)
# Behind nginx (docker-compose): Django checks access, nginx streams the file from its internal location
# (nginx.standalone.conf, /protected-media/). Off by default: without that nginx location the body would be empty.
MEDIA_X_ACCEL_REDIRECT = env_bool('MEDIA_X_ACCEL_REDIRECT', False)
MEDIA_X_ACCEL_PREFIX = '/protected-media/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

JAZZMIN_SETTINGS = {
    'site_title': 'Employee Service Admin',
    'site_header': 'Employee Service',
    'site_brand': 'Employee Service',
    'site_logo_classes': 'img-circle',
    'welcome_sign': 'Employee Service admin panel',
    'copyright': 'TB + Employee Service',
    'search_model': ['auth.User', 'employees.Employee', 'employees.UserProfile'],
    'show_sidebar': True,
    'navigation_expanded': True,
    'hide_apps': [],
    'hide_models': [],
    'order_with_respect_to': ['auth', 'employees'],
    'icons': {
        'auth': 'fas fa-users-cog',
        'auth.user': 'fas fa-user',
        'employees.Employee': 'fas fa-id-card',
        'employees.UserProfile': 'fas fa-user-shield',
        'employees.Department': 'fas fa-building',
        'employees.Section': 'fas fa-sitemap',
        'employees.StaffPosition': 'fas fa-list-check',
    },
}

JAZZMIN_UI_TWEAKS = {
    'theme': 'flatly',
    'dark_mode_theme': 'darkly',
    'navbar': 'navbar-white navbar-light',
    'accent': 'accent-primary',
    'sidebar': 'sidebar-dark-primary',
    'brand_colour': 'navbar-primary',
    'sidebar_nav_small_text': False,
    'sidebar_disable_expand': False,
    'sidebar_nav_child_indent': True,
    'sidebar_nav_compact_style': False,
    'sidebar_nav_flat_style': False,
    'sidebar_nav_legacy_style': False,
    'actions_sticky_top': True,
}

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'employees.authentication.ServiceApiKeyAuthentication',
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'employees.permissions.ServiceApiAuthenticatedPermission',
    ),
    # nginx appends the real client address to X-Forwarded-For: the throttles key on it, not on nginx's IP.
    'NUM_PROXIES': int(os.environ.get('NUM_PROXIES', '1')),
    'DEFAULT_THROTTLE_RATES': {
        'face': os.environ.get('FACE_THROTTLE_RATE', '30/min'),      # Face ID / face detection (heavy CPU work)
        'bnpzid': os.environ.get('BNPZID_THROTTLE_RATE', '60/min'),  # bnpzID code exchange
    },
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'API сервиса сотрудников',
    'DESCRIPTION': 'Переиспользуемый серверный сервис для сотрудников и проверки по лицу.',
    'VERSION': '1.0.0',
    'APPEND_COMPONENTS': {
        'securitySchemes': {
            'EmployeeServiceApiKeyHeader': {
                'type': 'apiKey',
                'in': 'header',
                'name': 'X-Employee-Service-Key',
                'description': 'API key for cross-project integration with employee_service.',
            },
            'EmployeeServiceBearerToken': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'API key',
                'description': 'Alternative auth format: Authorization: Bearer <api-key>.',
            },
        },
    },
    'SECURITY': [
        {'EmployeeServiceApiKeyHeader': []},
        {'EmployeeServiceBearerToken': []},
    ],
}

# The dev key is published in the repo and docs: it works only with DEBUG on.
EMPLOYEE_SERVICE_API_KEYS = env_list('EMPLOYEE_SERVICE_API_KEYS', 'dev-employee-service-key' if DEBUG else '')
if not DEBUG and any(looks_insecure(key, 32) for key in EMPLOYEE_SERVICE_API_KEYS):
    raise ImproperlyConfigured(
        'EMPLOYEE_SERVICE_API_KEYS contains a short or placeholder key. '
        'Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )

PKI_DIR = BASE_DIR / 'pki'
EMPLOYEE_CERT_VALIDITY_DAYS = int(os.environ.get('EMPLOYEE_CERT_VALIDITY_DAYS', '730'))

FACE_SIMILARITY_THRESHOLD = float(os.environ.get('FACE_SIMILARITY_THRESHOLD', '72.0'))
BNPZID_ENABLED = os.environ.get('BNPZID_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
BNPZID_CODE_TTL_SECONDS = int(os.environ.get('BNPZID_CODE_TTL_SECONDS', '120'))
BNPZID_ACCESS_CHECK_CA_BUNDLE = os.environ.get('BNPZID_ACCESS_CHECK_CA_BUNDLE', '').strip()
BNPZID_ACCESS_CHECK_VERIFY_SSL = os.environ.get(
    'BNPZID_ACCESS_CHECK_VERIFY_SSL',
    'false' if DEBUG else 'true',
).strip().lower() in {'1', 'true', 'yes', 'on'}
_default_bnpzid_clients = {
    'tb-project': {
        'name': 'TB project',
        'client_secret': 'dev-bnpzid-secret',
        'access_check_url': f'{TB_PROJECT_BACKEND_URL}/api/v1/users/bnpzid/access-check/',
        'allowed_redirects': [
            'http://localhost:5175/auth/signin',
            'http://127.0.0.1:5175/auth/signin',
            f'http://{PUBLIC_HOST}:6060/auth/signin',
            f'{TB_PROJECT_FRONTEND_URL}/auth/signin',
        ],
    },
}

_raw_bnpzid_clients = os.environ.get('BNPZID_CLIENTS', '').strip()
if _raw_bnpzid_clients:
    try:
        BNPZID_CLIENTS = json.loads(_raw_bnpzid_clients)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured('BNPZID_CLIENTS must be valid JSON object mapping client ids to config.') from exc
else:
    # The default client secret is in the repo: without DEBUG only clients from BNPZID_CLIENTS
    # or from the database (BnpzIdClient, admin panel) are accepted.
    BNPZID_CLIENTS = _default_bnpzid_clients if DEBUG else {}
if not DEBUG and isinstance(BNPZID_CLIENTS, dict):
    for _client_id, _client in BNPZID_CLIENTS.items():
        if looks_insecure(str((_client or {}).get('client_secret', '')), 32):
            raise ImproperlyConfigured(f'BNPZID_CLIENTS[{_client_id!r}].client_secret is short or a placeholder.')

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    TB_PROJECT_FRONTEND_URL,
    f"http://{PUBLIC_HOST}:6060",
]

CORS_ALLOWED_ORIGINS += env_list('CORS_EXTRA_ORIGINS')
CORS_ALLOW_CREDENTIALS = True
# Was "= DEBUG" (and DEBUG is on in production): any site could read the API with the user's cookies.
CORS_ALLOW_ALL_ORIGINS = False

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ---- Cookies and security headers ----
# Secure cookies need HTTPS (nginx on :5000 serves HTTPS). Local HTTP development: SECURE_COOKIES=false.
SECURE_COOKIES = env_bool('SECURE_COOKIES', not DEBUG)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
# Cookies are per host, not per port: other apps on the same server (TB on :6060/:8050) receive them too.
# A distinct name avoids clashing with their "sessionid" (changing it logs everyone out once).
SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME', 'sessionid')
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE_SECONDS', str(12 * 60 * 60)))   # 12 hours instead of 2 weeks
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))   # enable once the certificate is trusted
SECURE_HSTS_INCLUDE_SUBDOMAINS = False

# ---- Cache: login/Face ID counters, API throttles, bnpzID one-time codes (employees/security.py) ----
# Shared by all gunicorn workers of the container: files in a local temp directory (not the bind mount).
# LocMem was per process, so with several workers every worker had its own counters.
# Under "manage.py test" the cache is a no-op so that counters do not leak between tests
# (the security tests switch a real cache on for themselves).
if sys.argv[1:2] == ['test']:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': os.environ.get('EMPLOYEE_SERVICE_CACHE_DIR', '').strip()
            or os.path.join(tempfile.gettempdir(), 'employee_service_cache'),
            # Culling drops a third of the entries at random: keep it rare so lockout counters survive.
            'OPTIONS': {'MAX_ENTRIES': 20000},
        },
    }

# ---- Login protection (employees/security.py) ----
LOGIN_FAILURE_LIMIT = int(os.environ.get('LOGIN_FAILURE_LIMIT', '5'))          # per login + IP
LOGIN_IP_FAILURE_LIMIT = int(os.environ.get('LOGIN_IP_FAILURE_LIMIT', '30'))   # per IP, any login
LOGIN_LOCKOUT_SECONDS = int(os.environ.get('LOGIN_LOCKOUT_SECONDS', '900'))
FACE_LOGIN_MAX_ATTEMPTS = int(os.environ.get('FACE_LOGIN_MAX_ATTEMPTS', '5'))
FACE_LOGIN_PENDING_TTL_SECONDS = int(os.environ.get('FACE_LOGIN_PENDING_TTL_SECONDS', '300'))
TRUST_X_REAL_IP = env_bool('TRUST_X_REAL_IP', True)   # nginx sets X-Real-IP; turn off if Django is exposed directly

# ---- Logging: security events go to the container log (docker logs es_app) ----
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
    },
    'loggers': {
        'employees.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}
CSRF_TRUSTED_ORIGINS = [
    EMPLOYEE_SERVICE_PUBLIC_URL,
    'https://localhost:8010',
    'https://127.0.0.1:8010',
]
