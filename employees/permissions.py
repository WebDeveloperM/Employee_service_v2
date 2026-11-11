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


class IsAdminOrHrRole(BasePermission):
    """Доступ только для администраторов и HR (как и пункт меню «Настройка»)."""

    message = 'Сизда ушбу бўлимга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        return is_admin_or_hr(request.user)