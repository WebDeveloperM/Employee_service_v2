import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User as AuthUser
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from .models import Department, Employee, EmployeeCategory, EmployeeCertificate, EmployeeReplacement, PositionCategory, Section, UserProfile, WorkSchedule, ROLE_ADMIN, ROLE_HR, ROLE_USER
from .import_template import COLUMN_LABELS, TEMPLATE_FILE_NAME, build_import_template
from .ordering import order_sections_by_code
from .permissions import AdminOrHrCanWrite, IsAdminOrHrRole, is_admin, is_admin_or_hr
from .pki import generate_employee_pfx
from .replacements import CANDIDATE_LIMIT, substitute_candidates
from .sections_tree import SectionIndex, delete_section_or_400, order_depth_first
from .serializers import (
    DepartmentSerializer, EmployeeCategorySerializer, EmployeeSerializer, PositionCategorySerializer,
    SectionSerializer, UserProfileSerializer, WorkScheduleSerializer,
)
from .staff import StaffTreeOrder
from .thumbnails import thumbnail_url


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
    permission_classes = [IsAuthenticated, AdminOrHrCanWrite]   # ўқиш — ҳамма, ўзгартириш — админ/HR
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


class CategoryListCreateTemplateView(generics.ListCreateAPIView):
    """«Номи» рўйхатлари: рўйхатни барча кирганлар кўради, янги тоифани фақат администратор/HR қўшади."""
    permission_classes = [IsAuthenticated]
    pagination_class = None   # тоифалар кам: селектга бутун рўйхат керак
    list_message = ''
    create_message = ''
    denied_message = ''

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.filter_queryset(self.get_queryset()), many=True)
        return Response({
            'success': True,
            'message': self.list_message,
            'data': serializer.data,
        })

    def create(self, request, *args, **kwargs):
        if not is_admin_or_hr(request.user):
            raise PermissionDenied(self.denied_message)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            'success': True,
            'message': self.create_message,
            'data': serializer.data,
        }, status=status.HTTP_201_CREATED)


class PositionCategoryListCreateTemplateView(CategoryListCreateTemplateView):
    queryset = PositionCategory.objects.all()
    serializer_class = PositionCategorySerializer
    list_message = 'Лавозим тоифалари муваффақиятли олинди'
    create_message = 'Лавозим тоифаси муваффақиятли қўшилди'
    denied_message = 'Лавозим тоифасини фақат администратор ёки HR қўша олади.'


class EmployeeCategoryListCreateTemplateView(CategoryListCreateTemplateView):
    queryset = EmployeeCategory.objects.all()
    serializer_class = EmployeeCategorySerializer
    list_message = 'Ходимлар тоифалари муваффақиятли олинди'
    create_message = 'Ходимлар тоифаси муваффақиятли қўшилди'
    denied_message = 'Ходимлар тоифасини фақат администратор ёки HR қўша олади.'


class WorkScheduleListCreateTemplateView(CategoryListCreateTemplateView):
    queryset = WorkSchedule.objects.all()
    serializer_class = WorkScheduleSerializer
    list_message = 'Иш тартиблари муваффақиятли олинди'
    create_message = 'Иш тартиби муваффақиятли қўшилди'
    denied_message = 'Иш тартибини фақат администратор ёки HR қўша олади.'


class DepartmentDetailTemplateView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated, AdminOrHrCanWrite]   # ўқиш — ҳамма, ўзгартириш — админ/HR

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
    permission_classes = [IsAuthenticated, AdminOrHrCanWrite]   # ўқиш — ҳамма, ўзгартириш — админ/HR
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
    permission_classes = [IsAuthenticated, AdminOrHrCanWrite]   # ўқиш — ҳамма, ўзгартириш — админ/HR

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
    """Ходимларни бошқариш (веб-саҳифа): фақат администратор ва HR."""

    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated, IsAdminOrHrRole]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        # Порядок цех → отдел → должность задаёт StaffTreeOrder в list(); здесь — порядок внутри одного места.
        queryset = Employee.objects.select_related(
            'department', 'section', 'section__department', 'position_category', 'employee_category', 'work_schedule',
            'user', 'user__profile',
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
        by_id = Employee.objects.select_related(
            'department', 'section', 'section__department', 'position_category', 'employee_category', 'work_schedule',
            'user', 'user__profile',
        ).in_bulk(ids)
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
        'department', 'section', 'section__department', 'position_category', 'employee_category', 'work_schedule',
        'user', 'user__profile',
    ).filter(is_deleted=False)
    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated, IsAdminOrHrRole]

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
        # Ходим ўчирилганда унинг фойдаланувчиси ҳам ўчади: HR бу йўл билан администратор ёки бошқа HR ни ўчира олмасин.
        if linked_user is not None and not is_admin(request.user) and (
            linked_user.is_superuser or (linked_profile is not None and linked_profile.role in {ROLE_ADMIN, ROLE_HR})
        ):
            raise PermissionDenied('Администратор ёки HR менежер ходимини фақат администратор ўчира олади.')

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


