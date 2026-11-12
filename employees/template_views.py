import json
import os
import re
import zipfile
from datetime import date, datetime, timedelta

from django.contrib.auth.models import User as AuthUser
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from .models import Department, Employee, EmployeeCertificate, Section, UserProfile, ROLE_ADMIN, ROLE_HR, ROLE_USER
from .ordering import order_sections_by_code
from .pki import generate_employee_pfx
from .sections_tree import SectionIndex, delete_section_or_400, order_depth_first
from .serializers import DepartmentSerializer, EmployeeSerializer, SectionSerializer, UserProfileSerializer
from .staff import StaffTreeOrder


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class UserManagementAccessMixin:
    def ensure_user_management_access(self):
        profile = getattr(self.request.user, 'profile', None)
        if profile is None or profile.role not in {ROLE_ADMIN, ROLE_HR}:
            raise PermissionDenied('Сизда фойдаланувчиларни бошқариш ҳуқуқи йўқ.')

    def ensure_admin_user_management_access(self):
        profile = getattr(self.request.user, 'profile', None)
        if profile is None or profile.role != ROLE_ADMIN:
            raise PermissionDenied('Сизда фойдаланувчилар рухсатларини бошқариш ҳуқуқи йўқ.')


class DepartmentListCreateTemplateView(generics.ListCreateAPIView):
    queryset = Department.objects.all().order_by('sort_order', 'name')
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return Response({
                'success': True,
                'message': 'Цехлар муваффақиятли олинди',
                'data': serializer.data,
                'count': self.paginator.page.paginator.count,
                'next': self.paginator.get_next_link(),
                'previous': self.paginator.get_previous_link(),
            })

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'success': True,
            'message': 'Цехлар муваффақиятли олинди',
            'data': serializer.data
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response({
            'success': True,
            'message': 'Цех муваффақиятли яратилди',
            'data': serializer.data
        }, status=status.HTTP_201_CREATED, headers=headers)


class DepartmentDetailTemplateView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            'success': True,
            'message': 'Цех муваффақиятли олинди',
            'data': serializer.data
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return Response({
            'success': True,
            'message': 'Цех муваффақиятли янгиланди',
            'data': serializer.data
        })

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({
            'success': True,
            'message': 'Цех муваффақиятли ўчирилди'
        }, status=status.HTTP_204_NO_CONTENT)


