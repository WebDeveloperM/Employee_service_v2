from pathlib import Path
import os
import json
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
        raise ImproperlyConfigured('PUBLIC_BASE_URL must include scheme and hostname, for example https://192.168.2.72')
    return value


def build_public_url(base_url: str, port: int) -> str:
    parsed = urlparse(base_url)
    return f'{parsed.scheme}://{parsed.hostname}:{port}'


PUBLIC_BASE_URL = normalize_public_base_url(
    os.environ.get('PUBLIC_BASE_URL', ''),
    'https://192.168.2.72',
)
TB_PROJECT_BACKEND_URL = build_public_url(PUBLIC_BASE_URL, 8050)
TB_PROJECT_FRONTEND_URL = build_public_url(PUBLIC_BASE_URL, 6060)
EMPLOYEE_SERVICE_PUBLIC_URL = build_public_url(PUBLIC_BASE_URL, 5000)
PUBLIC_HOST = urlparse(PUBLIC_BASE_URL).hostname
LEGACY_PUBLIC_HOSTS = ['192.168.2.72']
INTERNAL_ALLOWED_HOSTS = ['host.docker.internal', 'employee-service', *LEGACY_PUBLIC_HOSTS]

SECRET_KEY = os.environ.get('SECRET_KEY', 'employee-service-dev-secret-key')
DEBUG = os.environ.get('EMPLOYEE_SERVICE_DEBUG', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('ALLOWED_HOSTS', '*').split(',') if host.strip()]
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
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'employees.middleware.ClientCertificateAuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

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
    }
}

AUTH_PASSWORD_VALIDATORS = []

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

EMPLOYEE_SERVICE_API_KEYS = [
    value.strip()
    for value in os.environ.get('EMPLOYEE_SERVICE_API_KEYS', 'dev-employee-service-key').split(',')
    if value.strip()
]

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
    BNPZID_CLIENTS = _default_bnpzid_clients

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    TB_PROJECT_FRONTEND_URL,
    f"http://{PUBLIC_HOST}:6060",
]

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = DEBUG

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = [
    EMPLOYEE_SERVICE_PUBLIC_URL,
    'https://localhost:8010',
    'https://127.0.0.1:8010',
]
