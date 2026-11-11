from io import BytesIO
import json

from PIL import Image
from django.conf import settings
from django.contrib.auth import login
from django.core import signing
from django.db import models
from django.utils.http import url_has_allowed_host_and_scheme
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import ServiceApiKeyAuthentication
from .permissions import ServiceApiAuthenticatedPermission
from .face import calculate_face_similarity, decode_image_to_pil, detect_face_boxes, estimate_head_pose_from_frames
from .bnpzid import build_bnpzid_redirect, get_bnpzid_client_config, is_allowed_redirect_uri, read_bnpzid_code
from .models import Department, Employee, EmployeeBaseImageChangeLog, Section, UserProfile, ROLE_ADMIN
from .sections_tree import delete_section_or_400
from .serializers import DepartmentSerializer, EmployeeSerializer, EmployeeUpsertSerializer, SectionSerializer


PENDING_FACE_AUTH_USER_ID = 'pending_face_auth_user_id'
PENDING_FACE_AUTH_BACKEND = 'pending_face_auth_backend'
PENDING_FACE_AUTH_NEXT = 'pending_face_auth_next'
PENDING_BNPZID_REQUEST = 'pending_bnpzid_request'


class FaceCaptureRequestSerializer(serializers.Serializer):
    captured_image = serializers.CharField(required=False)
    captured_images = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)
    required_direction = serializers.ChoiceField(required=False, choices=['left', 'right', 'up'])


class FaceVerifyResponseSerializer(serializers.Serializer):
    verified = serializers.BooleanField()
    similarity = serializers.FloatField()
    samples = serializers.IntegerField(required=False)
    threshold = serializers.FloatField(required=False)
    message = serializers.CharField()
    redirect_url = serializers.CharField(required=False)
    required_direction = serializers.CharField(required=False)
    head_pose_passed = serializers.BooleanField(required=False)
    yaw_range = serializers.FloatField(required=False)
    max_right_yaw = serializers.FloatField(required=False)
    max_left_yaw = serializers.FloatField(required=False)


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


def _get_request_value(request, key, default=None):
    data = getattr(request, 'data', None)
    if data is not None:
        value = data.get(key, default)
        if value not in [None, '']:
            return value

    try:
        raw_body = getattr(request, 'body', b'') or b''
        if raw_body:
            parsed = json.loads(raw_body.decode('utf-8'))
            if isinstance(parsed, dict):
                value = parsed.get(key, default)
                if value not in [None, '']:
                    return value
    except Exception:
        pass

    return default


def _collect_face_payloads(request):
    payloads = []
    captured_image = _get_request_value(request, 'captured_image')
    if captured_image:
        payloads.append(captured_image)
    captured_images = _get_request_value(request, 'captured_images')
    if isinstance(captured_images, list):
        payloads.extend(value for value in captured_images if value)
    return payloads


def _load_reference_image(reference_file, error_message):
    reference_bytes = reference_file.read()
    reference_file.close()
    try:
        return Image.open(BytesIO(reference_bytes)).convert('RGB'), None
    except Exception:
        return None, Response({'error': error_message}, status=status.HTTP_400_BAD_REQUEST)


def _resolve_head_pose_result(decoded_images, required_direction):
    if not required_direction:
        return None, None

    if required_direction not in {'left', 'right', 'up'}:
        return None, Response({'error': 'Бошни текшириш йўналиши нотўғри.'}, status=status.HTTP_400_BAD_REQUEST)

    if len(decoded_images) < 3:
        return None, Response({'error': 'Бош бурилишини текшириш учун камида 3 та кадр керак.'}, status=status.HTTP_400_BAD_REQUEST)

    pose_analysis = estimate_head_pose_from_frames(decoded_images[:15])
    if pose_analysis.get('valid_frames', 0) <= 0:
        return None, Response({'error': 'Кадрларда юзни барқарор аниқлаб бўлмади.'}, status=status.HTTP_400_BAD_REQUEST)

    yaw_threshold = float(getattr(settings, 'FACE_ID_HEAD_POSE_YAW_THRESHOLD', 15.0))
    pitch_threshold = float(getattr(settings, 'FACE_ID_HEAD_POSE_PITCH_THRESHOLD', 12.0))
    max_right = float(pose_analysis.get('max_right_yaw', 0.0))
    max_left = float(pose_analysis.get('max_left_yaw', 0.0))
    min_pitch = float(pose_analysis.get('max_up_pitch', 0.0))

    if required_direction == 'right':
        passed = max_right >= yaw_threshold
    elif required_direction == 'left':
        passed = max_left <= -yaw_threshold
    else:
        passed = min_pitch <= -pitch_threshold

    return {
        'required_direction': required_direction,
        'head_pose_passed': passed,
        'yaw_range': round(float(pose_analysis.get('yaw_range', 0.0)), 2),
        'max_right_yaw': round(max_right, 2),
        'max_left_yaw': round(max_left, 2),
    }, None