class EmployeeReplacementCandidatesTemplateView(APIView):
    """Ходимнинг «Ҳолати» ўзгартирилганда очиладиган ойна учун: унинг ўрнига ишлай оладиган, шу цехдаги ходимлар.

    `department_id` — формада танланган цех (ходим бошқа цехга ўтказилаётган бўлиши мумкин); берилмаса — ходимнинг цехи."""

    permission_classes = [IsAuthenticated, IsAdminOrHrRole]

    def get(self, request, pk, *args, **kwargs):
        employee = generics.get_object_or_404(Employee.objects.select_related('department').filter(is_deleted=False), pk=pk)

        department = employee.department
        requested = request.query_params.get('department_id', '').strip()
        if requested.isdigit():
            department = Department.objects.filter(pk=int(requested)).first()
        if department is None:
            return Response({'department': None, 'data': [], 'truncated': False, 'current': None})

        found = list(substitute_candidates(department.pk, exclude_pk=employee.pk)[:CANDIDATE_LIMIT + 1])
        record = EmployeeReplacement.objects.filter(employee=employee).first()
        return Response({
            'department': {'id': department.pk, 'name': department.name},
            'data': [{
                'id': person.pk,
                'full_name': str(person),
                'tabel_number': person.tabel_number,
                'position': person.position,
                'section_name': person.section.name if person.section_id else '',
                'base_image_url': person.base_image.url if person.base_image else None,
                'base_image_thumb_url': thumbnail_url(person.base_image),
            } for person in found[:CANDIDATE_LIMIT]],
            'truncated': len(found) > CANDIDATE_LIMIT,
            'current': record.substitute_id if record is not None else None,
        })


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


