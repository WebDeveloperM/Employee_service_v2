"""Табелчи API си: бошқа тизим табелчи номидан ўз цехи ходимларининг маълумотларини ўқийди ва янгилайди.

Ҳужжат: API_TIMEKEEPER.md. Манзиллар — /api/v1/timekeeper/ (api_urls.py).

Қандай ишлайди
  1. Табелчи ўз логини ва пароли билан токен олади (POST token/). Токен муддатли, базада фақат хэши сақланади.
  2. Бошқа тизим сўровларга «Authorization: Token <токен>» қўшади.
  3. Ҳар бир сўровда сервер табелчининг цехини қайта аниқлайди (permissions.timekeeper_department): табелчи
     олиб ташланса ёки ходими фаол бўлмай қолса, токен ишламай қолади. Бошқа цех ходимлари «топилмади» (404).
  4. Ёзиш — веб-саҳифадаги «Цех ходимлари» билан бир хил қоидалар (TimekeeperManagedEmployeeSerializer): ҳисоб,
     рол ва цех ўзгармайди, администратор/HR ходимига тегилмайди. Бу ерда яна: фото ва табель рақами ўзгармайди.

Сервис калити (X-Employee-Service-Key) ва веб-сессия бу API да ишламайди: фақат табелчи токени.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import models, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import exceptions, serializers, status
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import (
    EMPLOYEE_STATUS_CHOICES, Employee, EmployeeCategory, PositionCategory, Section, TimekeeperApiToken, WorkSchedule,
    STATUS_INACTIVE, STATUS_WORKING,
)
from .ordering import order_sections_by_code
from .permissions import timekeeper_department
from .sections_tree import SectionIndex, annotate_path_labels, order_depth_first
from .security import client_ip, is_login_locked, register_login_failure, reset_login_failures, security_log
from .serializers import (
    EMPLOYEE_DATE_INPUT_FORMATS, EmployeeCategorySerializer, PositionCategorySerializer, TimekeeperManagedEmployeeSerializer,
    WorkScheduleSerializer,
)
from .staff import StaffTreeOrder

TOKEN_PREFIX = 'tk_'
TOUCH_INTERVAL = timedelta(minutes=1)   # «охирги ишлатилган» вақти ҳар сўровда эмас, дақиқада бир марта ёзилади
STATUS_LABELS = dict(EMPLOYEE_STATUS_CHOICES)


# ---------------------------------------------------------------------------------------------------------------- токен

def hash_token(raw_token):
    return hashlib.sha256(str(raw_token).encode('utf-8')).hexdigest()


def token_lifetime():
    return timedelta(hours=float(getattr(settings, 'TIMEKEEPER_API_TOKEN_TTL_HOURS', 24)))


def issue_token(user, ip=''):
    """Янги токен. Қайтади: (токен матни — фақат шу ерда кўринади, TimekeeperApiToken)."""
    now = timezone.now()
    tokens = TimekeeperApiToken.objects.filter(user=user)
    tokens.filter(expires_at__lte=now).delete()
    # Бир фойдаланувчида фаол токенлар сони чекланган: энг эскилари алмашади (токен чиқиб кетса, ўз-ўзидан тўхтайди).
    keep = max(int(getattr(settings, 'TIMEKEEPER_API_MAX_TOKENS', 5)) - 1, 0)
    stale = list(tokens.order_by('-created_at').values_list('pk', flat=True)[keep:])
    if stale:
        TimekeeperApiToken.objects.filter(pk__in=stale).delete()

    raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = TimekeeperApiToken.objects.create(
        user=user,
        key_hash=hash_token(raw_token),
        auth_stamp=user.get_session_auth_hash(),
        expires_at=now + token_lifetime(),
        created_ip=str(ip or '')[:45],
    )
    return raw_token, token


class TimekeeperTokenAuthentication(BaseAuthentication):
    """«Authorization: Token <токен>». Токен: муддати ўтмаган, ҳисоб фаол, парол токен олингандан бери ўзгармаган."""

    keyword = 'Token'

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed('Authorization сарлавҳаси «Token <токен>» кўринишида бўлиши керак.', code='bad_header')
        try:
            raw_token = parts[1].decode('utf-8')
        except UnicodeError:
            raise exceptions.AuthenticationFailed('Токенда рухсат этилмаган белгилар бор.', code='token_invalid')
        return self.authenticate_credentials(raw_token)

    def authenticate_credentials(self, raw_token):
        token = TimekeeperApiToken.objects.select_related('user').filter(key_hash=hash_token(raw_token)).first()
        if token is None:
            raise exceptions.AuthenticationFailed('Токен нотўғри ёки бекор қилинган.', code='token_invalid')
        now = timezone.now()
        if token.expires_at <= now:
            raise exceptions.AuthenticationFailed('Токен муддати тугаган. Логин ва парол билан янги токен олинг.', code='token_expired')
        user = token.user
        if not user.is_active:
            raise exceptions.AuthenticationFailed('Фойдаланувчи ҳисоби ўчирилган.', code='account_disabled')
        if not constant_time_compare(token.auth_stamp, user.get_session_auth_hash()):
            raise exceptions.AuthenticationFailed('Парол ўзгартирилган. Логин ва парол билан янги токен олинг.', code='password_changed')
        if token.last_used_at is None or now - token.last_used_at > TOUCH_INTERVAL:
            TimekeeperApiToken.objects.filter(pk=token.pk).update(last_used_at=now)
        return user, token

    def authenticate_header(self, request):
        return self.keyword   # жавобда «WWW-Authenticate: Token» билан 401 (403 эмас)


class IsWorkingTimekeeper(BasePermission):
    """Ишлаб турган табелчи: «Табелчилар» саҳифасида цехга тайинланган, ходими фаол, ҳисоби ёқиқ."""

    message = 'Сиз ишлайдиган табелчи эмассиз: ходим «Табелчилар» саҳифасида цехга тайинланмаган ёки ҳисоби/ходими фаол эмас.'
    code = 'not_a_timekeeper'

    def has_permission(self, request, view):
        department = timekeeper_department(request.user)
        if department is None:
            return False
        request.timekeeper_department = department
        return True


# ------------------------------------------------------------------------------------------------------------ умумий қисм

class ErrorCodeMixin:
    """Хато жавобига машина ўқийдиган «code» қўшади (APIView дан олдин келиши керак)."""

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        # {"detail": "...", "code": "token_expired"}: интеграция матнга эмас, code га таяниши мумкин.
        # Майдон хатолари (400) — {майдон: [хабарлар]} — ўзгармайди.
        if isinstance(exc, exceptions.APIException) and not isinstance(exc, exceptions.ValidationError) \
                and isinstance(response.data, dict) and 'detail' in response.data:
            response.data['code'] = exc.get_codes() if isinstance(exc.get_codes(), str) else 'error'
        return response


class TimekeeperApiMixin(ErrorCodeMixin):
    """Барча табелчи API view лари учун: фақат токен, чегара, ишлаб турган табелчи."""

    authentication_classes = [TimekeeperTokenAuthentication]
    permission_classes = [IsWorkingTimekeeper]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'timekeeper_api'


def timekeeper_info(user, department):
    employee = getattr(user, 'employee', None)
    return {
        'employee_id': employee.pk if employee else None,
        'tabel_number': employee.tabel_number if employee else None,
        'full_name': str(employee) if employee else user.get_username(),
        'login': user.get_username(),
        'department': {'id': department.pk, 'name': department.name},
    }


def isoformat(value):
    return timezone.localtime(value).isoformat() if value else None


# --------------------------------------------------------------------------------------------------------- токен олиш/қайтариш

class TokenRequestSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=150, error_messages={'required': 'Логинни юборинг.', 'blank': 'Логинни киритинг.'})
    password = serializers.CharField(
        trim_whitespace=False, max_length=256, error_messages={'required': 'Паролни юборинг.', 'blank': 'Паролни киритинг.'},
    )


class TokenResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    token_type = serializers.CharField()
    expires_at = serializers.DateTimeField()
    expires_in = serializers.IntegerField()
    timekeeper = serializers.DictField()


@extend_schema(
    request=TokenRequestSerializer, responses={200: TokenResponseSerializer}, auth=[],
    summary='Табелчи токенини олиш', tags=['timekeeper'],
    description='Табелчининг логини ва пароли билан муддатли токен. Администратор, HR ва оддий ходимга токен берилмайди.',
)
class TimekeeperTokenView(ErrorCodeMixin, APIView):
    authentication_classes = []        # кириш сўрови: ҳали токен йўқ (нотўғри сарлавҳа уни бузмасин)
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'timekeeper_token'

    def post(self, request):
        serializer = TokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        login = serializer.validated_data['login'].strip()
        password = serializer.validated_data['password']
        ip = client_ip(request)

        # Паролни танлаб топишдан ҳимоя: веб-кириш билан бир хил ҳисоблагичлар (security.py).
        if is_login_locked(login, ip):
            response = Response(
                {'detail': 'Жуда кўп нотўғри уриниш. Бир оздан сўнг қайта уриниб кўринг.', 'code': 'login_locked'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response['Retry-After'] = str(int(getattr(settings, 'LOGIN_LOCKOUT_SECONDS', 900)))
            return response

        user = authenticate(request=request._request, username=login, password=password)   # ўчирилган ҳисоб ҳам None
        if user is None:
            register_login_failure(login, ip)
            # Логин йўқми, парол нотўғрими — бир хил жавоб: логинларни саралаб бўлмасин.
            return Response({'detail': 'Логин ёки парол нотўғри.', 'code': 'invalid_credentials'}, status=status.HTTP_401_UNAUTHORIZED)
        reset_login_failures(login, ip)

        department = timekeeper_department(user)
        if department is None:
            security_log.warning('timekeeper api token refused (not a working timekeeper): user=%r ip=%s', user.get_username(), ip)
            raise exceptions.PermissionDenied(IsWorkingTimekeeper.message, code='not_a_timekeeper')

        raw_token, token = issue_token(user, ip)
        security_log.info('timekeeper api token issued: user=%r department=%r ip=%s', user.get_username(), department.name, ip)
        return Response({
            'token': raw_token,
            'token_type': TimekeeperTokenAuthentication.keyword,
            'expires_at': isoformat(token.expires_at),
            'expires_in': int((token.expires_at - timezone.now()).total_seconds()),
            'timekeeper': timekeeper_info(user, department),
        })


@extend_schema(
    request=None, responses={204: None}, summary='Жорий токенни бекор қилиш (чиқиш)', tags=['timekeeper'],
)
class TimekeeperLogoutView(TimekeeperApiMixin, APIView):
    def post(self, request):
        token = request.auth
        if isinstance(token, TimekeeperApiToken):
            token.delete()
            security_log.info('timekeeper api token revoked: user=%r', request.user.get_username())
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeResponseSerializer(serializers.Serializer):
    timekeeper = serializers.DictField()
    token = serializers.DictField(allow_null=True)


class ReferenceResponseSerializer(serializers.Serializer):
    department = serializers.DictField()
    sections = serializers.ListField(child=serializers.DictField())
    position_categories = serializers.ListField(child=serializers.DictField())
    employee_categories = serializers.ListField(child=serializers.DictField())
    work_schedules = serializers.ListField(child=serializers.DictField())
    statuses = serializers.ListField(child=serializers.DictField())
    genders = serializers.ListField(child=serializers.DictField())


@extend_schema(responses={200: MeResponseSerializer}, summary='Мен кимман: табелчи, цех ва токен муддати', tags=['timekeeper'])
class TimekeeperMeView(TimekeeperApiMixin, APIView):
    def get(self, request):
        token = request.auth
        return Response({
            'timekeeper': timekeeper_info(request.user, request.timekeeper_department),
            'token': {
                'created_at': isoformat(token.created_at),
                'expires_at': isoformat(token.expires_at),
            } if isinstance(token, TimekeeperApiToken) else None,
        })


@extend_schema(
    responses={200: ReferenceResponseSerializer}, tags=['timekeeper'],
    summary='Рўйхатлар: бўлимлар, тоифалар, иш тартиблари, ҳолатлар (PATCH да id ва қийматлар учун)',
)
class TimekeeperReferenceView(TimekeeperApiMixin, APIView):
    def get(self, request):
        department = request.timekeeper_department
        sections = Section.objects.filter(department=department).select_related('department')
        ordered = annotate_path_labels(order_depth_first(order_sections_by_code(sections)))
        return Response({
            'department': {'id': department.pk, 'name': department.name},
            'sections': [
                {'id': section.pk, 'code': section.code, 'name': section.name, 'path': section.path_label, 'parent_id': section.parent_id}
                for section in ordered
            ],
            'position_categories': PositionCategorySerializer(PositionCategory.objects.all(), many=True).data,
            'employee_categories': EmployeeCategorySerializer(EmployeeCategory.objects.all(), many=True).data,
            'work_schedules': WorkScheduleSerializer(WorkSchedule.objects.all(), many=True).data,
            'statuses': [{'value': value, 'label': label} for value, label in EMPLOYEE_STATUS_CHOICES],
            'genders': [{'value': value, 'label': label} for value, label in Employee._meta.get_field('gender').choices],
        })


# ---------------------------------------------------------------------------------------------------------------- ходимлар

class TimekeeperApiEmployeeSerializer(TimekeeperManagedEmployeeSerializer):
    """Ходим: ўқишда — қисқа ва аниқ майдонлар, ёзишда — фақат рўйхатдаги майдонлар (WRITABLE_FIELDS).

    Қоидалар (цех доим табелчиники, ҳисоб/рол ўзгармайди, администратор/HR ходими ёпиқ, бўлим ўз цехидан, ўринбосар
    шу цехдан бўлиши) — TimekeeperManagedEmployeeSerializer ва EmployeeSerializer да, веб-форма билан бир хил.
    """

    tabel_number = serializers.CharField(read_only=True)
    department = serializers.SerializerMethodField()
    section = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    replacement = serializers.SerializerMethodField()

    class Meta(TimekeeperManagedEmployeeSerializer.Meta):
        fields = [
            'id', 'tabel_number', 'slug',
            'first_name', 'last_name', 'surname', 'full_name', 'gender', 'date_of_birth',
            'phone_number_1', 'phone_number_2',
            'department', 'section', 'section_id',
            'position', 'grade', 'date_of_employment', 'date_of_change_position',
            'position_category', 'position_category_id', 'employee_category', 'employee_category_id',
            'work_schedule', 'work_schedule_id',
            'status', 'status_label', 'is_active', 'work_status', 'replacement', 'replacement_employee_id',
            'special_clothing_size', 'clothe_size', 'shoe_size', 'jacket_size', 'tshirt_size',
            'base_image_url', 'base_image_thumb_url',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['slug', 'created_at', 'updated_at']

    def get_department(self, obj) -> dict:
        return {'id': obj.department_id, 'name': obj.department.name} if obj.department_id else None

    def get_section(self, obj) -> dict:
        if not obj.section_id:
            return None
        if 'section_index' not in self.context:
            self.context['section_index'] = SectionIndex()   # рўйхатда бир марта қурилади (ҳар қаторга эмас)
        index = self.context['section_index']
        return {'id': obj.section_id, 'code': obj.section.code, 'name': obj.section.name, 'path': index.path(obj.section_id)}

    def get_status(self, obj) -> str:
        return obj.status_key

    def get_status_label(self, obj) -> str:
        return STATUS_LABELS.get(obj.status_key, '')

    def get_replacement(self, obj) -> dict:
        record = getattr(obj, 'replacement', None) if hasattr(obj, 'replacement') else None
        if record is None:
            return None
        substitute = record.substitute
        return {'id': substitute.pk, 'tabel_number': substitute.tabel_number, 'full_name': str(substitute)}

    def save(self, **kwargs):
        instance = super().save(**kwargs)
        self.context.pop('section_index', None)   # бўлим ўзгарган бўлиши мумкин: кейинги жавобда янги йўл
        return instance


# PATCH да қабул қилинадиган майдонлар (қолгани рад этилади, сабаби билан).
WRITABLE_FIELDS = frozenset({
    'first_name', 'last_name', 'surname', 'gender', 'date_of_birth', 'phone_number_1', 'phone_number_2',
    'section_id', 'position', 'grade', 'date_of_employment', 'date_of_change_position',
    'position_category_id', 'employee_category_id', 'work_schedule_id',
    'status', 'replacement_tabel_number',
    'special_clothing_size', 'clothe_size', 'shoe_size', 'jacket_size', 'tshirt_size',
})
REJECTED_HINTS = {
    'tabel_number': 'Табель рақами ўзгартирилмайди (у ходимнинг калити).',
    'department_id': 'Цехни ўзгартириб бўлмайди: ходим сизнинг цехингизда қолади.',
    'department': 'Цехни ўзгартириб бўлмайди: ходим сизнинг цехингизда қолади.',
    'is_active': '«status» майдонидан фойдаланинг (working, vacation, ..., inactive).',
    'work_status': '«status» майдонидан фойдаланинг (working, vacation, ..., inactive).',
    'replacement_employee_id': '«replacement_tabel_number» майдонидан фойдаланинг (ўринбосарнинг табель рақами).',
    'base_image': 'Фотосуратни бу API орқали ўзгартириб бўлмайди (Face ID эталони): веб-интерфейсдан фойдаланинг.',
    'login': 'Ҳисоб (логин, парол, рол) бу API орқали ўзгармайди.',
    'password': 'Ҳисоб (логин, парол, рол) бу API орқали ўзгармайди.',
    'password_confirm': 'Ҳисоб (логин, парол, рол) бу API орқали ўзгармайди.',
    'role': 'Ҳисоб (логин, парол, рол) бу API орқали ўзгармайди.',
}
UNKNOWN_FIELD_MESSAGE = 'Номаълум ёки ўзгартириш мумкин бўлмаган майдон.'


def employee_queryset(department):
    return (
        Employee.objects
        .select_related('department', 'section', 'user', 'user__profile', 'position_category', 'employee_category',
                        'work_schedule', 'replacement', 'replacement__substitute')
        .filter(is_deleted=False, department=department)
    )


class TimekeeperEmployeePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def apply_status(data):
    """status (working/vacation/.../inactive) -> is_active + work_status: веб-формадаги «Ҳолати» билан бир хил."""
    value = data.pop('status', None)
    if value is None:
        return
    value = str(value).strip()
    if value not in STATUS_LABELS:
        raise serializers.ValidationError({'status': [f'Номаълум ҳолат. Мумкин қийматлар: {", ".join(STATUS_LABELS)}.']})
    data['is_active'] = value != STATUS_INACTIVE
    data['work_status'] = None if value in (STATUS_WORKING, STATUS_INACTIVE) else value


def apply_replacement(data, department, employee):
    """replacement_tabel_number (ёки null) -> replacement_employee_id; ўринбосар шу цехдан бўлиши serializer да текширилади."""
    if 'replacement_tabel_number' not in data:
        return
    tabel = data.pop('replacement_tabel_number')
    if tabel in (None, ''):
        data['replacement_employee_id'] = None
        return
    substitute = Employee.objects.filter(is_deleted=False, department=department, tabel_number=str(tabel).strip()).first()
    if substitute is None:
        raise serializers.ValidationError({'replacement_tabel_number': ['Бундай табель рақамли ходим сизнинг цехингизда топилмади.']})
    data['replacement_employee_id'] = substitute.pk


class TimekeeperEmployeeUpdateSerializer(serializers.Serializer):
    """Фақат ҳужжат учун (Swagger): PATCH да қабул қилинадиган майдонлар. Ҳаммаси ихтиёрий — юборилгани ўзгаради."""

    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    surname = serializers.CharField(required=False, allow_blank=True)
    gender = serializers.ChoiceField(choices=['M', 'F'], required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True, input_formats=EMPLOYEE_DATE_INPUT_FORMATS)
    phone_number_1 = serializers.CharField(required=False, allow_blank=True, max_length=20)
    phone_number_2 = serializers.CharField(required=False, allow_blank=True, max_length=20)
    section_id = serializers.IntegerField(required=False, allow_null=True, help_text='reference/ дан: ўз цехининг бўлими')
    position = serializers.CharField(required=False, allow_blank=True)
    grade = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text='1-2 хонали рақам, охирида ихтиёрий *')
    date_of_employment = serializers.DateField(required=False, allow_null=True, input_formats=EMPLOYEE_DATE_INPUT_FORMATS)
    date_of_change_position = serializers.DateField(required=False, allow_null=True, input_formats=EMPLOYEE_DATE_INPUT_FORMATS)
    position_category_id = serializers.IntegerField(required=False, allow_null=True)
    employee_category_id = serializers.IntegerField(required=False, allow_null=True)
    work_schedule_id = serializers.IntegerField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=list(STATUS_LABELS), required=False)
    replacement_tabel_number = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    special_clothing_size = serializers.CharField(required=False, allow_blank=True)
    clothe_size = serializers.CharField(required=False, allow_blank=True)
    shoe_size = serializers.CharField(required=False, allow_blank=True)
    jacket_size = serializers.CharField(required=False, allow_blank=True)
    tshirt_size = serializers.CharField(required=False, allow_blank=True)


@extend_schema_view(
    get=extend_schema(
        summary='Цех ходимлари рўйхати', tags=['timekeeper'],
        parameters=[
            OpenApiParameter('search', str, description='Исм, фамилия, отасининг исми, табель ёки лавозимдан (сўзлар бўйича)'),
            OpenApiParameter('tabel_number', str, description='Аниқ табель рақами'),
            OpenApiParameter('status', str, description=f'Ҳолат: {", ".join(STATUS_LABELS)}'),
            OpenApiParameter('updated_since', str, description='ISO 8601: шундан кейин ўзгарганлар (масалан 2026-11-25T09:00:00+05:00)'),
            OpenApiParameter('page', int), OpenApiParameter('page_size', int, description='1..200, сукут бўйича 50'),
        ],
    ),
)
class TimekeeperEmployeeListView(TimekeeperApiMixin, ListAPIView):
    """Фақат табелчининг ўз цехи ходимлари (ўчирилганлари йўқ). Тартиб — «Штат бирлиги ва лавозим» саҳифасидагидек."""

    serializer_class = TimekeeperApiEmployeeSerializer
    pagination_class = TimekeeperEmployeePagination

    def get_queryset(self):
        queryset = employee_queryset(self.request.timekeeper_department)
        params = self.request.query_params
        for word in params.get('search', '').split():
            queryset = queryset.filter(
                models.Q(first_name__icontains=word) | models.Q(last_name__icontains=word) | models.Q(surname__icontains=word)
                | models.Q(tabel_number__icontains=word) | models.Q(position__icontains=word)
            )
        tabel_number = params.get('tabel_number', '').strip()
        if tabel_number:
            queryset = queryset.filter(tabel_number=tabel_number)

        status_filter = params.get('status', '').strip()
        if status_filter:
            if status_filter not in STATUS_LABELS:
                raise serializers.ValidationError({'status': [f'Номаълум ҳолат. Мумкин қийматлар: {", ".join(STATUS_LABELS)}.']})
            if status_filter == STATUS_INACTIVE:
                queryset = queryset.filter(is_active=False)
            elif status_filter == STATUS_WORKING:
                queryset = queryset.filter(is_active=True).filter(models.Q(work_status__isnull=True) | models.Q(work_status=''))
            else:
                queryset = queryset.filter(is_active=True, work_status=status_filter)

        updated_since = params.get('updated_since', '').strip()
        if updated_since:
            moment = parse_datetime(updated_since)
            if moment is None:
                raise serializers.ValidationError({'updated_since': ['ISO 8601 вақти юборинг, масалан 2026-11-25T09:00:00+05:00.']})
            if timezone.is_naive(moment):
                moment = timezone.make_aware(moment)
            queryset = queryset.filter(updated_at__gte=moment)
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        ordered_ids = StaffTreeOrder().sorted_ids(queryset)
        page_ids = self.paginate_queryset(ordered_ids)
        by_id = queryset.in_bulk(page_ids)
        serializer = self.get_serializer([by_id[pk] for pk in page_ids if pk in by_id], many=True)
        return self.get_paginated_response(serializer.data)


@extend_schema_view(
    get=extend_schema(summary='Ходим (табель рақами бўйича)', tags=['timekeeper']),
    patch=extend_schema(
        summary='Ходим маълумотини янгилаш (қисман)', tags=['timekeeper'],
        request=TimekeeperEmployeeUpdateSerializer, responses={200: TimekeeperApiEmployeeSerializer},
        description='Фақат юборилган майдонлар ўзгаради. Рўйхатда йўқ майдон юборилса — 400 (сабаби билан).',
    ),
)
class TimekeeperEmployeeDetailView(TimekeeperApiMixin, RetrieveUpdateAPIView):
    serializer_class = TimekeeperApiEmployeeSerializer
    lookup_field = 'tabel_number'
    http_method_names = ['get', 'patch', 'head', 'options']   # PUT йўқ: фақат қисман янгилаш

    def get_queryset(self):
        return employee_queryset(self.request.timekeeper_department)

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            # Бошқа цех ходими ҳам «топилмади»: унинг борлиги билинмасин.
            raise exceptions.NotFound('Ходим топилмади (ёки у сизнинг цехингизга тегишли эмас).', code='not_found')

    def patch(self, request, *args, **kwargs):
        employee = self.get_object()
        payload = request.data
        if not hasattr(payload, 'keys'):
            raise exceptions.ParseError('Майдонлар JSON объекти бўлиши керак: {"майдон": қиймат}.', code='parse_error')
        if not payload:
            raise exceptions.ParseError('Ўзгартириладиган майдон юборилмади.', code='empty_update')
        rejected = {name: [REJECTED_HINTS.get(name, UNKNOWN_FIELD_MESSAGE)] for name in payload.keys() if name not in WRITABLE_FIELDS}
        if rejected:
            raise serializers.ValidationError(rejected)

        data = dict(payload.items())
        apply_status(data)
        apply_replacement(data, request.timekeeper_department, employee)
        serializer = self.get_serializer(employee, data=data, partial=True)
        if not serializer.is_valid():
            errors = dict(serializer.errors)
            if 'replacement_employee_id' in errors:   # мижоз replacement_tabel_number юборган: хато шу номда қайтади
                errors['replacement_tabel_number'] = errors.pop('replacement_employee_id')
            raise serializers.ValidationError(errors)
        with transaction.atomic():
            serializer.save()
        security_log.info(
            'timekeeper api update: user=%r employee=%r fields=%s', request.user.get_username(), employee.tabel_number, sorted(payload.keys()),
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=employee.pk)).data)
