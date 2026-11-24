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


def is_timekeeper(user):
    """Роль «Табелчи» (суперпользователь ҳеч қачон табелчи ҳисобланмайди)."""
    from .models import ROLE_TIMEKEEPER

    if not user or not user.is_authenticated or user.is_superuser:
        return False
    profile = getattr(user, 'profile', None)
    return profile is not None and profile.role == ROLE_TIMEKEEPER


def timekeeper_department(user):
    """Табелчининг цехи (Department) ёки None.

    None — табелчи эмас ёки тайинлов ишламайди: ходим ўчирилган/фаол эмас, ёки ҳисоби ўчирилган. Шундай табелчи ҳеч нимани
    кўрмайди (рол сақланиб қолган бўлса ҳам), яъни ходим ишдан чиққач кириш ўз-ўзидан тугайди.
    """
    if not is_timekeeper(user) or not user.is_active:
        return None
    from .models import Timekeeper

    assignment = (
        Timekeeper.objects.select_related('department', 'employee')
        .filter(employee__user_id=user.pk, employee__is_deleted=False, employee__is_active=True)
        .first()
    )
    return assignment.department if assignment is not None else None


class IsAdminOrHrRole(BasePermission):
    """Доступ только для администраторов и HR (как и пункт меню «Настройка»)."""

    message = 'Сизда ушбу бўлимга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        return is_admin_or_hr(request.user)


class AdminOrHrOrTimekeeperRead(BasePermission):
    """«Ходимлар рўйхати» (барча ходимлар): администратор ва HR — тўлиқ; табелчи — фақат ўқиш (GET)."""

    message = 'Сизда ушбу бўлимга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        if is_admin_or_hr(request.user):
            return True
        return request.method in SAFE_METHODS and timekeeper_department(request.user) is not None


class AdminOrHrOrTimekeeperManage(BasePermission):
    """Ходимларни бошқариш (ўқиш, қўшиш, ўзгартириш, ўчириш): администратор ва HR — ҳаммасини; табелчи — фақат ўз цехи.

    Табелчининг доираси ва чекловлари (бошқа цех йўқ, ҳисоб/рол ўзгармайди, администратор/HR ходими тегилмайди) —
    queryset ва TimekeeperManagedEmployeeSerializer да.
    """

    message = 'Сизда ушбу бўлимга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        return is_admin_or_hr(request.user) or timekeeper_department(request.user) is not None


class AdminOrHrCanWrite(BasePermission):
    """Читать может любой вошедший, менять (POST/PUT/PATCH/DELETE) — только администратор или HR.

    Сервисный ключ (TB / ppe_product) тоже пишет: через него TB синхронизирует цеха, отделы и сотрудников.
    """

    message = 'Ўзгартириш фақат администратор ёки HR менежерга рухсат этилган.'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS or is_service_api_request(request):
            return True
        return is_admin_or_hr(request.user)


class AdminOrHrOrTimekeeperCanWrite(AdminOrHrCanWrite):
    """Ходимлар API си: ўқиш — ҳамма, ёзиш — администратор, HR, сервис калити ва (ўз цехи доирасида) табелчи.

    Фақат ходим қўшиш/ўзгартириш API лари учун: цех ва бўлим API ларида AdminOrHrCanWrite қолади.
    """

    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        return timekeeper_department(request.user) is not None


class AdminOrHrOrServiceKey(BasePermission):
    """Администратор, HR или другая система по сервисному ключу (обычный ходим — нет)."""

    message = 'Сизда ушбу маълумотларга кириш ҳуқуқи йўқ.'

    def has_permission(self, request, view):
        return is_service_api_request(request) or is_admin_or_hr(request.user)