def _verify_face_payloads(reference_image, payloads, *, success_message, failure_message, required_direction=''):
    if not payloads:
        return None, Response({'error': 'captured_image майдони талаб қилинади'}, status=status.HTTP_400_BAD_REQUEST)

    similarities = []
    decoded_images = []
    for payload in payloads[:15]:
        image = decode_image_to_pil(payload)
        if image is None:
            continue
        decoded_images.append(image)
        try:
            similarities.append(calculate_face_similarity(reference_image, image))
        except ValueError:
            continue

    if not similarities:
        return None, Response({'error': 'Олинган тасвирларда тўғри юз топилмади'}, status=status.HTTP_400_BAD_REQUEST)

    threshold = float(getattr(settings, 'FACE_SIMILARITY_THRESHOLD', 72.0))
    similarity = max(similarities)
    verified = similarity >= threshold
    result = {
        'verified': verified,
        'similarity': round(similarity, 2),
        'samples': len(similarities),
        'threshold': threshold,
    }

    head_pose_result, error_response = _resolve_head_pose_result(decoded_images, required_direction)
    if error_response is not None:
        return None, error_response
    if head_pose_result is not None:
        result.update(head_pose_result)
        result['verified'] = bool(result['verified']) and bool(head_pose_result['head_pose_passed'])

    if result['verified']:
        result['message'] = success_message
    elif head_pose_result is not None and not head_pose_result['head_pose_passed'] and verified:
        direction_labels = {
            'left': 'Бошингизни чапга буринг ва қайта уриниб кўринг.',
            'right': 'Бошингизни ўнгга буринг ва қайта уриниб кўринг.',
            'up': 'Бошингизни юқорироқ кўтаринг ва қайта уриниб кўринг.',
        }
        result['message'] = direction_labels.get(required_direction, failure_message)
    else:
        result['message'] = failure_message

    return result, None


class FaceBoxSerializer(serializers.Serializer):
    x = serializers.IntegerField()
    y = serializers.IntegerField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()


class FaceDetectRequestSerializer(serializers.Serializer):
    captured_image = serializers.CharField()


class FaceDetectResponseSerializer(serializers.Serializer):
    boxes = FaceBoxSerializer(many=True)
    count = serializers.IntegerField()


class FaceIdExemptionEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    external_id = serializers.CharField(allow_blank=True)
    source_system = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    surname = serializers.CharField(allow_blank=True)
    tabel_number = serializers.CharField()
    position = serializers.CharField(allow_blank=True)
    requires_face_id_checkout = serializers.BooleanField()


class FaceIdExemptionListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True)
    previous = serializers.CharField(required=False, allow_null=True)
    employees = FaceIdExemptionEmployeeSerializer(many=True)


class FaceIdExemptionPatchRequestSerializer(serializers.Serializer):
    requires_face_id_checkout = serializers.BooleanField()


class FaceIdExemptionPatchedEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    full_name = serializers.CharField()
    requires_face_id_checkout = serializers.BooleanField()


class FaceIdExemptionPatchResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    employee = FaceIdExemptionPatchedEmployeeSerializer()


class EmployeeBaseImageChangeLogEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_slug = serializers.CharField(allow_blank=True)
    employee_full_name = serializers.CharField(allow_blank=True)
    employee_tabel_number = serializers.CharField(allow_blank=True)
    changed_by_username = serializers.CharField(allow_blank=True)
    changed_by_user_id = serializers.CharField(allow_blank=True)
    changed_by_role = serializers.CharField(allow_blank=True)
    old_image = serializers.CharField(allow_blank=True)
    old_image_url = serializers.CharField(allow_blank=True)
    new_image = serializers.CharField(allow_blank=True)
    new_image_url = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class EmployeeBaseImageChangeLogListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True)
    previous = serializers.CharField(required=False, allow_null=True)
    results = EmployeeBaseImageChangeLogEntrySerializer(many=True)


