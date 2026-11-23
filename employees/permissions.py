from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated

from .authentication import ServiceApiKeyAuthentication


class ServiceApiKeyReadOnlyPermission(BasePermission):
    message = 'Сервисный API-ключ поддерживает только операции чтения.'

    def has_permission(self, request, view):
        authenticator = getattr(request, 'successful_authenticator', None)
        if isinstance(authenticator, ServiceApiKeyAuthentication) and request.method not in SAFE_METHODS:
            return False
        return True


class ServiceApiAuthenticatedPermission(IsAuthenticated):
    pass


def is_admin_or_hr(user):
    """Суперпользователь (у него может не быть UserProfile) либо роль «Администратор» / «HR менеджер»."""
    # Импорт внутри функции: этот модуль DRF подгружает из настроек
    # (DEFAULT_PERMISSION_CLASSES), возможно до готовности реестра моделей.
    from .models import ROLE_ADMIN, ROLE_HR

    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    return profile is not None and profile.role in {ROLE_ADMIN, ROLE_HR}


def is_admin(user):
    """Суперпользователь либо роль «Администратор»: роли, пароли администраторов и HR меняет только он."""
    from .models import ROLE_ADMIN

    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    return profile is not None and profile.role == ROLE_ADMIN


def is_service_api_request(request):
    """Запрос другой системы по сервисному API-ключу (X-Employee-Service-Key / Bearer)."""
    return isinstance(getattr(request, 'successful_authenticator', None), ServiceApiKeyAuthentication)


class IsAdminOrHrRole(BasePermission):
    """Доступ только для администраторов и HR (как и пункт меню «Настройка»)."""

    message = 'Сизда ушбу бўлимга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        return is_admin_or_hr(request.user)


class AdminOrHrCanWrite(BasePermission):
    """Читать может любой вошедший, менять (POST/PUT/PATCH/DELETE) — только администратор или HR.

    Сервисный ключ сюда не попадает: для него запись режет ServiceApiKeyReadOnlyPermission.
    """

    message = 'Ўзгартириш фақат администратор ёки HR менежерга рухсат этилган.'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS or is_service_api_request(request):
            return True
        return is_admin_or_hr(request.user)


class AdminOrHrOrServiceKey(BasePermission):
    """Администратор, HR или другая система по сервисному ключу (обычный ходим — нет)."""

    message = 'Сизда ушбу маълумотларга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        return is_service_api_request(request) or is_admin_or_hr(request.user)