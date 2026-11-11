from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core import signing


BNPZID_CODE_SALT = 'employees.bnpzid.code'


def is_bnpzid_enabled() -> bool:
    return bool(getattr(settings, 'BNPZID_ENABLED', True))


def normalize_client_id(value) -> str:
    return str(value or '').strip()


def normalize_redirect_uri(value) -> str:
    return str(value or '').strip()


def get_bnpzid_clients() -> dict:
    clients = getattr(settings, 'BNPZID_CLIENTS', {}) or {}
    return clients if isinstance(clients, dict) else {}


def get_bnpzid_client_config(client_id: str):
    client_id = normalize_client_id(client_id)
    if not client_id:
        return None

    try:
        from .models import BnpzIdClient
        db_client = BnpzIdClient.objects.filter(client_id=client_id, is_active=True).first()
        if db_client is not None:
            return db_client.to_config_dict()
    except Exception:
        pass

    config = get_bnpzid_clients().get(client_id)
    return config if isinstance(config, dict) else None


def is_allowed_redirect_uri(client_config: dict, redirect_uri: str) -> bool:
    redirect_uri = normalize_redirect_uri(redirect_uri)
    allowed_redirects = client_config.get('allowed_redirects') or []
    return redirect_uri in {normalize_redirect_uri(item) for item in allowed_redirects}


def get_bnpzid_authorize_request(data) -> tuple[dict | None, str]:
    client_id = normalize_client_id((data or {}).get('client_id'))
    redirect_uri = normalize_redirect_uri((data or {}).get('redirect_uri'))
    state = str((data or {}).get('state', '')).strip()

    if not client_id and not redirect_uri and not state:
        return None, ''

    if not is_bnpzid_enabled():
        return None, 'bnpzID орқали кириш вақтинча ўчирилган.'

    if not client_id or not redirect_uri:
        return None, 'bnpzID сўрови нотўғри.'

    client_config = get_bnpzid_client_config(client_id)
    if client_config is None:
        return None, 'Номаълум bnpzID мижози.'

    if not is_allowed_redirect_uri(client_config, redirect_uri):
        return None, 'bnpzID учун redirect URI рухсат этилмаган.'

    return {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'state': state,
        'client_name': str(client_config.get('name') or client_id),
    }, ''


def issue_bnpzid_code(*, user, profile, client_id: str, redirect_uri: str) -> str:
    employee = getattr(user, 'employee', None)
    payload = {
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'role': getattr(profile, 'role', 'user') if profile is not None else 'user',
        'employee_slug': getattr(employee, 'slug', '') or '',
        'tabel_number': getattr(employee, 'tabel_number', '') or '',
        'client_id': normalize_client_id(client_id),
        'redirect_uri': normalize_redirect_uri(redirect_uri),
    }
    return signing.dumps(payload, salt=BNPZID_CODE_SALT, compress=True)


def read_bnpzid_code(code: str, *, max_age: int) -> dict:
    return signing.loads(code, salt=BNPZID_CODE_SALT, max_age=max_age)


def append_query_params(url: str, params: dict) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            continue
        query[str(key)] = str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def build_bnpzid_redirect(*, user, profile, client_id: str, redirect_uri: str, state: str) -> str:
    code = issue_bnpzid_code(
        user=user,
        profile=profile,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    return append_query_params(redirect_uri, {
        'bnpzid_code': code,
        'state': state or '',
    })