class BnpzIdExchangeRequestSerializer(serializers.Serializer):
    client_id = serializers.CharField()
    client_secret = serializers.CharField()
    redirect_uri = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField()


class BnpzIdExchangeResponseSerializer(serializers.Serializer):
    username = serializers.CharField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    role = serializers.CharField()
    employee_slug = serializers.CharField(allow_blank=True)
    tabel_number = serializers.CharField(allow_blank=True)


class EmployeeResultsSetPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class EmployeeBaseImageChangeLogPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def get_safe_login_redirect_url(request):
    next_url = request.data.get('next') or request.query_params.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()):
        return next_url
    return settings.LOGIN_REDIRECT_URL


class DepartmentListApiView(generics.ListCreateAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [ServiceApiAuthenticatedPermission]


class DepartmentDetailApiView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [ServiceApiAuthenticatedPermission]


class SectionListApiView(generics.ListCreateAPIView):
    queryset = Section.objects.select_related('department').all()
    serializer_class = SectionSerializer
    permission_classes = [ServiceApiAuthenticatedPermission]


class SectionDetailApiView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Section.objects.select_related('department').all()
    serializer_class = SectionSerializer
    permission_classes = [ServiceApiAuthenticatedPermission]

    def perform_destroy(self, instance):
        delete_section_or_400(instance)   # вложенные отделы -> 400 с пояснением, а не 500


class EmployeeListCreateApiView(generics.ListCreateAPIView):
    serializer_class = EmployeeSerializer
    permission_classes = [ServiceApiAuthenticatedPermission]
    pagination_class = EmployeeResultsSetPagination

    def get_queryset(self):
        queryset = Employee.objects.select_related('department', 'section', 'section__department').filter(is_deleted=False)
        source_system = self.request.query_params.get('source_system')
        tabel_number = self.request.query_params.get('tabel_number')
        external_id = self.request.query_params.get('external_id')
        external_ids_raw = self.request.query_params.get('external_ids', '')
        slugs_raw = self.request.query_params.get('slugs', '')
        search = self.request.query_params.get('search', '').strip()

        external_ids = [value.strip() for value in external_ids_raw.split(',') if value.strip()]
        slugs = [value.strip() for value in slugs_raw.split(',') if value.strip()]

        if source_system:
            queryset = queryset.filter(source_system=source_system)
        if tabel_number:
            queryset = queryset.filter(tabel_number=tabel_number)
        if external_id:
            queryset = queryset.filter(external_id=external_id)
        if external_ids:
            queryset = queryset.filter(external_id__in=external_ids)
        if slugs:
            queryset = queryset.filter(slug__in=slugs)
        if search:
            queryset = queryset.filter(
                models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(surname__icontains=search)
                | models.Q(tabel_number__icontains=search)
                | models.Q(position__icontains=search)
                | models.Q(department__name__icontains=search)
                | models.Q(section__name__icontains=search)
                | models.Q(slug__icontains=search)
            )
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        no_pagination = request.query_params.get('no_pagination', '').strip().lower() == 'true'
        if no_pagination:
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class EmployeeDetailApiView(generics.RetrieveUpdateAPIView):
    queryset = Employee.objects.select_related('department', 'section', 'section__department').all()
    serializer_class = EmployeeSerializer
    permission_classes = [ServiceApiAuthenticatedPermission]
    lookup_field = 'slug'

    self_service_fields = {'special_clothing_size', 'clothe_size', 'shoe_size', 'jacket_size', 'tshirt_size', 'base_image'}

    def get_object(self):
        employee = super().get_object()
        profile = getattr(self.request.user, 'profile', None)
        user_employee = getattr(self.request.user, 'employee', None)

        if profile is not None and profile.role == 'user':
            if user_employee is None or user_employee.pk != employee.pk:
                raise PermissionDenied('Сиз фақат ўз ходим карточкангизни кўра оласиз.')

        return employee

    def update(self, request, *args, **kwargs):
        profile = getattr(request.user, 'profile', None)
        if profile is not None and profile.role == 'user':
            partial = kwargs.pop('partial', False)
            instance = self.get_object()
            payload = request.data.copy()
            payload_keys = {key for key in payload.keys() if key != 'csrfmiddlewaretoken'}
            disallowed_fields = payload_keys - self.self_service_fields
            if disallowed_fields:
                raise PermissionDenied('Оддий ходим фақат «Ўлчамлар» бўлимини ўзгартира олади.')

            serializer = self.get_serializer(instance, data=payload, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)

        return super().update(request, *args, **kwargs)


class EmployeeBaseImageUpdateApiView(APIView):
    queryset = Employee.objects.select_related('department', 'section', 'section__department').all()
    permission_classes = [IsAuthenticated]

    def get_object(self):
        employee = generics.get_object_or_404(self.queryset, slug=self.kwargs.get('slug'))
        authenticator = getattr(self.request, 'successful_authenticator', None)
        if isinstance(authenticator, ServiceApiKeyAuthentication):
            return employee

        profile = getattr(self.request.user, 'profile', None)
        user_employee = getattr(self.request.user, 'employee', None)
        if profile is not None and profile.role == 'user':
            if user_employee is None or user_employee.pk != employee.pk:
                raise PermissionDenied('Сиз асосий тасвирни фақат ўз ходим карточкангизда ўзгартира оласиз.')

        return employee

    def patch(self, request, *args, **kwargs):
        if 'base_image' not in request.FILES:
            return Response({'error': 'base_image файли мажбурий.'}, status=status.HTTP_400_BAD_REQUEST)

        employee = self.get_object()
        old_image_name = employee.base_image.name if employee.base_image else ''

        serializer = EmployeeSerializer(
            employee,
            data={'base_image': request.FILES['base_image']},
            partial=True,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        employee.refresh_from_db()
        actor_user_id = str(request.META.get('HTTP_X_ACTOR_USER_ID', '')).strip()
        actor_username = str(request.META.get('HTTP_X_ACTOR_USERNAME', '')).strip()
        actor_role = str(request.META.get('HTTP_X_ACTOR_ROLE', '')).strip()

        if not actor_username and getattr(request.user, 'is_authenticated', False):
            actor_username = getattr(request.user, 'username', '') or ''
        if not actor_role and getattr(getattr(request.user, 'profile', None), 'role', None):
            actor_role = str(request.user.profile.role)
        if not actor_user_id and getattr(request.user, 'pk', None):
            actor_user_id = str(request.user.pk)

        EmployeeBaseImageChangeLog.objects.create(
            employee=employee,
            employee_full_name=str(employee),
            employee_tabel_number=employee.tabel_number or '',
            changed_by_username=actor_username,
            changed_by_user_id=actor_user_id,
            changed_by_role=actor_role,
            old_image=old_image_name,
            new_image=employee.base_image.name if employee.base_image else '',
        )

        return Response(EmployeeSerializer(employee, context={'request': request}).data)


class EmployeeBaseImageChangeLogListApiView(generics.ListAPIView):
    serializer_class = EmployeeBaseImageChangeLogEntrySerializer
    permission_classes = [ServiceApiAuthenticatedPermission]
    pagination_class = EmployeeBaseImageChangeLogPagination

    def get_queryset(self):
        queryset = EmployeeBaseImageChangeLog.objects.select_related('employee').all()
        search = str(self.request.query_params.get('search', '') or '').strip()
        changed_by_username = str(self.request.query_params.get('changed_by_username', '') or '').strip()
        employee_slug = str(self.request.query_params.get('employee_slug', '') or '').strip()
        date_from = str(self.request.query_params.get('date_from', '') or '').strip()
        date_to = str(self.request.query_params.get('date_to', '') or '').strip()

        if search:
            queryset = queryset.filter(
                models.Q(employee_full_name__icontains=search)
                | models.Q(employee_tabel_number__icontains=search)
                | models.Q(changed_by_username__icontains=search)
                | models.Q(changed_by_role__icontains=search)
                | models.Q(employee__slug__icontains=search)
            )
        if changed_by_username:
            queryset = queryset.filter(changed_by_username__icontains=changed_by_username)
        if employee_slug:
            queryset = queryset.filter(employee__slug=employee_slug)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset

    def _build_image_url(self, value):
        raw = str(value or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://') or raw.startswith('/'):
            return raw
        media_url = str(getattr(settings, 'MEDIA_URL', '/media/') or '/media/')
        return f"{media_url.rstrip('/')}/{raw.lstrip('/')}"

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        records = page if page is not None else queryset

        results = [
            {
                'id': entry.id,
                'employee_slug': entry.employee.slug if entry.employee_id else '',
                'employee_full_name': entry.employee_full_name or str(entry.employee),
                'employee_tabel_number': entry.employee_tabel_number or getattr(entry.employee, 'tabel_number', ''),
                'changed_by_username': entry.changed_by_username,
                'changed_by_user_id': entry.changed_by_user_id,
                'changed_by_role': entry.changed_by_role,
                'old_image': entry.old_image,
                'old_image_url': self._build_image_url(entry.old_image),
                'new_image': entry.new_image,
                'new_image_url': self._build_image_url(entry.new_image),
                'created_at': entry.created_at,
            }
            for entry in records
        ]

        if page is not None:
            return self.get_paginated_response(results)

        return Response({'count': len(results), 'next': None, 'previous': None, 'results': results})


class EmployeeBaseImageChangeLogDetailApiView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk, *args, **kwargs):
        authenticator = getattr(request, 'successful_authenticator', None)
        if isinstance(authenticator, ServiceApiKeyAuthentication):
            actor_role = str(request.META.get('HTTP_X_ACTOR_ROLE', '') or '').strip().lower()
            if actor_role != ROLE_ADMIN:
                raise PermissionDenied('Асосий фото ўзгаришлар журнали ёзувларини фақат администратор ўчира олади.')
        else:
            profile = getattr(request.user, 'profile', None)
            is_admin = bool(getattr(request.user, 'is_superuser', False)) or bool(profile is not None and profile.role == ROLE_ADMIN)
            if not is_admin:
                raise PermissionDenied('Асосий фото ўзгаришлар журнали ёзувларини фақат администратор ўчира олади.')

        entry = generics.get_object_or_404(EmployeeBaseImageChangeLog.objects.all(), pk=pk)
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DepartmentReadOnlyListApiView(DepartmentListApiView):
    http_method_names = ['get', 'head', 'options']


class DepartmentReadOnlyDetailApiView(DepartmentDetailApiView):
    http_method_names = ['get', 'head', 'options']


class SectionReadOnlyListApiView(SectionListApiView):
    http_method_names = ['get', 'head', 'options']


class SectionReadOnlyDetailApiView(SectionDetailApiView):
    http_method_names = ['get', 'head', 'options']


class EmployeeReadOnlyListApiView(EmployeeListCreateApiView):
    http_method_names = ['get', 'head', 'options']


class EmployeeReadOnlyDetailApiView(EmployeeDetailApiView):
    http_method_names = ['get', 'head', 'options']


class EmployeeUpsertApiView(APIView):
    permission_classes = [ServiceApiAuthenticatedPermission]

    @extend_schema(
        request=EmployeeUpsertSerializer,
        responses={200: EmployeeSerializer, 201: EmployeeSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        source_system = str(request.data.get('source_system', 'default')).strip() or 'default'
        external_id = str(request.data.get('external_id', '')).strip()
        tabel_number = str(request.data.get('tabel_number', '')).strip()

        employee = None
        if external_id:
            employee = Employee.objects.filter(source_system=source_system, external_id=external_id).first()
        if employee is None and tabel_number:
            employee = Employee.objects.filter(tabel_number=tabel_number).first()

        serializer = EmployeeUpsertSerializer(employee, data=request.data, partial=employee is not None, context={'request': request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(source_system=source_system, external_id=external_id)
        response_status = status.HTTP_200_OK if employee else status.HTTP_201_CREATED
        return Response(EmployeeSerializer(instance, context={'request': request}).data, status=response_status)


class ProfileFaceVerifyApiView(APIView):
    permission_classes = [ServiceApiAuthenticatedPermission]

    @extend_schema(
        request=FaceCaptureRequestSerializer,
        responses={200: FaceVerifyResponseSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        profile = getattr(request.user, 'profile', None)
        employee = getattr(request.user, 'employee', None)

        reference_file = profile.avatar_image if profile else None
        if reference_file is None and employee and employee.base_image:
            reference_file = employee.base_image

        if reference_file is None:
            return Response({'error': 'Сизда ходимнинг асосий тасвири созланмаган.'}, status=status.HTTP_400_BAD_REQUEST)

        payloads = _collect_face_payloads(request)
        reference_image, error_response = _load_reference_image(reference_file, 'Юзнинг эталон тасвирини ўқиб бўлмади')
        if error_response is not None:
            return error_response

        result, error_response = _verify_face_payloads(
            reference_image,
            payloads,
            success_message='Шахс тасдиқланди',
            failure_message='Шахс тасдиқланмади',
            required_direction=str(_get_request_value(request, 'required_direction', '')).strip().lower(),
        )
        if error_response is not None:
            return error_response
        return Response(result)


class EmployeeFaceVerifyApiView(APIView):
    permission_classes = [ServiceApiAuthenticatedPermission]

    @extend_schema(
        request=FaceCaptureRequestSerializer,
        responses={200: FaceVerifyResponseSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    )
    def post(self, request, slug, *args, **kwargs):
        employee = Employee.objects.filter(slug=slug, is_deleted=False).first()
        if employee is None:
            return Response({'error': 'Ходим топилмади'}, status=status.HTTP_404_NOT_FOUND)

        if not employee.base_image:
            return Response({'error': 'Ходимнинг асосий тасвири йўқ'}, status=status.HTTP_400_BAD_REQUEST)

        payloads = _collect_face_payloads(request)
        reference_image, error_response = _load_reference_image(employee.base_image, 'Ходимнинг асосий тасвирини ўқиб бўлмади')
        if error_response is not None:
            return error_response

        result, error_response = _verify_face_payloads(
            reference_image,
            payloads,
            success_message='Ходим тасдиқланди',
            failure_message='Ходим тасдиқланмади',
            required_direction=str(_get_request_value(request, 'required_direction', '')).strip().lower(),
        )
        if error_response is not None:
            return error_response
        return Response(result)


class FaceDetectBoxesApiView(APIView):
    permission_classes = [ServiceApiAuthenticatedPermission]

    @extend_schema(
        request=FaceDetectRequestSerializer,
        responses={200: FaceDetectResponseSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        payload = request.data.get('captured_image')
        if not payload:
            return Response({'error': 'captured_image майдони талаб қилинади'}, status=status.HTTP_400_BAD_REQUEST)

        image = decode_image_to_pil(payload)
        if image is None:
            return Response({'error': 'Тасвирни декодлаб бўлмади'}, status=status.HTTP_400_BAD_REQUEST)

        boxes = detect_face_boxes(image)
        return Response({'boxes': boxes, 'count': len(boxes)})


class LivenessHeadPoseApiView(APIView):
    permission_classes = [ServiceApiAuthenticatedPermission]

    HEAD_POSE_YAW_THRESHOLD = 15.0
    HEAD_POSE_PITCH_THRESHOLD = 12.0

    def post(self, request, *args, **kwargs):
        required_direction = str(request.data.get('required_direction', '')).lower().strip()
        if required_direction not in ('left', 'right', 'up'):
            return Response(
                {'error': "required_direction must be 'left', 'right', or 'up'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        captured_images_raw = request.data.get('captured_images', [])
        if not isinstance(captured_images_raw, list) or len(captured_images_raw) < 3:
            return Response({'error': 'Minimum 3 frames required'}, status=status.HTTP_400_BAD_REQUEST)

        images = []
        for raw in captured_images_raw[:15]:
            img = decode_image_to_pil(raw)
            if img is not None:
                images.append(img)

        if len(images) < 3:
            return Response({'error': 'Could not decode at least 3 frames'}, status=status.HTTP_400_BAD_REQUEST)

        pose_analysis = estimate_head_pose_from_frames(images)

        if pose_analysis.get('valid_frames', 0) == 0:
            return Response(
                {'error': 'No face detected in frames', 'passed': False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_right = pose_analysis.get('max_right_yaw', 0.0)
        max_left = pose_analysis.get('max_left_yaw', 0.0)
        min_pitch = pose_analysis.get('max_up_pitch', 0.0)

        if required_direction == 'right':
            passed = max_right >= self.HEAD_POSE_YAW_THRESHOLD
        elif required_direction == 'left':
            passed = max_left <= -self.HEAD_POSE_YAW_THRESHOLD
        elif required_direction == 'up':
            passed = min_pitch <= -self.HEAD_POSE_PITCH_THRESHOLD
        else:
            passed = False

        return Response({
            'passed': passed,
            'required_direction': required_direction,
            'max_right_yaw': max_right,
            'max_left_yaw': max_left,
            'yaw_range': pose_analysis.get('yaw_range', 0.0),
            'valid_frames': pose_analysis.get('valid_frames', 0),
            'threshold': self.HEAD_POSE_YAW_THRESHOLD,
        })





@extend_schema_view(
    get=extend_schema(responses={200: FaceIdExemptionListResponseSerializer}),
    patch=extend_schema(
        request=FaceIdExemptionPatchRequestSerializer,
        responses={200: FaceIdExemptionPatchResponseSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    ),
)
class EmployeeFaceIdExemptionApiView(APIView):
    permission_classes = [ServiceApiAuthenticatedPermission]
    pagination_class = EmployeeResultsSetPagination

    @staticmethod
    def _parse_requires_face_id_filter(raw_value):
        value = str(raw_value or '').strip().lower()
        if value in {'true', '1', 'yes'}:
            return True
        if value in {'false', '0', 'no'}:
            return False
        return None

    def get(self, request, *args, **kwargs):
        queryset = Employee.objects.filter(is_deleted=False).order_by('last_name', 'first_name', 'surname', 'tabel_number')
        search = request.query_params.get('search', '').strip()
        requires_face_id_filter = self._parse_requires_face_id_filter(
            request.query_params.get('requires_face_id_checkout')
        )
        if search:
            queryset = queryset.filter(
                models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(surname__icontains=search)
                | models.Q(tabel_number__icontains=search)
                | models.Q(position__icontains=search)
            )
        if requires_face_id_filter is not None:
            queryset = queryset.filter(requires_face_id_checkout=requires_face_id_filter)

        def serialize_employees(employees):
            return [
                {
                    'id': employee.id,
                    'slug': employee.slug,
                    'external_id': employee.external_id,
                    'source_system': employee.source_system,
                    'first_name': employee.first_name,
                    'last_name': employee.last_name,
                    'surname': employee.surname,
                    'tabel_number': employee.tabel_number,
                    'position': employee.position,
                    'requires_face_id_checkout': employee.requires_face_id_checkout,
                }
                for employee in employees
            ]

        no_pagination = request.query_params.get('no_pagination', '').strip().lower() == 'true'
        if no_pagination:
            payload = serialize_employees(queryset)
            return Response({'count': len(payload), 'employees': payload})

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        if page is not None:
            paginated = paginator.get_paginated_response(serialize_employees(page))
            paginated.data['employees'] = paginated.data.pop('results', [])
            return paginated

        payload = serialize_employees(queryset)
        return Response({'count': len(payload), 'employees': payload})

    def patch(self, request, slug, *args, **kwargs):
        employee = Employee.objects.filter(slug=slug, is_deleted=False).first()
        if employee is None:
            return Response({'error': 'Ходим топилмади'}, status=status.HTTP_404_NOT_FOUND)

        if request.data.get('requires_face_id_checkout') is None:
            return Response({'error': 'requires_face_id_checkout майдони талаб қилинади'}, status=status.HTTP_400_BAD_REQUEST)

        employee.requires_face_id_checkout = bool(request.data.get('requires_face_id_checkout'))
        employee.save(update_fields=['requires_face_id_checkout', 'updated_at'])

        return Response({
            'success': True,
            'employee': {
                'id': employee.id,
                'slug': employee.slug,
                'full_name': str(employee),
                'requires_face_id_checkout': employee.requires_face_id_checkout,
            },
        })


class UserLoginFaceVerifyApiView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        request=FaceCaptureRequestSerializer,
        responses={200: FaceVerifyResponseSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        pending_user_id = request.session.get('pending_face_auth_user_id')
        if not pending_user_id:
            return Response({'error': 'Face ID тасдиқлаш сеанси топилмади.'}, status=status.HTTP_400_BAD_REQUEST)

        profile = UserProfile.objects.select_related('user').filter(user_id=pending_user_id).first()
        pending_bnpzid_request = request.session.get(PENDING_BNPZID_REQUEST)
        requires_face_verification = bool(profile and (profile.requires_face_id_login or pending_bnpzid_request))
        if not requires_face_verification:
            return Response({'error': 'Face ID текшируви учун фойдаланувчи топилмади.'}, status=status.HTTP_404_NOT_FOUND)

        reference_file = profile.avatar_image
        if not reference_file:
            return Response({'error': 'Фойдаланувчи учун ходимнинг асосий тасвири созланмаган.'}, status=status.HTTP_400_BAD_REQUEST)

        payloads = _collect_face_payloads(request)
        reference_image, error_response = _load_reference_image(reference_file, 'Ходимнинг асосий тасвирини ўқиб бўлмади')
        if error_response is not None:
            return error_response

        response_data, error_response = _verify_face_payloads(
            reference_image,
            payloads,
            success_message='Face ID тасдиқланди',
            failure_message='Face ID тасдиқланмади',
            required_direction=str(_get_request_value(request, 'required_direction', '')).strip().lower(),
        )
        if error_response is not None:
            return error_response

        if response_data['verified']:
            if pending_bnpzid_request:
                redirect_url = build_bnpzid_redirect(
                    user=profile.user,
                    profile=profile,
                    client_id=pending_bnpzid_request['client_id'],
                    redirect_uri=pending_bnpzid_request['redirect_uri'],
                    state=pending_bnpzid_request.get('state', ''),
                )
            else:
                redirect_url = request.session.get(PENDING_FACE_AUTH_NEXT) or '/'
            backend = request.session.get('pending_face_auth_backend') or 'django.contrib.auth.backends.ModelBackend'
            login(request, profile.user, backend=backend)
            for key in (PENDING_FACE_AUTH_USER_ID, PENDING_FACE_AUTH_BACKEND, PENDING_FACE_AUTH_NEXT, PENDING_BNPZID_REQUEST):
                request.session.pop(key, None)
            response_data['redirect_url'] = redirect_url

        return Response(response_data)


class BnpzIdExchangeApiView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        request=BnpzIdExchangeRequestSerializer,
        responses={200: BnpzIdExchangeResponseSerializer, 400: ErrorResponseSerializer, 403: ErrorResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        if not getattr(settings, 'BNPZID_ENABLED', True):
            return Response({'error': 'bnpzID вход отключён.'}, status=status.HTTP_400_BAD_REQUEST)

        client_id = str(request.data.get('client_id', '')).strip()
        client_secret = str(request.data.get('client_secret', '')).strip()
        redirect_uri = str(request.data.get('redirect_uri', '')).strip()
        code = str(request.data.get('code', '')).strip()

        if not client_id or not client_secret or not code:
            return Response({'error': 'Некорректный запрос обмена bnpzID.'}, status=status.HTTP_400_BAD_REQUEST)

        client_config = get_bnpzid_client_config(client_id)
        if client_config is None:
            return Response({'error': 'Неизвестный клиент bnpzID.'}, status=status.HTTP_400_BAD_REQUEST)

        if client_secret != str(client_config.get('client_secret', '')).strip():
            return Response({'error': 'Некорректный client secret bnpzID.'}, status=status.HTTP_403_FORBIDDEN)

        if redirect_uri and not is_allowed_redirect_uri(client_config, redirect_uri):
            return Response({'error': 'Недопустимый redirect URI для bnpzID.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = read_bnpzid_code(code, max_age=int(getattr(settings, 'BNPZID_CODE_TTL_SECONDS', 120)))
        except signing.SignatureExpired:
            return Response({'error': 'Срок действия кода bnpzID истёк.'}, status=status.HTTP_400_BAD_REQUEST)
        except signing.BadSignature:
            return Response({'error': 'Недействительный код bnpzID.'}, status=status.HTTP_400_BAD_REQUEST)

        if str(payload.get('client_id', '')).strip() != client_id:
            return Response({'error': 'Код bnpzID выпущен для другого клиента.'}, status=status.HTTP_400_BAD_REQUEST)

        issued_redirect_uri = str(payload.get('redirect_uri', '')).strip()
        if redirect_uri and issued_redirect_uri != redirect_uri:
            return Response({'error': 'Код bnpzID выпущен для другого redirect URI.'}, status=status.HTTP_400_BAD_REQUEST)

        username = str(payload.get('username', '')).strip()
        profile = UserProfile.objects.select_related('user').filter(user__username=username).first()
        if profile is not None and not profile.user.is_active:
            return Response({'error': 'Пользователь деактивирован.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'username': username,
            'first_name': str(payload.get('first_name', '')).strip(),
            'last_name': str(payload.get('last_name', '')).strip(),
            'role': str(payload.get('role', 'user')).strip() or 'user',
            'employee_slug': str(payload.get('employee_slug', '')).strip(),
            'tabel_number': str(payload.get('tabel_number', '')).strip(),
        })