class SectionListCreateTemplateView(generics.ListCreateAPIView):
    queryset = Section.objects.select_related('department').all().order_by('department__sort_order', 'department__name', 'name')
    serializer_class = SectionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = super().get_queryset()

        search = str(self.request.query_params.get('search', '') or '').strip()
        department_id = str(self.request.query_params.get('department_id', '') or '').strip()

        if department_id.isdigit():
            queryset = queryset.filter(department_id=int(department_id))

        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search) |
                models.Q(code__icontains=search) |
                models.Q(department__name__icontains=search)
            )

        return order_sections_by_code(queryset)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Все отделы читаются один раз на запрос: глубина, путь и число вложенных без запросов на строку.
        context['section_index'] = getattr(self, 'section_index', None)
        return context

    def list(self, request, *args, **kwargs):
        self.section_index = SectionIndex()
        rows = list(self.filter_queryset(self.get_queryset()))
        # Без поиска показываем дерево: отдел, затем его вложенные. В результатах поиска — плоский список.
        if not str(request.query_params.get('search', '') or '').strip():
            rows = order_depth_first(rows)

        page = self.paginate_queryset(rows)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return Response({
                'success': True,
                'message': 'Бўлимлар муваффақиятли олинди',
                'data': serializer.data,
                'count': self.paginator.page.paginator.count,
                'next': self.paginator.get_next_link(),
                'previous': self.paginator.get_previous_link(),
            })

        serializer = self.get_serializer(rows, many=True)
        return Response({
            'success': True,
            'message': 'Бўлимлар муваффақиятли олинди',
            'data': serializer.data
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response({
            'success': True,
            'message': 'Бўлим муваффақиятли яратилди',
            'data': serializer.data
        }, status=status.HTTP_201_CREATED, headers=headers)


class SectionDetailTemplateView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Section.objects.select_related('department').all()
    serializer_class = SectionSerializer
    permission_classes = [IsAuthenticated]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            'success': True,
            'message': 'Бўлим муваффақиятли олинди',
            'data': serializer.data
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return Response({
            'success': True,
            'message': 'Бўлим муваффақиятли янгиланди',
            'data': serializer.data
        })

    def perform_destroy(self, instance):
        delete_section_or_400(instance)   # вложенные отделы -> понятная ошибка 400, а не 500

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({
            'success': True,
            'message': 'Бўлим муваффақиятли ўчирилди'
        }, status=status.HTTP_204_NO_CONTENT)


class EmployeeListCreateTemplateView(generics.ListCreateAPIView):
    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        # Порядок цех → отдел → должность задаёт StaffTreeOrder в list(); здесь — порядок внутри одного места.
        queryset = Employee.objects.select_related(
            'department', 'section', 'section__department'
        ).filter(is_deleted=False).order_by(
            'last_name',
            'first_name',
            'surname',
            'tabel_number',
        )

        # Қидирув сўзларга бўлинади: ҳар бир сўз исм, фамилия, отасининг исми, табель ёки лавозимдан биронтасида
        # учраши керак. Шунинг учун «Фазилов Акбар» ҳам, «Акбар Фазилов Алишерович» ҳам ходимни топади.
        for word in self.request.query_params.get('search', '').split():
            queryset = queryset.filter(
                models.Q(first_name__icontains=word) |
                models.Q(last_name__icontains=word) |
                models.Q(surname__icontains=word) |
                models.Q(tabel_number__icontains=word) |
                models.Q(position__icontains=word)
            )

        tabel_number = self.request.query_params.get('tabel_number', '').strip()
        if tabel_number:
            queryset = queryset.filter(tabel_number__icontains=tabel_number)

        # Телефон бўйича қидирув: Телефон 1 ёки Телефон 2 дан биронтасида киритилган рақамлар учраши керак.
        # Рақамлар базада турлича сақланган («+998 90 123 45 67», «+998901234567», «901234567»), шунинг учун
        # икки томон ҳам фақат рақамларга келтирилади: «90 123», «+998(90)123» ва «901234567» бир хил ишлайди.
        phone = self.request.query_params.get('phone', '').strip()
        if phone:
            digits = re.sub(r'\D', '', phone)
            if not digits:
                return queryset.none()   # рақамсиз матн ҳеч кимга мос келмайди (рўйхатни жим тўлиқ қайтармаймиз)
            queryset = queryset.annotate(
                phone_1_digits=models.Func(models.F('phone_number_1'), models.Value(r'\D'), models.Value(''), models.Value('g'),
                                           function='regexp_replace', output_field=models.CharField()),
                phone_2_digits=models.Func(models.F('phone_number_2'), models.Value(r'\D'), models.Value(''), models.Value('g'),
                                           function='regexp_replace', output_field=models.CharField()),
            )
            conditions = models.Q(phone_1_digits__contains=digits) | models.Q(phone_2_digits__contains=digits)
            if digits.startswith('998') and len(digits) >= 5:
                # «+998 90 123…» деб ёзилса, давлат кодисиз сақланган «90123…» ҳам топилади.
                local = digits[3:]
                conditions |= models.Q(phone_1_digits__startswith=local) | models.Q(phone_2_digits__startswith=local)
            queryset = queryset.filter(conditions)

        name = self.request.query_params.get('name', '').strip()
        if name:
            queryset = queryset.filter(
                models.Q(first_name__icontains=name)
                | models.Q(last_name__icontains=name)
                | models.Q(surname__icontains=name)
            )

        position = self.request.query_params.get('position', '').strip()
        if position:
            queryset = queryset.filter(position__icontains=position)

        employment_date = self.request.query_params.get('employment_date', '').strip()
        if employment_date:
            queryset = queryset.filter(date_of_employment=employment_date)

        status_filter = self.request.query_params.get('status', '').strip().lower()
        if status_filter == 'active':
            queryset = queryset.filter(is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)

        department_id = self.request.query_params.get('department_id')
        if department_id:
            queryset = queryset.filter(department_id=department_id)

        section_id = self.request.query_params.get('section_id')
        if section_id:
            # Родительский отдел включает сотрудников всех своих вложенных отделов.
            if str(section_id).isdigit():
                queryset = queryset.filter(section_id__in=SectionIndex().subtree(int(section_id)))
            else:
                queryset = queryset.filter(section_id=section_id)

        return queryset

    def employees_in_tree_order(self, ids):
        """Сотрудники по списку id — ровно в этом порядке (одним запросом)."""
        by_id = Employee.objects.select_related('department', 'section', 'section__department').in_bulk(ids)
        return [by_id[employee_id] for employee_id in ids if employee_id in by_id]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        # Таблица идёт в том же порядке, что и «Штат бирлиги ва лавозим»: страницы режутся по уже упорядоченным id.
        ordered_ids = StaffTreeOrder().sorted_ids(queryset)
        page_ids = self.paginate_queryset(ordered_ids)
        if page_ids is not None:
            serializer = self.get_serializer(self.employees_in_tree_order(page_ids), many=True)
            return Response({
                'success': True,
                'message': 'Ходимлар муваффақиятли олинди',
                'data': serializer.data,
                'count': self.paginator.page.paginator.count,
                'next': self.paginator.get_next_link(),
                'previous': self.paginator.get_previous_link(),
            })

        serializer = self.get_serializer(self.employees_in_tree_order(ordered_ids), many=True)
        return Response({
            'success': True,
            'message': 'Ходимлар муваффақиятли олинди',
            'data': serializer.data
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response({
            'success': True,
            'message': 'Ходим муваффақиятли яратилди',
            'data': serializer.data
        }, status=status.HTTP_201_CREATED, headers=headers)


class EmployeeDetailTemplateView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Employee.objects.select_related(
        'department', 'section', 'section__department'
    ).filter(is_deleted=False)
    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            'success': True,
            'message': 'Ходим муваффақиятли олинди',
            'data': serializer.data
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return Response({
            'success': True,
            'message': 'Ходим муваффақиятли янгиланди',
            'data': serializer.data
        })

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        linked_user = instance.user
        linked_profile = getattr(linked_user, 'profile', None) if linked_user is not None else None

        with transaction.atomic():
            if instance.base_image:
                instance.base_image.delete(save=False)
            if linked_profile is not None and linked_profile.face_image:
                linked_profile.face_image.delete(save=False)
            instance.delete()
            if linked_user is not None:
                linked_user.delete()

        return Response({
            'success': True,
            'message': 'Ходим муваффақиятли ўчирилди'
        }, status=status.HTTP_204_NO_CONTENT)


class EmployeeStatsTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        total_employees = Employee.objects.filter(is_deleted=False).count()
        active_employees = Employee.objects.filter(is_deleted=False, is_active=True).count()
        total_departments = Department.objects.count()
        total_sections = Section.objects.count()
        
        return Response({
            'success': True,
            'message': 'Статистика муваффақиятли олинди',
            'data': {
                'total_employees': total_employees,
                'active_employees': active_employees,
                'total_departments': total_departments,
                'total_sections': total_sections,
                'inactive_employees': total_employees - active_employees
            }
        })


class EmployeeCertificateIssueTemplateView(APIView):
    """Issues a brand-new personal .pfx login key for one employee.

    Only admins can issue keys. Returns the .pfx file as the response body
    (for direct browser download) and the one-time password in the
    X-Pfx-Password header, since PKCS#12 files must be password protected
    and this password is never stored anywhere after this response.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        profile = getattr(request.user, 'profile', None)
        if profile is None or profile.role != ROLE_ADMIN:
            raise PermissionDenied('Кириш калитини фақат администратор бера олади.')

        employee = generics.get_object_or_404(Employee.objects.filter(is_deleted=False), pk=pk)
        if not employee.user_id:
            return Response({
                'success': False,
                'message': 'Кириш калити бериш учун аввал ходимга логин киритинг ва карточкани сақланг.',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            issued = generate_employee_pfx(employee)
        except Exception as exc:
            return Response({
                'success': False,
                'message': f'Кириш калитини яратиб бўлмади: {exc}',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        with transaction.atomic():
            EmployeeCertificate.objects.filter(employee=employee, revoked=False).update(
                revoked=True,
                revoked_at=timezone.now(),
            )
            EmployeeCertificate.objects.create(
                employee=employee,
                serial_number=issued['serial_number'],
                fingerprint_sha256=issued['fingerprint_sha256'],
                expires_at=issued['expires_at'],
                issued_by_username=request.user.username,
            )

        filename = f'{employee.tabel_number}.pfx'
        response = HttpResponse(issued['pfx_bytes'], content_type='application/x-pkcs12')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['X-Pfx-Password'] = issued['password']
        response['Access-Control-Expose-Headers'] = 'X-Pfx-Password, Content-Disposition'
        return response


class EmployeeImportExcelTemplateView(APIView):
    permission_classes = [IsAuthenticated]
    max_import_rows = 1000
    supported_image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    header_aliases = {
        'first_name': 'first_name',
        'имя': 'first_name',
        'last_name': 'last_name',
        'фамилия': 'last_name',
        'surname': 'surname',
        'отчество': 'surname',
        'tabel_number': 'tabel_number',
        'табельный_номер': 'tabel_number',
        'табельный': 'tabel_number',
        'gender': 'gender',
        'пол': 'gender',
        'date_of_birth': 'date_of_birth',
        'дата_рождения': 'date_of_birth',
        'special_clothing_size': 'special_clothing_size',
        'спецодежда': 'special_clothing_size',
        'size_special_clothing': 'special_clothing_size',
        'clothe_size': 'clothe_size',
        'размер_одежды': 'clothe_size',
        'халат': 'clothe_size',
        'shoe_size': 'shoe_size',
        'обувь': 'shoe_size',
        'размер_обуви': 'shoe_size',
        'jacket_size': 'jacket_size',
        'куртка': 'jacket_size',
        'tshirt_size': 'tshirt_size',
        'футболка': 'tshirt_size',
        'phone_number_1': 'phone_number_1',
        'телефон_1': 'phone_number_1',
        'телефон1': 'phone_number_1',
        'phone_number_2': 'phone_number_2',
        'телефон_2': 'phone_number_2',
        'телефон2': 'phone_number_2',
        'login': 'login',
        'логин': 'login',
        'password': 'password',
        'пароль': 'password',
        'password_confirm': 'password_confirm',
        'подтверждение_пароля': 'password_confirm',
        'role': 'role',
        'роль': 'role',
        'department_name': 'department_name',
        'цех': 'department_name',
        'boss_full_name': 'boss_full_name',
        'руководитель_цеха': 'boss_full_name',
        'section_name': 'section_name',
        'отдел': 'section_name',
        'position': 'position',
        'должность': 'position',
        'date_of_employment': 'date_of_employment',
        'дата_приема_на_работу': 'date_of_employment',
        'date_of_change_position': 'date_of_change_position',
        'дата_изменения_должности': 'date_of_change_position',
        'requires_face_id_checkout': 'requires_face_id_checkout',
        'требует_face_id': 'requires_face_id_checkout',
        # Узбекские (кириллица) заголовки — те же, что показываются в интерфейсе и в экспорте Excel,
        # чтобы выгруженный файл можно было загрузить обратно. Русские заголовки продолжают работать.
        'исм': 'first_name',
        'отасининг_исми': 'surname',
        'табель_рақами': 'tabel_number',
        'табель': 'tabel_number',
        'жинси': 'gender',
        'туғилган_сана': 'date_of_birth',
        'туғилган_санаси': 'date_of_birth',
        'махсус_кийим': 'special_clothing_size',
        'кийим_ўлчами': 'clothe_size',
        'оёқ_кийим': 'shoe_size',
        'пойабзал': 'shoe_size',
        'парол': 'password',
        'парол_тасдиғи': 'password_confirm',
        'рол': 'role',
        'бўлим': 'section_name',
        'лавозим': 'position',
        'цех_раҳбари': 'boss_full_name',
        'ишга_қабул_қилинган_сана': 'date_of_employment',
        'лавозим_ўзгарган_сана': 'date_of_change_position',
        'face_id_талаб_қилинади': 'requires_face_id_checkout',
    }
    required_fields = ('tabel_number', 'first_name', 'last_name')
    date_fields = {'date_of_birth', 'date_of_employment', 'date_of_change_position'}
    boolean_fields = {'requires_face_id_checkout'}
    string_fields = {
        'first_name', 'last_name', 'surname', 'tabel_number', 'special_clothing_size', 'clothe_size',
        'shoe_size', 'jacket_size', 'tshirt_size', 'phone_number_1', 'phone_number_2', 'login',
        'password', 'password_confirm', 'department_name',
        'section_name', 'boss_full_name', 'position',
    }

    def post(self, request, *args, **kwargs):
        rows = request.data.get('rows')
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except json.JSONDecodeError:
                return Response({
                    'success': False,
                    'message': 'Excel қаторларини ўқиб бўлмади. Файлни қайта импорт қилинг.',
                }, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(rows, list):
            return Response({
                'success': False,
                'message': 'Импорт учун Excel қаторлари массиви кутилмоқда.',
            }, status=status.HTTP_400_BAD_REQUEST)

        if not rows:
            return Response({
                'success': False,
                'message': 'Файл бўш. Камида битта маълумотли қатор қўшинг.',
            }, status=status.HTTP_400_BAD_REQUEST)

        if len(rows) > self.max_import_rows:
            return Response({
                'success': False,
                'message': f'Бир марта импортда {self.max_import_rows} тадан ортиқ қатор юклаб бўлмайди.',
            }, status=status.HTTP_400_BAD_REQUEST)

        uploaded_images, image_errors = self._collect_uploaded_images(request)
        if image_errors:
            return Response({
                'success': False,
                'message': 'Фотосуратлар импорти бажарилмади. Файл номларидаги хатоларни тузатиб, қайта уриниб кўринг.',
                'error_count': len(image_errors),
                'errors': image_errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        row_errors = []
        prepared_rows = []
        seen_tabel_numbers = {}
        used_generated_logins = set()
        unsupported_only_rows = []

        for row_number, raw_row in enumerate(rows, start=2):
            if not isinstance(raw_row, dict):
                row_errors.append({
                    'row': row_number,
                    'message': 'Қатор устунлар ва қийматлар тўпламини ўз ичига олиши керак.',
                })
                continue

            normalized_row = self._normalize_row(raw_row)
            if normalized_row is None:
                continue

            if not normalized_row:
                if self._has_only_unsupported_values(raw_row):
                    unsupported_only_rows.append(row_number)
                    continue
                row_errors.append({
                    'row': row_number,
                    'message': 'Устунларни аниқлаб бўлмади. Импорт йўриқномасидаги сарлавҳалардан фойдаланинг.',
                })
                continue

            missing_fields = [field for field in self.required_fields if not str(normalized_row.get(field, '')).strip()]
            if missing_fields:
                row_errors.append({
                    'row': row_number,
                    'message': 'Мажбурий майдонлар тўлдирилмаган.',
                    'fields': missing_fields,
                })
                continue

            normalized_row, generated_password = self._prepare_system_fields(normalized_row, used_generated_logins)

            tabel_number = str(normalized_row.get('tabel_number', '')).strip()
            if tabel_number:
                seen_tabel_numbers.setdefault(tabel_number, []).append(row_number)

            prepared_rows.append((row_number, normalized_row, generated_password))

        duplicate_rows = [
            {
                'row': row_numbers[0],
                'message': f'Табель рақами {tabel_number} файлда такрорланмоқда.',
                'tabel_number': tabel_number,
                'rows': row_numbers,
            }
            for tabel_number, row_numbers in seen_tabel_numbers.items()
            if len(row_numbers) > 1
        ]
        row_errors.extend(duplicate_rows)

        if not prepared_rows and unsupported_only_rows and not row_errors:
            row_errors.append({
                'row': unsupported_only_rows[0],
                'message': 'Устунларни аниқлаб бўлмади. Импорт йўриқномасидаги сарлавҳалардан фойдаланинг.',
            })

        if row_errors:
            return Response({
                'success': False,
                'message': 'Импорт бажарилмади. Хатоларни тузатиб, қайта уриниб кўринг.',
                'error_count': len(row_errors),
                'errors': row_errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        created_employees = []
        serializer_errors = []
        imported_with_images = 0

        with transaction.atomic():
            for row_number, row_data, generated_password in prepared_rows:
                serializer_input = dict(row_data)
                tabel_number = str(row_data.get('tabel_number', '')).strip()
                image_entry = uploaded_images.get(tabel_number)
                if image_entry is not None:
                    serializer_input['base_image'] = ContentFile(image_entry['bytes'], name=image_entry['name'])

                serializer = EmployeeSerializer(data=serializer_input, context={'request': request})
                if not serializer.is_valid():
                    serializer_errors.append({
                        'row': row_number,
                        'tabel_number': row_data.get('tabel_number', ''),
                        'errors': serializer.errors,
                    })
                    continue

                employee = serializer.save()
                if image_entry is not None and employee.base_image:
                    imported_with_images += 1
                created_employees.append({
                    'id': employee.id,
                    'slug': employee.slug,
                    'tabel_number': employee.tabel_number,
                    'full_name': str(employee),
                    'login': employee.login,
                    'generated_password': generated_password,
                    'role': ROLE_USER,
                })

            if serializer_errors:
                transaction.set_rollback(True)
                return Response({
                    'success': False,
                    'message': 'Импорт бекор қилинди. Айрим қаторлар текширувдан ўтмади.',
                    'error_count': len(serializer_errors),
                    'errors': serializer_errors,
                }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'success': True,
            'message': f'Импорт якунланди. Қўшилган ходимлар: {len(created_employees)}.',
            'created_count': len(created_employees),
            'imported_with_images': imported_with_images,
            'total_rows': len(prepared_rows),
            'employees': created_employees,
        }, status=status.HTTP_201_CREATED)

    def _collect_uploaded_images(self, request):
        uploaded_images = {}
        image_errors = []

        for uploaded_file in request.FILES.getlist('images'):
            file_name = str(getattr(uploaded_file, 'name', '') or '').strip()
            if not file_name:
                continue

            if os.path.splitext(file_name)[1].lower() == '.zip':
                image_errors.extend(self._extract_images_from_archive(uploaded_file, uploaded_images))
                continue

            error = self._store_uploaded_image(uploaded_images, file_name, uploaded_file.read(), getattr(uploaded_file, 'content_type', ''))
            if error:
                image_errors.append(error)

        return uploaded_images, image_errors

    def _extract_images_from_archive(self, uploaded_file, uploaded_images):
        image_errors = []
        try:
            uploaded_file.seek(0)
            with zipfile.ZipFile(uploaded_file) as archive:
                for archive_item in archive.infolist():
                    if archive_item.is_dir():
                        continue

                    original_name = str(archive_item.filename or '').strip()
                    if not original_name:
                        continue

                    with archive.open(archive_item) as archived_file:
                        error = self._store_uploaded_image(uploaded_images, original_name, archived_file.read(), '')
                        if error:
                            image_errors.append(error)
        except zipfile.BadZipFile:
            image_errors.append({
                'message': f'{getattr(uploaded_file, "name", "архив")} файли тўғри ZIP-архив эмас.',
            })

        return image_errors

    def _store_uploaded_image(self, uploaded_images, original_name, image_bytes, content_type):
        image_key, normalized_name = self._build_image_mapping_key(original_name)
        if image_key is None:
            return {
                'message': f'{original_name} файли қўллаб-қувватланмайди. JPG, PNG, WEBP, BMP ёки GIF тасвирларидан фойдаланинг, файл номи табель рақамига тенг бўлиши керак.',
            }

        if image_key in uploaded_images:
            return {
                'message': f'{image_key} номли фотосурат бир нечта марта юкланган.',
            }

        uploaded_images[image_key] = {
            'bytes': image_bytes,
            'name': normalized_name,
            'content_type': content_type,
        }
        return None

    def _build_image_mapping_key(self, file_name):
        normalized_file_name = os.path.basename(str(file_name or '').replace('\\', '/'))
        image_name, extension = os.path.splitext(normalized_file_name)
        if extension.lower() not in self.supported_image_extensions:
            return None, None

        image_key = image_name.strip()
        if not image_key:
            return None, None

        return image_key, f'{image_key}{extension.lower()}'

    def _normalize_row(self, raw_row):
        if not any(self._has_value(value) for value in raw_row.values()):
            return None

        normalized_row = {}
        for raw_header, raw_value in raw_row.items():
            field_name = self.header_aliases.get(self._normalize_header(raw_header))
            if not field_name:
                continue

            parsed_value = self._normalize_value(field_name, raw_value)
            if parsed_value is None:
                continue
            normalized_row[field_name] = parsed_value

        return normalized_row

    def _has_only_unsupported_values(self, raw_row):
        has_unsupported_value = False
        for raw_header, raw_value in raw_row.items():
            if not self._has_value(raw_value):
                continue

            field_name = self.header_aliases.get(self._normalize_header(raw_header))
            if field_name:
                return False

            has_unsupported_value = True

        return has_unsupported_value

    def _prepare_system_fields(self, normalized_row, used_generated_logins):
        generated_password = self._generate_password()
        normalized_row['login'] = self._generate_login(normalized_row, used_generated_logins)
        normalized_row['password'] = generated_password
        normalized_row['password_confirm'] = generated_password
        normalized_row['role'] = ROLE_USER
        return normalized_row, generated_password

    def _normalize_header(self, value):
        normalized = str(value or '').strip().lower().replace('ё', 'е')
        # ўқғҳ — узбекские буквы; без них «Ишга қабул қилинган сана» превращалось бы в «ишга__абул…».
        normalized = re.sub(r'[^a-z0-9а-яўқғҳ]+', '_', normalized)
        return normalized.strip('_')

    def _normalize_value(self, field_name, value):
        if field_name in self.string_fields:
            text_value = str(value or '').strip()
            return text_value or None

        if field_name in self.date_fields:
            return self._normalize_date(value)

        if field_name in self.boolean_fields:
            return self._normalize_bool(value)

        if field_name == 'gender':
            return self._normalize_gender(value)

        if field_name == 'role':
            return self._normalize_role(value)

        if field_name == 'metadata':
            return self._normalize_metadata(value)

        text_value = str(value or '').strip()
        return text_value or None

    def _normalize_date(self, value):
        if value in (None, ''):
            return None

        if isinstance(value, datetime):
            return value.date().isoformat()

        if isinstance(value, date):
            return value.isoformat()

        if isinstance(value, (int, float)):
            excel_epoch = date(1899, 12, 30)
            return (excel_epoch + timedelta(days=int(value))).isoformat()

        text_value = str(value).strip()
        if not text_value:
            return None

        if re.fullmatch(r'\d+(?:\.\d+)?', text_value):
            excel_epoch = date(1899, 12, 30)
            return (excel_epoch + timedelta(days=int(float(text_value)))).isoformat()

        candidate_values = [text_value]
        for separator in ('T', ' '):
            if separator in text_value:
                trimmed_value = text_value.split(separator, 1)[0].strip()
                if trimmed_value and trimmed_value not in candidate_values:
                    candidate_values.append(trimmed_value)

        for candidate_value in candidate_values:
            try:
                return datetime.fromisoformat(candidate_value).date().isoformat()
            except ValueError:
                pass

            for date_format in (
                '%Y-%m-%d',
                '%Y/%m/%d',
                '%Y.%m.%d',
                '%d.%m.%Y',
                '%d.%m.%y',
                '%d/%m/%Y',
                '%d/%m/%y',
                '%d-%m-%Y',
                '%d-%m-%y',
                '%m/%d/%Y',
                '%m/%d/%y',
            ):
                try:
                    return datetime.strptime(candidate_value, date_format).date().isoformat()
                except ValueError:
                    continue

        return text_value

    def _normalize_bool(self, value):
        if value in (None, ''):
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        normalized = str(value).strip().lower().replace('ё', 'е')
        truthy_values = {'1', 'true', 'yes', 'y', 'да', 'активный', 'активен', 'ҳа', 'фаол'}
        falsy_values = {'0', 'false', 'no', 'n', 'нет', 'неактивный', 'не активный', 'йўқ', 'фаол эмас'}
        if normalized in truthy_values:
            return True
        if normalized in falsy_values:
            return False
        return None

    def _normalize_gender(self, value):
        normalized = str(value or '').strip().lower().replace('ё', 'е')
        if not normalized:
            return None
        if normalized in {'m', 'male', 'м', 'муж', 'мужской', 'эркак', 'э'}:
            return 'M'
        # «ё» выше заменено на «е», поэтому «Аёл» приходит сюда как «аел».
        if normalized in {'f', 'female', 'ж', 'жен', 'женский', 'аел', 'а'}:
            return 'F'
        return str(value).strip()

    def _normalize_role(self, value):
        return ROLE_USER

    def _normalize_login_segment(self, value):
        normalized = slugify(str(value or '').strip(), allow_unicode=True).replace('-', '.')
        normalized = re.sub(r'[^\w.@+-]+', '.', normalized, flags=re.UNICODE)
        normalized = re.sub(r'\.+', '.', normalized).strip('.').lower()
        return normalized

    def _login_exists(self, login, used_generated_logins):
        if not login:
            return True
        if login in used_generated_logins:
            return True
        if Employee.objects.filter(login=login).exists():
            return True
        return AuthUser.objects.filter(username=login).exists()

    def _generate_login(self, normalized_row, used_generated_logins):
        first_name_part = self._normalize_login_segment(normalized_row.get('first_name'))
        last_name_part = self._normalize_login_segment(normalized_row.get('last_name'))
        tabel_part = self._normalize_login_segment(normalized_row.get('tabel_number'))

        base_login = '.'.join(part for part in (first_name_part, last_name_part) if part)
        if not base_login:
            base_login = tabel_part or 'user'

        candidate = base_login
        unique_base = base_login
        if self._login_exists(candidate, used_generated_logins):
            unique_base = f'{base_login}.{tabel_part}' if tabel_part else base_login
            candidate = unique_base

        counter = 2
        while self._login_exists(candidate, used_generated_logins):
            candidate = f'{unique_base}.{counter}'
            counter += 1

        used_generated_logins.add(candidate)
        return candidate

    def _generate_password(self):
        allowed_chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
        while True:
            password = get_random_string(10, allowed_chars=allowed_chars)
            if any(char.isalpha() for char in password) and any(char.isdigit() for char in password):
                return password

    def _normalize_metadata(self, value):
        if value in (None, ''):
            return None
        if isinstance(value, dict):
            return value

        text_value = str(value).strip()
        if not text_value:
            return None

        try:
            return json.loads(text_value)
        except json.JSONDecodeError:
            return {'raw': text_value}

    def _has_value(self, value):
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True


class EmployeePhoneImportExcelTemplateView(APIView):
    permission_classes = [IsAuthenticated]
    max_import_rows = 5000

    TABEL_KEYWORDS = ['табел', 'tabel']
    PHONE_KEYWORDS = ['телефон', 'phone']
    DOB_KEYWORDS   = ['дата', 'туғил', 'birth', 'date_of']

    def post(self, request, *args, **kwargs):
        excel_file = request.FILES.get('excel_file')
        if not excel_file:
            return Response({'success': False, 'message': 'Excel файл юкланмади.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            import openpyxl
            wb = openpyxl.load_workbook(excel_file, read_only=True, data_only=True)
            ws = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
            wb.close()
        except Exception as exc:
            return Response({'success': False, 'message': f'Excel файлни ўқиб бўлмади: {exc}'}, status=status.HTTP_400_BAD_REQUEST)

        header_row_idx = None
        for i, row in enumerate(all_rows):
            if any(cell is not None and str(cell).strip() for cell in row):
                header_row_idx = i
                break

        if header_row_idx is None:
            return Response({'success': False, 'message': 'Файл бўш.'}, status=status.HTTP_400_BAD_REQUEST)

        headers = [str(h or '').strip().lower().replace('ё', 'е') for h in all_rows[header_row_idx]]

        def find_col(keywords):
            for i, h in enumerate(headers):
                if any(kw in h for kw in keywords):
                    return i
            return None

        tabel_col = find_col(self.TABEL_KEYWORDS)
        phone_col = find_col(self.PHONE_KEYWORDS)
        dob_col   = find_col(self.DOB_KEYWORDS)

        if tabel_col is None:
            return Response({
                'success': False,
                'message': 'Табель рақами устуни топилмади. Файлда «Табель рақами» сарлавҳали устун бўлиши керак.',
            }, status=status.HTTP_400_BAD_REQUEST)

        data_rows = all_rows[header_row_idx + 1:]
        if len(data_rows) > self.max_import_rows:
            return Response({'success': False, 'message': f'Бир марта импортда {self.max_import_rows} тадан ортиқ қатор юклаб бўлмайди.'}, status=status.HTTP_400_BAD_REQUEST)

        row_errors = []
        prepared = []
        seen_tabel = {}

        for offset, row in enumerate(data_rows):
            row_number = header_row_idx + 2 + offset
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue

            def cell(idx):
                if idx is None or idx >= len(row):
                    return None
                return row[idx]

            tabel = self._parse_tabel(cell(tabel_col))
            if not tabel:
                continue

            phone1 = self._clean_phone(str(cell(phone_col) or ''))
            dob    = self._parse_date(cell(dob_col))

            if not phone1 and not dob:
                row_errors.append({'row': row_number, 'tabel_number': tabel, 'message': 'Камида биттасини киритинг: Телефон 1, Туғилган сана.'})
                continue

            seen_tabel.setdefault(tabel, []).append(row_number)
            prepared.append((row_number, tabel, phone1, dob))

        for tabel, row_numbers in seen_tabel.items():
            if len(row_numbers) > 1:
                row_errors.append({'row': row_numbers[0], 'tabel_number': tabel, 'message': f'Табель рақами {tabel} файлда такрорланмоқда.', 'rows': row_numbers})

        if not prepared:
            msg = 'Импорт бажарилмади. Хатоларни тузатиб, қайта уриниб кўринг.' if row_errors else 'Файлда маълумот топилмади.'
            return Response({'success': False, 'message': msg, 'errors': row_errors}, status=status.HTTP_400_BAD_REQUEST)

        updated = []
        not_found = []
        with transaction.atomic():
            for row_number, tabel, phone1, dob in prepared:
                employee = Employee.objects.filter(tabel_number=tabel, is_deleted=False).first()
                if employee is None:
                    not_found.append({'row': row_number, 'tabel_number': tabel})
                    continue
                update_fields = []
                if phone1:
                    employee.phone_number_1 = phone1
                    update_fields.append('phone_number_1')
                if dob:
                    employee.date_of_birth = dob
                    update_fields.append('date_of_birth')
                if update_fields:
                    employee.save(update_fields=update_fields)
                    updated.append({'tabel_number': tabel})

        return Response({
            'success': True,
            'message': f'Импорт якунланди. Янгиланган ходимлар: {len(updated)}.',
            'updated_count': len(updated),
            'not_found_count': len(not_found),
            'not_found': not_found[:20],
        }, status=status.HTTP_200_OK)

    def _clean_phone(self, value):
        text = re.sub(r'[\s\-\(\)\.]', '', str(value or '').strip())
        return text[:20]

    def _parse_tabel(self, value):
        if value is None:
            return ''
        text = str(value).strip()
        if not text:
            return ''
        try:
            f = float(text)
            if f == int(f):
                return str(int(f))
        except (ValueError, OverflowError):
            pass
        return text

    def _parse_date(self, value):
        if value is None or value == '':
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, (int, float)):
            return date(1899, 12, 30) + timedelta(days=int(value))
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r'\d+(?:\.\d+)?', text):
            return date(1899, 12, 30) + timedelta(days=int(float(text)))
        for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d.%m.%y', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(text.split('T')[0].split(' ')[0].strip(), fmt).date()
            except ValueError:
                continue
        return None


class UserListCreateTemplateView(UserManagementAccessMixin, generics.ListCreateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        self.ensure_user_management_access()
        return UserProfile.objects.select_related('user').order_by('user__username')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                models.Q(user__username__icontains=search)
                | models.Q(user__first_name__icontains=search)
                | models.Q(user__last_name__icontains=search)
            )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return Response({
                'success': True,
                'message': 'Фойдаланувчилар муваффақиятли олинди',
                'data': serializer.data,
                'count': self.paginator.page.paginator.count,
            })

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'success': True,
            'message': 'Фойдаланувчилар муваффақиятли олинди',
            'data': serializer.data,
        })

    def create(self, request, *args, **kwargs):
        self.ensure_user_management_access()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            'success': True,
            'message': 'Фойдаланувчи муваффақиятли яратилди',
            'data': serializer.data,
        }, status=status.HTTP_201_CREATED)


class UserAccessListTemplateView(UserManagementAccessMixin, generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request, *args, **kwargs):
        self.ensure_admin_user_management_access()
        search = request.query_params.get('search', '').strip()
        role_filter = request.query_params.get('role', '').strip().lower()
        if search:
            queryset = Employee.objects.select_related('department', 'section', 'user').filter(is_deleted=False).order_by(
                'last_name', 'first_name', 'surname', 'tabel_number'
            )
            queryset = queryset.filter(
                models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(surname__icontains=search)
                | models.Q(tabel_number__icontains=search)
                | models.Q(login__icontains=search)
                | models.Q(position__icontains=search)
            )

            page = self.paginate_queryset(queryset)
            employees = list(page) if page is not None else list(queryset)

            user_ids = [employee.user_id for employee in employees if employee.user_id]
            profiles = UserProfile.objects.select_related('user').filter(user_id__in=user_ids)
            if role_filter in {ROLE_ADMIN, ROLE_HR}:
                profiles = profiles.filter(role=role_filter)
            profile_map = {profile.user_id: profile for profile in profiles}
            if role_filter in {ROLE_ADMIN, ROLE_HR}:
                employees = [employee for employee in employees if employee.user_id in profile_map]
        else:
            queryset = UserProfile.objects.select_related(
                'user',
                'user__employee',
                'user__employee__department',
                'user__employee__section',
            ).filter(
                role__in=[ROLE_ADMIN, ROLE_HR],
                user__employee__isnull=False,
                user__employee__is_deleted=False,
            ).order_by(
                'user__employee__last_name',
                'user__employee__first_name',
                'user__employee__surname',
                'user__employee__tabel_number',
            )

            if role_filter in {ROLE_ADMIN, ROLE_HR}:
                queryset = queryset.filter(role=role_filter)

            page = self.paginate_queryset(queryset)
            profiles = list(page) if page is not None else list(queryset)
            employees = [profile.user.employee for profile in profiles if getattr(profile.user, 'employee', None) is not None]
            profile_map = {profile.user_id: profile for profile in profiles}

        payload = []
        for employee in employees:
            profile = profile_map.get(employee.user_id)
            profile_role = getattr(profile, 'role', '') or ''
            visible_role = profile_role if profile_role in {ROLE_ADMIN, ROLE_HR} else None
            payload.append({
                'id': employee.id,
                'slug': employee.slug,
                'first_name': employee.first_name,
                'last_name': employee.last_name,
                'surname': employee.surname,
                'full_name': str(employee),
                'tabel_number': employee.tabel_number,
                'login': getattr(getattr(employee, 'user', None), 'username', '') or employee.login,
                'department_name': employee.department.name if employee.department else '',
                'section_name': employee.section.name if employee.section else '',
                'position': employee.position,
                'base_image_url': employee.base_image.url if employee.base_image else None,
                'role': visible_role,
                'profile_id': profile.id if profile else None,
                'has_access': bool(profile),
                'is_active': bool(profile.user.is_active) if profile else bool(employee.is_active),
            })

        if page is not None:
            return Response({
                'success': True,
                'message': 'Ходимлар рухсатлари муваффақиятли олинди',
                'data': payload,
                'count': self.paginator.page.paginator.count,
                'next': self.paginator.get_next_link(),
                'previous': self.paginator.get_previous_link(),
            })

        return Response({
            'success': True,
            'message': 'Ходимлар рухсатлари муваффақиятли олинди',
            'data': payload,
            'count': len(payload),
            'next': None,
            'previous': None,
        })


class UserDetailTemplateView(UserManagementAccessMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserProfile.objects.select_related('user')

    def get_object(self):
        instance = super().get_object()
        profile = getattr(self.request.user, 'profile', None)
        if profile is not None and profile.role in {ROLE_ADMIN, ROLE_HR}:
            return instance
        if instance.user_id != self.request.user.id:
            raise PermissionDenied('Сизда ушбу фойдаланувчини бошқариш ҳуқуқи йўқ.')
        return instance

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            'success': True,
            'message': 'Фойдаланувчи муваффақиятли олинди',
            'data': serializer.data,
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            'success': True,
            'message': 'Фойдаланувчи муваффақиятли янгиланди',
            'data': serializer.data,
        })

    def destroy(self, request, *args, **kwargs):
        self.ensure_user_management_access()
        instance = self.get_object()
        user = instance.user
        employee = getattr(user, 'employee', None)
        if employee is not None:
            employee.user = None
            employee.login = ''
            employee.password_hash = ''
            employee.save(update_fields=['user', 'login', 'password_hash', 'updated_at'])
        instance.delete()
        user.delete()
        return Response({
            'success': True,
            'message': 'Фойдаланувчи муваффақиятли ўчирилди',
        }, status=status.HTTP_200_OK)
