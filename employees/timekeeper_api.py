"""Табелчи API лари (бошқа тизим учун). Ҳужжат: API_TIMEKEEPER.md. Манзиллар — /api/v1/ (api_urls.py).

Тизимлар ўртасида иш тақсимоти
  * Табелчини БОШҚА ТИЗИМ тайинлайди ва олиб ташлайди (timekeeper-assignments/). ES да тайинлаш саҳифаси йўқ.
  * Табелчи бошқа тизимда ишлайди (ўз логини билан ўша тизимга киради); бошқа тизим ES га унинг НОМИДАН мурожаат қилади
    (timekeeper/<табелчининг табель рақами>/...): ўз цехи ходимларини ўқийди ва янгилайди.

Хавфсизлик
  * Иккала гуруҳ ҳам ФАҚАТ сервис калити билан (X-Employee-Service-Key ёки Bearer). Табелчининг ES парол/токени йўқ:
    логин ва парол тизимлар ўртасида юрмайди. Калит — бошқа тизимнинг сири (у ходим маълумотини бошқа йўллар билан ҳам ўзгартира олади).
  * «Табелчи номидан» дегани ES да ЧЕКЛОВ: ES шу табель рақамли ходим ҳозир ишлаб турган табелчими (тайинланган, фаол,
    ўчирилмаган) — текширади ва ундан кейин фақат унинг цехи ходимлари, фақат рухсат этилган майдонлар билан ишлайди.
    Цех ва майдонлар чеки калит бор-йўқлигига боғлиқ эмас, ҳар сўровда текширилади.
  * Ёзув қоидалари веб-саҳифадаги «Цех ходимлари» билан бир хил (TimekeeperManagedEmployeeSerializer): ҳисоб, рол ва цех
    ўзгармайди, администратор/HR ходимига тегилмайди. Бу ерда яна: фото ва табель рақами ўзгармайди.
"""
from django.db import models, transaction
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import ServiceApiKeyAuthentication
from .models import (
    Department, EMPLOYEE_STATUS_CHOICES, Employee, EmployeeCategory, PositionCategory, Section, Timekeeper, WorkSchedule,
    STATUS_INACTIVE, STATUS_WORKING,
)
from .ordering import order_sections_by_code
from .permissions import is_service_api_request
from .sections_tree import SectionIndex, annotate_path_labels, order_depth_first
from .security import client_ip, security_log
from .serializers import (
    EMPLOYEE_DATE_INPUT_FORMATS, EmployeeCategorySerializer, PositionCategorySerializer, TimekeeperManagedEmployeeSerializer,
    WorkScheduleSerializer,
)
from .staff import StaffTreeOrder
from .timekeepers import (
    TimekeeperError, active_assignment, assign_timekeeper, assignments_payload, department_assignment, remove_timekeeper,
)

STATUS_LABELS = dict(EMPLOYEE_STATUS_CHOICES)


# ------------------------------------------------------------------------------------------------------------ умумий қисм

class IsServiceKey(BasePermission):
    message = 'Бу API фақат бошқа тизимнинг сервис калити билан ишлайди.'

    def has_permission(self, request, view):
        return is_service_api_request(request)


class ErrorCodeMixin:
    """Хато жавобига машина ўқийдиган «code» қўшади (APIView дан олдин келиши керак)."""

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        # {"detail": "...", "code": "not_a_timekeeper"}: интеграция матнга эмас, code га таяниши мумкин.
        # Майдон хатолари (400) — {майдон: [хабарлар]} — ўзгармайди.
        if isinstance(exc, exceptions.APIException) and not isinstance(exc, exceptions.ValidationError) \
                and isinstance(response.data, dict) and 'detail' in response.data:
            response.data['code'] = exc.get_codes() if isinstance(exc.get_codes(), str) else 'error'
        return response


class ServiceKeyApiMixin(ErrorCodeMixin):
    """Фақат сервис калити. Чегара (throttle) йўқ: калитни сир сақлаш — бошқа тизимнинг иши (веб-кириш чегаралари парол учун)."""

    authentication_classes = [ServiceApiKeyAuthentication]
    permission_classes = [IsServiceKey]

    def get_authenticate_header(self, request):
        # Калит йўқ ёки нотўғри бўлса 401 ва «WWW-Authenticate» (ServiceApiKeyAuthentication ўзи сарлавҳа бермайди — DRF 403 қайтарарди).
        return 'Bearer realm="employee-service"'