class EmployeeImportTemplateDownloadView(APIView):
    """Импорт шаблони (.xlsx): устунлар, рўйхатлар (цех, жинси, тоифалар) ва йўриқнома — import_template.py."""

    permission_classes = [IsAuthenticated, IsAdminOrHrRole]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(
            build_import_template(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = content_disposition_header(True, TEMPLATE_FILE_NAME)
        return response


def _plain_name(value):
    """Номларни солиштириш учун: ортиқча бўшлиқсиз, катта-кичик ҳарфга қарамай, «ё» = «е»."""
    return ' '.join(str(value or '').split()).casefold().replace('ё', 'е')


class _ImportLookups:
    """Цех, бўлим ва рўйхатлар (тоифалар, иш тартиби) номи бўйича: «1-цех », «1-ЦЕХ» ва «1-Цех» — битта цех.

    Илгари цех ва бўлим айнан шу ёзув билан қидирилар эди: ортиқча бўшлиқ ёки ҳарф катталиги фарқ қилса,
    импорт «жим» янги цех яратарди.
    """

    CHOICE_MODELS = (
        ('position_category', PositionCategory),
        ('employee_category', EmployeeCategory),
        ('work_schedule', WorkSchedule),
    )

    def __init__(self):
        self.departments = {}
        for department in Department.objects.all():
            self.departments.setdefault(_plain_name(department.name), department)
        self.sections = {}
        for section in Section.objects.only('id', 'name', 'department_id'):
            self.sections.setdefault((section.department_id, _plain_name(section.name)), section)
        self.existing_department_ids = {department.pk for department in self.departments.values()}
        self.existing_section_ids = {section.pk for section in self.sections.values()}
        self.choices = {}
        self.choice_names = {}
        for field, model in self.CHOICE_MODELS:
            pairs = list(model.objects.values_list('pk', 'name'))
            self.choices[field] = {_plain_name(name): pk for pk, name in pairs}
            self.choice_names[field] = [name for _, name in pairs]

    def department(self, name):
        return self.departments.get(_plain_name(name)) if name else None

    def section(self, department, name):
        if department is None or not name:
            return None
        return self.sections.get((department.pk, _plain_name(name)))

    def remember(self, employee):
        """Шу импортда яратилган цех/бўлим кейинги қаторларда (бошқача ёзилган бўлса ҳам) топилсин."""
        if employee.department_id:
            self.departments.setdefault(_plain_name(employee.department.name), employee.department)
        if employee.section_id:
            self.sections.setdefault((employee.section.department_id, _plain_name(employee.section.name)), employee.section)


class EmployeeImportExcelTemplateView(APIView):
    """Excel импорти. Файлни браузер ўқийди (employees_list.html) ва қаторларни JSON да юборади.

    Икки босқич:
    - `dry_run=1` — бутун файл текширилади, ҳеч нарса сақланмайди: хатолар (қатор ва устун номи билан), янги
      яратиладиган цех/бўлимлар, фотосуратлар. Фотосуратлардан фақат номлари (`image_names`) юборилади.
    - оддий сўров — қаторлар (браузер уларни 50 тадан бўлиб юборади) ва шу қаторларнинг фотосуратлари сақланади.
      Сўров атомар: битта қатор ўтмаса, шу сўровдаги ҳеч ким қўшилмайди.

    Ҳар бир ходимга логин ва парол яратилади. Парол хеши қиммат (PBKDF2, ~0,3 с): у бир марта ҳисобланади (илгари
    ходим ва унинг фойдаланувчиси учун икки марта) ва бир нечта оқимда — 1000 қатор gunicorn timeout'ига тушмасин.
    """

    permission_classes = [IsAuthenticated, IsAdminOrHrRole]
    max_import_rows = 1000          # битта (сақлайдиган) сўровда
    max_check_rows = 5000           # dry_run да бутун файл
    max_reported_errors = 500
    max_hash_threads = 4
    row_number_key = '__row'        # браузер Excel даги ҳақиқий қатор рақамини юборади
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
        'grade': 'grade',
        'разряд': 'grade',
        'position_category': 'position_category',
        'категория_должности': 'position_category',
        'employee_category': 'employee_category',
        'категория_сотрудников': 'employee_category',
        'work_schedule': 'work_schedule',
        'режим_работы': 'work_schedule',
        # Узбекские (кириллица) заголовки — те же, что показываются в интерфейсе, в шаблоне и в экспорте Excel,
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
        'оеқ_кийим': 'shoe_size',   # _normalize_header «ё» ни «е» га алмаштиради, шунинг учун «Оёқ кийим» шу кўринишда келади
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
        'даража_разряд': 'grade',
        'даража': 'grade',
        'лавозим_тоифаси': 'position_category',
        'ходимлар_тоифаси': 'employee_category',
        'иш_тартиби': 'work_schedule',
    }
    required_fields = ('tabel_number', 'last_name', 'first_name')
    date_fields = {'date_of_birth', 'date_of_employment', 'date_of_change_position'}
    boolean_fields = {'requires_face_id_checkout'}
    choice_fields = ('position_category', 'employee_category', 'work_schedule')
    # Логин, парол ва рол файлдан олинмайди: улар автоматик яратилади (рол доим «Фойдаланувчи»).
    system_fields = ('login', 'password', 'password_confirm', 'role')
    string_fields = {
        'first_name', 'last_name', 'surname', 'tabel_number', 'special_clothing_size', 'clothe_size',
        'shoe_size', 'jacket_size', 'tshirt_size', 'phone_number_1', 'phone_number_2', 'login',
        'password', 'password_confirm', 'department_name',
        'section_name', 'boss_full_name', 'position', 'grade', *choice_fields,
    }
    # Базадаги устун узунлиги (PostgreSQL узунроқ қийматни 500 хато билан рад этарди, масалан, битта катакка ёзилган
    # иккита телефон рақами).
    max_lengths = {
        **{field: Employee._meta.get_field(field).max_length for field in (
            'first_name', 'last_name', 'surname', 'tabel_number', 'special_clothing_size', 'clothe_size',
            'shoe_size', 'jacket_size', 'tshirt_size', 'phone_number_1', 'phone_number_2',
        )},
        'department_name': Department._meta.get_field('name').max_length,
        'boss_full_name': Department._meta.get_field('boss_full_name').max_length,
        'section_name': Section._meta.get_field('name').max_length,
    }
    # Excel узун рақамни сон деб сақласа, уни «9.98901E+11» кўринишида қисқартиради.
    scientific_number_re = re.compile(r'\d+(?:[.,]\d+)?[eE][+-]?\d+')
    shortened_number_fields = ('tabel_number', 'phone_number_1', 'phone_number_2')
    ignored_headers = {'', '№', 'n', 'no', 'nn', 't_r', 'т_р'}
    error_field_labels = {
        **COLUMN_LABELS,
        'department_id': COLUMN_LABELS['department_name'],
        'section_id': COLUMN_LABELS['section_name'],
        'position_category_id': COLUMN_LABELS['position_category'],
        'employee_category_id': COLUMN_LABELS['employee_category'],
        'work_schedule_id': COLUMN_LABELS['work_schedule'],
        'boss_full_name': 'Цех раҳбари',
        'base_image': 'Фотосурат',
        'login': 'Логин',
        'non_field_errors': '',
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

        dry_run = str(request.data.get('dry_run', '')).strip().lower() in {'1', 'true', 'yes'}
        row_limit = self.max_check_rows if dry_run else self.max_import_rows
        if len(rows) > row_limit:
            return Response({
                'success': False,
                'message': f'Бир марта импортда {row_limit} тадан ортиқ қатор юклаб бўлмайди.',
            }, status=status.HTTP_400_BAD_REQUEST)

        if dry_run:
            uploaded_images, image_errors, image_warnings = self._index_image_names(request.data.get('image_names'))
        else:
            uploaded_images, image_errors, image_warnings = self._collect_uploaded_images(request)
        if image_errors:
            return Response({
                'success': False,
                'message': 'Фотосуратлар импорти бажарилмади. Файл номларидаги хатоларни тузатиб, қайта уриниб кўринг.',
                'error_count': len(image_errors),
                'errors': image_errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        headers = set()
        for raw_row in rows:
            if isinstance(raw_row, dict):
                headers.update(str(key) for key in raw_row if key != self.row_number_key)
        missing_columns = self._missing_required_columns(headers)
        if missing_columns:
            labels = ', '.join(f'«{COLUMN_LABELS[field]}»' for field in missing_columns)
            return Response({
                'success': False,
                'message': f'Файлда мажбурий устун топилмади: {labels}. Шаблондаги сарлавҳалардан фойдаланинг '
                           '(сарлавҳалар биринчи қаторда бўлсин).',
                'error_count': 1,
                'errors': [{'row': 1, 'message': f'Мажбурий устун топилмади: {labels}.',
                            'fields': list(missing_columns)}],
            }, status=status.HTTP_400_BAD_REQUEST)

        lookups = _ImportLookups()
        row_errors = []
        prepared_rows = []
        seen_tabel_numbers = {}
        unsupported_only_rows = []

        for index, raw_row in enumerate(rows):
            if not isinstance(raw_row, dict):
                row_errors.append({
                    'row': index + 2,
                    'message': 'Қатор устунлар ва қийматлар тўпламини ўз ичига олиши керак.',
                })
                continue

            raw_row = dict(raw_row)
            row_number = self._row_number(raw_row.pop(self.row_number_key, None), index)
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

            for field in self.system_fields:
                normalized_row.pop(field, None)

            tabel_number = normalized_row.get('tabel_number', '')
            if tabel_number:
                # Қаторда бошқа хато бўлса ҳам: такрорланган табель бир йўла кўрсатилсин.
                seen_tabel_numbers.setdefault(tabel_number, []).append(row_number)
            missing_fields = [field for field in self.required_fields if not normalized_row.get(field)]
            if missing_fields:
                row_errors.append({
                    'row': row_number,
                    'tabel_number': tabel_number,
                    'message': 'Мажбурий устунлар тўлдирилмаган: '
                               + ', '.join(COLUMN_LABELS[field] for field in missing_fields) + '.',
                    'fields': missing_fields,
                    'columns': [COLUMN_LABELS[field] for field in missing_fields],
                })
                continue

            problems = self._row_problems(normalized_row, lookups)
            for field, message in problems:
                row_errors.append(self._error(row_number, tabel_number, field, message))
            if problems:
                continue

            prepared_rows.append((row_number, normalized_row))

        for tabel_number, row_numbers in seen_tabel_numbers.items():
            if len(row_numbers) > 1:
                row_errors.append({
                    'row': row_numbers[0],
                    'message': f'Табель рақами {tabel_number} файлда такрорланмоқда (қаторлар: '
                               + ', '.join(str(number) for number in row_numbers) + ').',
                    'tabel_number': tabel_number,
                    'column': COLUMN_LABELS['tabel_number'],
                    'rows': row_numbers,
                })

        row_errors.extend(self._existing_tabel_errors(prepared_rows))

        if not prepared_rows and unsupported_only_rows and not row_errors:
            row_errors.append({
                'row': unsupported_only_rows[0],
                'message': 'Устунларни аниқлаб бўлмади. Импорт йўриқномасидаги сарлавҳалардан фойдаланинг.',
            })

        if not prepared_rows and not row_errors:
            return Response({
                'success': False,
                'message': 'Файл бўш. Камида битта маълумотли қатор қўшинг.',
            }, status=status.HTTP_400_BAD_REQUEST)

        if row_errors:
            return self._errors_response('Импорт бажарилмади. Хатоларни тузатиб, қайта уриниб кўринг.', row_errors)

        if dry_run:
            return self._check(request, prepared_rows, lookups, uploaded_images, headers, image_warnings)
        return self._import(request, prepared_rows, lookups, uploaded_images, image_warnings)

    # ── Текширув (dry_run) ва сақлаш ──────────────────────────────────────────────────────────────────────

    def _check(self, request, prepared_rows, lookups, uploaded_images, headers, image_warnings):
        serializer_errors = []
        for row_number, row in prepared_rows:
            serializer = EmployeeSerializer(data=self._serializer_input(row, lookups), context={'request': request})
            if not serializer.is_valid():
                serializer_errors.extend(self._serializer_errors(row_number, row, serializer.errors))
        if serializer_errors:
            return self._errors_response('Импорт бажарилмади. Хатоларни тузатиб, қайта уриниб кўринг.', serializer_errors)

        new_departments = {}
        new_sections = {}
        for _, row in prepared_rows:
            department_name = row.get('department_name')
            if not department_name:
                continue
            department = lookups.department(department_name)
            if department is None:
                new_departments.setdefault(_plain_name(department_name), department_name)
            section_name = row.get('section_name')
            if section_name and lookups.section(department, section_name) is None:
                shown_department = department.name if department is not None else new_departments[_plain_name(department_name)]
                new_sections.setdefault(
                    (_plain_name(department_name), _plain_name(section_name)),
                    {'department': shown_department, 'section': section_name},
                )

        tabel_numbers = {row['tabel_number'] for _, row in prepared_rows}
        unmatched_images = sorted(key for key in uploaded_images if key not in tabel_numbers)
        warnings = list(image_warnings)
        unknown_headers = sorted(header for header in headers if self._is_unknown_header(header))
        if unknown_headers:
            warnings.append('Бу устунлар импорт қилинмайди ва ўтказиб юборилади: ' + ', '.join(unknown_headers) + '.')
        if unmatched_images:
            shown = ', '.join(unmatched_images[:10]) + (f' ва яна {len(unmatched_images) - 10} та' if len(unmatched_images) > 10 else '')
            warnings.append(f'Табель рақами файлда йўқ фотосуратлар юкланмайди: {shown}.')

        total = len(prepared_rows)
        return Response({
            'success': True,
            'dry_run': True,
            'message': f'Файл текширувдан ўтди: {total} та ходим импортга тайёр.',
            'total_rows': total,
            'images_matched': len(tabel_numbers & set(uploaded_images)),
            'new_departments': list(new_departments.values()),
            'new_sections': list(new_sections.values()),
            'warnings': warnings,
        })

    def _import(self, request, prepared_rows, lookups, uploaded_images, image_warnings):
        passwords = [self._generate_password() for _ in prepared_rows]
        password_hashes = self._hash_passwords(passwords)   # транзакциядан олдин: базани қулфлаб турмасин
        taken_logins = self._taken_logins()

        created_employees = []
        serializer_errors = []
        warnings = list(image_warnings)
        imported_with_images = 0
        created_departments = {}
        created_sections = {}

        with transaction.atomic():
            for (row_number, row), password, password_hash in zip(prepared_rows, passwords, password_hashes):
                serializer_input = self._serializer_input(row, lookups)
                serializer_input['login'] = self._generate_login(row, taken_logins)
                tabel_number = row['tabel_number']
                image_entry = uploaded_images.get(tabel_number)
                if image_entry is not None:
                    serializer_input['base_image'] = ContentFile(image_entry['bytes'], name=image_entry['name'])

                serializer = EmployeeSerializer(data=serializer_input, context={'request': request})
                valid = serializer.is_valid()
                if not valid and image_entry is not None and set(serializer.errors) == {'base_image'}:
                    # Бузилган фото бутун импортни тўхтатмасин: ходим фотосиз қўшилади, огоҳлантириш чиқади.
                    warnings.append(f'Қатор {row_number}: {tabel_number} фотосурати очилмади — ходим фотосиз қўшилди.')
                    serializer_input.pop('base_image')
                    image_entry = None
                    serializer = EmployeeSerializer(data=serializer_input, context={'request': request})
                    valid = serializer.is_valid()
                if not valid:
                    serializer_errors.extend(self._serializer_errors(row_number, row, serializer.errors))
                    continue

                employee = serializer.save()
                self._set_password_hash(employee, password_hash)
                lookups.remember(employee)
                if employee.department_id and employee.department_id not in lookups.existing_department_ids:
                    created_departments.setdefault(employee.department_id, employee.department.name)
                if employee.section_id and employee.section_id not in lookups.existing_section_ids:
                    created_sections.setdefault(employee.section_id, {
                        'department': employee.department.name if employee.department_id else '',
                        'section': employee.section.name,
                    })
                if image_entry is not None and employee.base_image:
                    imported_with_images += 1
                created_employees.append({
                    'id': employee.id,
                    'slug': employee.slug,
                    'tabel_number': employee.tabel_number,
                    'full_name': str(employee),
                    'login': employee.login,
                    'generated_password': password,
                    'role': ROLE_USER,
                })

            if serializer_errors:
                transaction.set_rollback(True)
                return self._errors_response('Импорт бекор қилинди. Айрим қаторлар текширувдан ўтмади.', serializer_errors)

        return Response({
            'success': True,
            'message': f'Импорт якунланди. Қўшилган ходимлар: {len(created_employees)}.',
            'created_count': len(created_employees),
            'imported_with_images': imported_with_images,
            'total_rows': len(prepared_rows),
            'employees': created_employees,
            'created_departments': list(created_departments.values()),
            'created_sections': list(created_sections.values()),
            'warnings': warnings,
        }, status=status.HTTP_201_CREATED)

    def _serializer_input(self, row, lookups):
        data = {field: value for field, value in row.items() if field not in self.choice_fields}
        for field in self.choice_fields:
            if field in row:
                data[f'{field}_id'] = lookups.choices[field][_plain_name(row[field])]
        # Мавжуд цех/бўлим базадаги ёзуви билан берилади: сериализатор уларни айнан шу ном билан топади.
        department = lookups.department(row.get('department_name'))
        if department is not None:
            data['department_name'] = department.name
            section = lookups.section(department, row.get('section_name'))
            if section is not None:
                data['section_name'] = section.name
        data['role'] = ROLE_USER
        return data

    def _set_password_hash(self, employee, password_hash):
        """Бир марта ҳисобланган хеш ходимга ҳам, унинг фойдаланувчисига ҳам ёзилади (иккаласи бир хил форматда)."""
        Employee.objects.filter(pk=employee.pk).update(password_hash=password_hash)
        employee.password_hash = password_hash
        if employee.user_id:
            AuthUser.objects.filter(pk=employee.user_id).update(password=password_hash)
            employee.user.password = password_hash

    def _hash_passwords(self, passwords):
        # PBKDF2 (hashlib) GIL ни қўйиб юборади: бир нечта оқимда хешлаш процессор ядролари сонига қараб тезлашади.
        workers = min(self.max_hash_threads, len(passwords), os.cpu_count() or 1)
        if workers < 2:
            return [make_password(password) for password in passwords]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(make_password, passwords))

    # ── Қатор текшируви ────────────────────────────────────────────────────────────────────────────────────

    def _row_problems(self, row, lookups):
        """Қатордаги хатолар: [(майдон, хабар)]. Хабар ўзбекча ва нимани тузатишни айтади."""
        problems = []
        for field in self.shortened_number_fields:
            value = row.get(field)
            if value and self.scientific_number_re.fullmatch(value):
                problems.append((field, f'Excel рақамни қисқартириб юборган ({value}). Устунни «Матн» форматига '
                                        'ўтказиб, рақамни тўлиқ қайта ёзинг.'))
        for field, max_length in self.max_lengths.items():
            value = row.get(field)
            if value and len(value) > max_length:
                problems.append((field, f'Кўпи билан {max_length} белги бўлиши мумкин, ёзилгани {len(value)} та.'))

        for field in sorted(self.date_fields):
            if field in row:
                problem = self._date_problem(field, row[field])
                if problem:
                    problems.append((field, problem))

        gender = row.get('gender')
        if gender and gender not in {'M', 'F'}:
            problems.append(('gender', f'«{gender}» тушунарсиз: «Эркак» ёки «Аёл» деб ёзинг.'))

        for field in self.choice_fields:
            value = row.get(field)
            if value and _plain_name(value) not in lookups.choices[field]:
                names = lookups.choice_names[field]
                if names:
                    problems.append((field, f'«{value}» рўйхатда йўқ. Мавжудлари: {", ".join(names)}.'))
                else:
                    problems.append((field, f'«{value}» рўйхатда йўқ: рўйхат ҳали бўш (уни ходим формасида тўлдиринг).'))

        if row.get('section_name') and not row.get('department_name'):
            problems.append(('section_name', 'Бўлим ёзилган, лекин «Цех» бўш: бўлим қайси цехга тегишлилигини кўрсатинг.'))
        return problems

    def _date_problem(self, field, value):
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            return f'«{value}» сана сифатида ўқилмади. Санани КК.ОО.ЙЙЙЙ кўринишида ёзинг, масалан 05.03.1990.'
        shown = parsed.strftime('%d.%m.%Y')
        if not 1900 <= parsed.year <= 2100:
            return f'{shown} — сана нотўғри кўринади. Йилни тўрт хонали ёзинг.'
        if field == 'date_of_birth' and parsed > timezone.localdate():
            return f'{shown} — туғилган сана келажакда. Йилни тўрт хонали ёзинг.'
        return ''

    def _existing_tabel_errors(self, prepared_rows):
        """Базада бор табель рақамлари — битта сўровда (илгари ҳар бир қатор учун алоҳида)."""
        tabel_numbers = [row['tabel_number'] for _, row in prepared_rows]
        existing = {}
        for start in range(0, len(tabel_numbers), 500):
            for employee in Employee.objects.filter(tabel_number__in=tabel_numbers[start:start + 500]).select_related('department'):
                existing[employee.tabel_number] = employee
        errors = []
        message = 'Бундай табель рақамли ходим аллақачон қўшилган.'
        for row_number, row in prepared_rows:
            employee = existing.get(row['tabel_number'])
            if employee is None:
                continue
            owner = str(employee) + (f', {employee.department.name}' if employee.department_id else '')
            details = f'ўчирилган ходимлар орасида: {owner}' if employee.is_deleted else owner
            errors.append({
                **self._error(row_number, row['tabel_number'], 'tabel_number', f'{message[:-1]} ({details}).'),
                'errors': {'tabel_number': [message]},
            })
        return errors

    def _missing_required_columns(self, headers):
        found = {self.header_aliases.get(self._normalize_header(header)) for header in headers}
        return [field for field in self.required_fields if field not in found]

    def _is_unknown_header(self, header):
        normalized = self._normalize_header(header)
        return (
            not str(header).startswith('__')
            and normalized not in self.ignored_headers
            and normalized not in self.header_aliases
        )

    def _row_number(self, value, index):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return index + 2
        return number if 1 <= number <= 1_048_576 else index + 2

    def _error(self, row_number, tabel_number, field, message):
        column = self.error_field_labels.get(field, field)
        return {
            'row': row_number,
            'tabel_number': tabel_number,
            'field': field,
            'column': column,
            'message': message,
        }

    def _serializer_errors(self, row_number, row, errors):
        """DRF хатолари (баъзилари инглизча) — устун номи билан, ўқиладиган кўринишда."""
        items = []
        for field, messages in errors.items():
            for message in self._flatten_messages(messages):
                item = self._error(row_number, row.get('tabel_number', ''), field, message)
                item['errors'] = {field: [message]}
                items.append(item)
        return items

    def _flatten_messages(self, messages):
        if isinstance(messages, dict):
            return [text for value in messages.values() for text in self._flatten_messages(value)]
        if isinstance(messages, (list, tuple)):
            return [text for value in messages for text in self._flatten_messages(value)]
        return [str(messages)]

    def _errors_response(self, message, errors):
        errors = sorted(errors, key=lambda item: item.get('row') or 0)
        return Response({
            'success': False,
            'message': message,
            'error_count': len(errors),
            'errors': errors[:self.max_reported_errors],
        }, status=status.HTTP_400_BAD_REQUEST)

    # ── Фотосуратлар ──────────────────────────────────────────────────────────────────────────────────────

    def _index_image_names(self, image_names):
        """dry_run: фотосуратлардан фақат номлари келади — такрор ва номаълум файлларни олдиндан аниқлаш учун."""
        if isinstance(image_names, str):
            try:
                image_names = json.loads(image_names)
            except json.JSONDecodeError:
                image_names = []
        uploaded_images = {}
        image_errors = []
        skipped = []
        for name in image_names if isinstance(image_names, list) else []:
            error = self._store_uploaded_image(uploaded_images, str(name or ''), b'', '')
            if error:
                (skipped if error.get('skipped') else image_errors).append(error)
        return uploaded_images, image_errors, self._skipped_images_warning(skipped)

    def _collect_uploaded_images(self, request):
        uploaded_images = {}
        image_errors = []
        skipped = []

        for uploaded_file in request.FILES.getlist('images'):
            file_name = str(getattr(uploaded_file, 'name', '') or '').strip()
            if not file_name:
                continue

            if os.path.splitext(file_name)[1].lower() == '.zip':
                archive_errors = self._extract_images_from_archive(uploaded_file, uploaded_images)
                for error in archive_errors:
                    (skipped if error.get('skipped') else image_errors).append(error)
                continue

            error = self._store_uploaded_image(uploaded_images, file_name, uploaded_file.read(), getattr(uploaded_file, 'content_type', ''))
            if error:
                (skipped if error.get('skipped') else image_errors).append(error)

        return uploaded_images, image_errors, self._skipped_images_warning(skipped)

    def _skipped_images_warning(self, skipped):
        if not skipped:
            return []
        names = [item['name'] for item in skipped]
        shown = ', '.join(names[:10]) + (f' ва яна {len(names) - 10} та' if len(names) > 10 else '')
        return [f'Тасвир бўлмаган файллар ўтказиб юборилди: {shown}.']

    def _extract_images_from_archive(self, uploaded_file, uploaded_images):
        image_errors = []
        try:
            uploaded_file.seek(0)
            with zipfile.ZipFile(uploaded_file) as archive:
                for archive_item in archive.infolist():
                    if archive_item.is_dir():
                        continue

                    original_name = str(archive_item.filename or '').strip()
                    if not original_name or self._is_archive_junk(original_name):
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

    def _is_archive_junk(self, name):
        """macOS ва Windows архивга қўшадиган хизмат файллари (__MACOSX/…, ._5400.jpg, Thumbs.db)."""
        normalized = str(name).replace('\\', '/')
        base_name = normalized.rsplit('/', 1)[-1]
        return normalized.startswith('__MACOSX/') or base_name.startswith('.') or base_name.lower() in {'thumbs.db', 'desktop.ini'}

    def _store_uploaded_image(self, uploaded_images, original_name, image_bytes, content_type):
        if self._is_archive_junk(original_name):
            return None
        image_key, normalized_name = self._build_image_mapping_key(original_name)
        if image_key is None:
            # Тасвир бўлмаган файл (масалан, архивдаги .txt) импортни тўхтатмайди — огоҳлантиришда кўрсатилади.
            return {
                'skipped': True,
                'name': os.path.basename(str(original_name or '').replace('\\', '/')),
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

    # ── Қийматларни ўқиш ──────────────────────────────────────────────────────────────────────────────────

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

    def _normalize_header(self, value):
        normalized = str(value or '').strip().lower().replace('ё', 'е')
        # ўқғҳ — узбекские буквы; без них «Ишга қабул қилинган сана» превращалось бы в «ишга__абул…».
        normalized = re.sub(r'[^a-z0-9а-яўқғҳ]+', '_', normalized)
        return normalized.strip('_')

    def _normalize_value(self, field_name, value):
        if field_name in self.string_fields:
            if isinstance(value, float) and value.is_integer():
                value = int(value)   # 5400.0 → «5400»
            text_value = ' '.join(str(value).split()) if value is not None else ''
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

    # Excel санаси сон кўринишида (1899-12-30 дан бери кунлар). 10000 дан кичик сон — бу сана эмас, кўпинча
    # фақат йил (1990) ёзилган: уни 1905 йилги сана деб сақламаймиз, хато чиқади.
    min_excel_serial_date = 10000

    def _normalize_date(self, value):
        if value in (None, ''):
            return None

        if isinstance(value, datetime):
            return value.date().isoformat()

        if isinstance(value, date):
            return value.isoformat()

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < self.min_excel_serial_date:
                return str(value)
            excel_epoch = date(1899, 12, 30)
            return (excel_epoch + timedelta(days=int(value))).isoformat()

        text_value = str(value).strip()
        if not text_value:
            return None

        if re.fullmatch(r'\d+(?:\.\d+)?', text_value):
            if float(text_value) < self.min_excel_serial_date:
                return text_value
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
        if normalized in {'m', 'male', 'м', 'муж', 'мужской', 'мужчина', 'эркак', 'э', 'erkak'}:
            return 'M'
        # «ё» выше заменено на «е», поэтому «Аёл» приходит сюда как «аел».
        if normalized in {'f', 'female', 'ж', 'жен', 'женский', 'женщина', 'аел', 'а', 'ayol'}:
            return 'F'
        return str(value).strip()

    def _normalize_role(self, value):
        return ROLE_USER

    def _normalize_login_segment(self, value):
        normalized = slugify(str(value or '').strip(), allow_unicode=True).replace('-', '.')
        normalized = re.sub(r'[^\w.@+-]+', '.', normalized, flags=re.UNICODE)
        normalized = re.sub(r'\.+', '.', normalized).strip('.').lower()
        return normalized

    def _taken_logins(self):
        """Банд логинлар — бир марта (илгари ҳар бир номзод логин учун иккита сўров)."""
        taken = set(Employee.objects.exclude(login='').values_list('login', flat=True))
        taken.update(AuthUser.objects.values_list('username', flat=True))
        return taken

    def _generate_login(self, normalized_row, taken_logins):
        first_name_part = self._normalize_login_segment(normalized_row.get('first_name'))
        last_name_part = self._normalize_login_segment(normalized_row.get('last_name'))
        tabel_part = self._normalize_login_segment(normalized_row.get('tabel_number'))

        base_login = '.'.join(part for part in (first_name_part, last_name_part) if part)
        if not base_login:
            base_login = tabel_part or 'user'

        candidate = base_login
        unique_base = base_login
        if candidate in taken_logins:
            unique_base = f'{base_login}.{tabel_part}' if tabel_part else base_login
            candidate = unique_base

        counter = 2
        while candidate in taken_logins:
            candidate = f'{unique_base}.{counter}'
            counter += 1

        taken_logins.add(candidate)
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
    permission_classes = [IsAuthenticated, IsAdminOrHrRole]
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
        # user__employee: аватар профиля — фото карточки сотрудника (UserProfile.avatar_image), иначе запрос на строку.
        return UserProfile.objects.select_related('user', 'user__employee').order_by('user__username')

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
                'base_image_thumb_url': thumbnail_url(employee.base_image),
                'role': visible_role,
                'profile_id': profile.id if profile else None,
                'has_access': bool(profile),
                'face_id_login_enabled': profile.face_id_login_enabled if profile else None,
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
        if (user.is_superuser or instance.role in {ROLE_ADMIN, ROLE_HR}) and not is_admin(request.user):
            raise PermissionDenied('Администратор ва HR менежер ҳисобларини фақат администратор ўчира олади.')
        if user.pk == request.user.pk:
            raise PermissionDenied('Ўз ҳисобингизни ўчириб бўлмайди.')
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