class TimekeeperActingMixin(ServiceKeyApiMixin):
    """«Табелчи номидан»: URL даги табель рақами ишлаб турган табелчига тегишли бўлиши шарт, цех шундан олинади."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)     # калит текширилади (401/403 биринчи)
        assignment = active_assignment(kwargs.get('timekeeper_tabel'))
        if assignment is None:
            security_log.warning('timekeeper api refused (not a working timekeeper): tabel=%r ip=%s',
                                 str(kwargs.get('timekeeper_tabel'))[:60], client_ip(request))
            raise exceptions.PermissionDenied(
                'Бу табель рақамли ходим ишлаб турган табелчи эмас: цехга тайинланмаган, ёки фаол эмас/ўчирилган.',
                code='not_a_timekeeper',
            )
        request.timekeeper = assignment
        request.timekeeper_department = assignment.department

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['timekeeper_department'] = self.request.timekeeper_department      # serializer цехни шундан олади
        context['acting_user'] = self.request.timekeeper.employee.user             # ўринбосарни «ким тайинлаган» (ҳисоби бўлса)
        return context


# -------------------------------------------------------------------------------------- табелчи тайинлаш (бошқа тизим)

class AssignmentRequestSerializer(serializers.Serializer):
    tabel_number = serializers.CharField(
        max_length=255, error_messages={'required': 'Табелчи ходимнинг табель рақамини юборинг.', 'blank': 'Табель рақамини киритинг.'},
    )


class AssignmentResponseSerializer(serializers.Serializer):
    department = serializers.DictField()
    timekeeper = serializers.DictField(allow_null=True)
    employee_count = serializers.IntegerField(required=False)


class AssignmentPutResponseSerializer(serializers.Serializer):
    department = serializers.DictField()
    timekeeper = serializers.DictField()
    replaced = serializers.DictField(allow_null=True)
    changed = serializers.BooleanField()


class AssignmentListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = AssignmentResponseSerializer(many=True)


def parse_bool_param(request, name):
    raw = request.query_params.get(name, '').strip().lower()
    if not raw:
        return None
    if raw in ('true', '1'):
        return True
    if raw in ('false', '0'):
        return False
    raise serializers.ValidationError({name: ['true ёки false юборинг.']})


@extend_schema(
    summary='Цехлар ва уларнинг табелчилари', tags=['timekeeper-assignments'], responses={200: AssignmentListResponseSerializer},
    parameters=[
        OpenApiParameter('assigned', bool, description='true — фақат табелчиси борлар, false — табелчиси йўқлар'),
        OpenApiParameter('tabel_number', str, description='Шу табель рақамли ходим табелчи бўлган цех'),
    ],
)
class TimekeeperAssignmentListView(ServiceKeyApiMixin, APIView):
    """Барча цехлар (штат дарахтидаги тартибда). Солиштириш (reconcile) учун: ES да ким табелчи."""

    def get(self, request):
        results = assignments_payload(
            assigned=parse_bool_param(request, 'assigned'), tabel_number=request.query_params.get('tabel_number', '').strip(),
        )
        return Response({'count': len(results), 'results': results})


def get_department_or_404(department_id):
    department = Department.objects.filter(pk=department_id).first()
    if department is None:
        raise exceptions.NotFound('Бундай цех топилмади.', code='department_not_found')
    return department


@extend_schema_view(
    get=extend_schema(summary='Цехнинг табелчиси', tags=['timekeeper-assignments'], responses={200: AssignmentResponseSerializer}),
    put=extend_schema(
        summary='Цехга табелчи тайинлаш (олдингиси алмашади)', tags=['timekeeper-assignments'],
        request=AssignmentRequestSerializer, responses={200: AssignmentPutResponseSerializer},
        description='Идемпотент: ўша ходимни қайта тайинлаш зарарсиз (changed=false).',
    ),
    delete=extend_schema(summary='Цехнинг табелчисини олиб ташлаш', tags=['timekeeper-assignments'], responses={204: None},
                         description='Идемпотент: табелчи бўлмаса ҳам 204.'),
)
class TimekeeperAssignmentDetailView(ServiceKeyApiMixin, APIView):
    def get(self, request, department_id):
        department = get_department_or_404(department_id)
        assignment = Timekeeper.objects.select_related('employee', 'employee__user').filter(department=department).first()
        return Response(department_assignment(department, assignment))

    def put(self, request, department_id):
        department = get_department_or_404(department_id)
        serializer = AssignmentRequestSerializer(data=request.data if hasattr(request.data, 'keys') else {})
        serializer.is_valid(raise_exception=True)
        tabel_number = serializer.validated_data['tabel_number'].strip()
        employee = Employee.objects.select_related('user', 'department').filter(tabel_number=tabel_number, is_deleted=False).first()
        if employee is None:
            raise exceptions.NotFound('Бундай табель рақамли ходим топилмади.', code='employee_not_found')
        try:
            result = assign_timekeeper(department, employee)
        except TimekeeperError as error:
            return Response({'detail': error.message, 'code': error.code, **error.extra}, status=status.HTTP_409_CONFLICT)

        replaced = result.replaced
        security_log.info(
            'timekeeper assigned via api: department=%r timekeeper=%r replaced=%r changed=%s ip=%s',
            department.name, employee.tabel_number, replaced.tabel_number if replaced else None, result.changed, client_ip(request),
        )
        payload = department_assignment(department, result.assignment)
        return Response({
            'department': payload['department'],
            'timekeeper': payload['timekeeper'],
            'replaced': {'tabel_number': replaced.tabel_number, 'full_name': str(replaced)} if replaced is not None else None,
            'changed': result.changed,
        })

    def delete(self, request, department_id):
        department = get_department_or_404(department_id)
        removed = remove_timekeeper(department)
        if removed is not None:
            security_log.info('timekeeper removed via api: department=%r timekeeper=%r ip=%s',
                              department.name, removed.tabel_number, client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------------------------ табелчи номидан: маълумотнома

class ReferenceResponseSerializer(serializers.Serializer):
    department = serializers.DictField()
    sections = serializers.ListField(child=serializers.DictField())
    position_categories = serializers.ListField(child=serializers.DictField())
    employee_categories = serializers.ListField(child=serializers.DictField())
    work_schedules = serializers.ListField(child=serializers.DictField())
    statuses = serializers.ListField(child=serializers.DictField())
    genders = serializers.ListField(child=serializers.DictField())


@extend_schema(summary='Табелчи ва унинг цехи (ишлаб турганини текшириш)', tags=['timekeeper'], responses={200: AssignmentResponseSerializer})
class TimekeeperInfoView(TimekeeperActingMixin, APIView):
    def get(self, request, timekeeper_tabel):
        return Response(department_assignment(request.timekeeper.department, request.timekeeper))


@extend_schema(
    responses={200: ReferenceResponseSerializer}, tags=['timekeeper'],
    summary='Рўйхатлар: бўлимлар, тоифалар, иш тартиблари, ҳолатлар (PATCH да id ва қийматлар учун)',
)
class TimekeeperReferenceView(TimekeeperActingMixin, APIView):
    def get(self, request, timekeeper_tabel):
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
    # EmployeeSerializer бу майдонларни max_length сиз эълон қилган: база эса varchar(20)/(255) — узун қиймат PostgreSQL да 500 берарди.
    surname = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phone_number_1 = serializers.CharField(required=False, allow_blank=True, max_length=20)
    phone_number_2 = serializers.CharField(required=False, allow_blank=True, max_length=20)
    clothe_size = serializers.CharField(required=False, allow_blank=True, max_length=255)
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
class TimekeeperEmployeeListView(TimekeeperActingMixin, ListAPIView):
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
class TimekeeperEmployeeDetailView(TimekeeperActingMixin, RetrieveUpdateAPIView):
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
            'timekeeper api update: timekeeper=%r employee=%r fields=%s ip=%s',
            request.timekeeper.employee.tabel_number, employee.tabel_number, sorted(payload.keys()), client_ip(request),
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=employee.pk)).data)
