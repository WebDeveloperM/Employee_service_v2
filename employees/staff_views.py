from django.db import models, transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Department, Employee, Section, StaffPosition
from .permissions import IsAdminOrHrRole
from .serializers import StaffPositionSerializer
from .staff import (
    UNIT_DEPARTMENT,
    UNIT_SECTION,
    assign_employee_to_position,
    build_staff_tree,
    collect_staff_stats,
    employees_of_position,
    remove_employee_from_position,
    unit_position_options,
    unit_totals,
    vacant_places,
)
from .sections_tree import SectionIndex
from .template_views import StandardResultsSetPagination


STAFF_PERMISSIONS = [IsAuthenticated, IsAdminOrHrRole]
EMPLOYEE_LOOKUP_LIMIT = 10


def serialize_staff_employee(employee, request):
    photo = request.build_absolute_uri(employee.base_image.url) if employee.base_image else ''
    return {
        'id': employee.pk,
        'slug': employee.slug,
        'full_name': str(employee),
        'tabel_number': employee.tabel_number,
        'login': employee.login,
        'photo': photo,
        'department': employee.department.name if employee.department_id else '',
        'section': employee.section.name if employee.section_id else '',
        'position': ' '.join((employee.position or '').split()),
    }


def get_position(pk, *, for_update=False):
    queryset = StaffPosition.objects.select_related('department', 'section')
    if for_update:
        # of=('self',): строка section может быть NULL (LEFT JOIN), блокировать её нельзя.
        queryset = queryset.select_for_update(of=('self',))
    return get_object_or_404(queryset, pk=pk)


def position_employees_payload(position, employees, request, message):
    unit_name = position.section.name if position.section_id else position.department.name
    return {
        'success': True,
        'message': message,
        'position': {
            'id': position.pk,
            'name': position.name,
            'unit_name': unit_name,
            'staff_count': position.staff_count,
            'occupied': len(employees),
            'vacant': vacant_places(position.staff_count, len(employees)),
        },
        # Должности этой же единицы — для выбора при переводе сотрудника (кнопка «Таҳрирлаш»).
        'unit_positions': unit_position_options(position, collect_staff_stats()),
        'data': [serialize_staff_employee(employee, request) for employee in employees],
    }


class StaffTreeTemplateView(APIView):
    """Дерево «цех → отделы» с итогами штата и вакансий для дерева-выбора."""

    permission_classes = STAFF_PERMISSIONS

    def get(self, request, *args, **kwargs):
        return Response({
            'success': True,
            'message': 'Дарахт муваффақиятли олинди',
            'data': build_staff_tree(),
        })


class StaffPositionListCreateTemplateView(generics.ListCreateAPIView):
    """Должности выбранной единицы: ?section_id=<id> (отдел) или ?department_id=<id> (сам цех)."""

    serializer_class = StaffPositionSerializer
    permission_classes = STAFF_PERMISSIONS
    pagination_class = StandardResultsSetPagination

    def get_unit(self):
        params = self.request.query_params
        section_id = str(params.get('section_id', '') or '').strip()
        department_id = str(params.get('department_id', '') or '').strip()

        if section_id.isdigit():
            section = get_object_or_404(Section.objects.select_related('department'), pk=int(section_id))
            return UNIT_SECTION, section
        if department_id.isdigit():
            return UNIT_DEPARTMENT, get_object_or_404(Department, pk=int(department_id))
        raise ValidationError({'detail': 'Цех ёки бўлимни танланг.'})

    def get_queryset(self):
        unit_type, unit = self.get_unit()
        queryset = StaffPosition.objects.all()
        if unit_type == UNIT_SECTION:
            queryset = queryset.filter(section=unit)
        else:
            queryset = queryset.filter(department=unit, section__isnull=True)

        search = str(self.request.query_params.get('search', '') or '').strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset.order_by('id')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Для списка статистика уже посчитана (одна выборка сотрудников на запрос,
        # а не на каждую строку); при создании сериализатор посчитает её сам.
        context['staff_stats'] = getattr(self, 'staff_stats', None)
        return context

    def list(self, request, *args, **kwargs):
        self.staff_stats = collect_staff_stats()
        unit_type, unit = self.get_unit()
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)

        if unit_type == UNIT_SECTION:
            index = SectionIndex()
            unit_payload = {
                'type': UNIT_SECTION,
                'id': unit.id,
                'name': unit.name,
                'department_id': unit.department_id,
                'department_name': unit.department.name,
                # «Цех / Юридик хизмати» — где в иерархии находится вложенный отдел.
                'breadcrumb': ' / '.join([unit.department.name, *[index.rows[i]['name'] for i in index.ancestors(unit.id)]]),
                'boss_full_name': '',
            }
        else:
            unit_payload = {
                'type': UNIT_DEPARTMENT,
                'id': unit.id,
                'name': unit.name,
                'department_id': unit.id,
                'department_name': unit.name,
                'boss_full_name': unit.boss_full_name,
            }

        payload = {
            'success': True,
            'message': 'Лавозимлар муваффақиятли олинди',
            'data': serializer.data,
            'unit': unit_payload,
            'summary': unit_totals(self.staff_stats, unit_type, unit.id),
        }
        if page is not None:
            payload.update({
                'count': self.paginator.page.paginator.count,
                'next': self.paginator.get_next_link(),
                'previous': self.paginator.get_previous_link(),
            })
        return Response(payload)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            'success': True,
            'message': 'Лавозим муваффақиятли қўшилди',
            'data': serializer.data,
        }, status=status.HTTP_201_CREATED)


class StaffPositionDetailTemplateView(generics.RetrieveUpdateDestroyAPIView):
    queryset = StaffPosition.objects.all()
    serializer_class = StaffPositionSerializer
    permission_classes = STAFF_PERMISSIONS

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return Response({
            'success': True,
            'message': 'Лавозим муваффақиятли олинди',
            'data': serializer.data,
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({
            'success': True,
            'message': 'Лавозим муваффақиятли янгиланди',
            'data': serializer.data,
        })

    def destroy(self, request, *args, **kwargs):
        self.perform_destroy(self.get_object())
        return Response({
            'success': True,
            'message': 'Лавозим муваффақиятли ўчирилди',
        }, status=status.HTTP_204_NO_CONTENT)


class StaffPositionEmployeesTemplateView(APIView):
    """Сотрудники должности (окно «Ходимлар») и назначение сотрудника на должность."""

    permission_classes = STAFF_PERMISSIONS

    def get(self, request, pk, *args, **kwargs):
        position = get_position(pk)
        return Response(position_employees_payload(
            position, employees_of_position(position), request, 'Ходимлар муваффақиятли олинди',
        ))

    def post(self, request, pk, *args, **kwargs):
        try:
            employee_id = int(request.data.get('employee_id'))
        except (TypeError, ValueError):
            raise ValidationError({'detail': 'Ходимни танланг.'})

        with transaction.atomic():
            # Блокировка должности: два одновременных назначения не должны превысить штат.
            position = get_position(pk, for_update=True)
            employee = get_object_or_404(
                Employee.objects.select_related('department', 'section'),
                pk=employee_id, is_deleted=False, is_active=True,
            )
            current = employees_of_position(position)
            if any(item.pk == employee.pk for item in current):
                raise ValidationError({'detail': 'Бу ходим аллақачон шу лавозимда.'})
            if len(current) >= position.staff_count:
                raise ValidationError({'detail': 'Бу лавозимда бўш иш ўрни йўқ. Аввал штатлар сонини кўпайтиринг.'})

            assign_employee_to_position(employee, position)
            employees = employees_of_position(position)

        return Response(
            position_employees_payload(position, employees, request, 'Ходим лавозимга қўшилди'),
            status=status.HTTP_201_CREATED,
        )


class StaffPositionEmployeeDetailTemplateView(APIView):
    """Один сотрудник должности: перевод на другую должность той же единицы (PUT/PATCH) и снятие с должности (DELETE)."""

    permission_classes = STAFF_PERMISSIONS

    def put(self, request, pk, employee_id, *args, **kwargs):
        try:
            target_id = int(request.data.get('position_id'))
        except (TypeError, ValueError):
            raise ValidationError({'detail': 'Лавозимни танланг.'})

        with transaction.atomic():
            # Обе должности блокируем в порядке pk, чтобы параллельные переводы не зашли в тупик.
            locked = {
                item.pk: item
                for item in StaffPosition.objects.select_related('department', 'section')
                .select_for_update(of=('self',)).filter(pk__in={pk, target_id}).order_by('pk')
            }
            source = locked.get(pk)
            target = locked.get(target_id)
            if source is None:
                raise NotFound('Лавозим топилмади.')
            if target is None:
                raise ValidationError({'detail': 'Танланган лавозим топилмади.'})
            if (source.department_id, source.section_id) != (target.department_id, target.section_id):
                raise ValidationError({'detail': 'Ходимни фақат шу цех ёки бўлимнинг бошқа лавозимига ўтказиш мумкин.'})

            employee = next((item for item in employees_of_position(source) if item.pk == employee_id), None)
            if employee is None:
                raise NotFound('Ходим бу лавозимда топилмади.')
            if target.pk == source.pk:
                raise ValidationError({'detail': 'Ходим аллақачон шу лавозимда.'})
            if len(employees_of_position(target)) >= target.staff_count:
                raise ValidationError({'detail': 'Танланган лавозимда бўш иш ўрни йўқ. Аввал штатлар сонини кўпайтиринг.'})

            assign_employee_to_position(employee, target)
            employees = employees_of_position(source)

        return Response(position_employees_payload(source, employees, request, 'Ходим бошқа лавозимга ўтказилди'))

    patch = put

    def delete(self, request, pk, employee_id, *args, **kwargs):
        with transaction.atomic():
            position = get_position(pk, for_update=True)
            employee = next((item for item in employees_of_position(position) if item.pk == employee_id), None)
            if employee is None:
                raise NotFound('Ходим бу лавозимда топилмади.')
            remove_employee_from_position(employee)
            employees = employees_of_position(position)

        return Response(position_employees_payload(position, employees, request, 'Ходим лавозимдан чиқарилди'))


class StaffEmployeeLookupTemplateView(APIView):
    """Поиск сотрудника по табельному номеру для добавления на должность."""

    permission_classes = STAFF_PERMISSIONS

    def get(self, request, *args, **kwargs):
        term = str(request.query_params.get('tabel_number', '') or '').strip()
        if not term:
            return Response({'success': True, 'message': 'Табель рақамини киритинг', 'data': []})

        employees = (
            Employee.objects
            .filter(is_deleted=False, is_active=True, tabel_number__icontains=term)
            .select_related('department', 'section')
            .annotate(match_rank=models.Case(
                models.When(tabel_number__iexact=term, then=models.Value(0)),
                default=models.Value(1),
                output_field=models.IntegerField(),
            ))
            .order_by('match_rank', 'tabel_number')[:EMPLOYEE_LOOKUP_LIMIT]
        )

        already_here = set()
        position_id = str(request.query_params.get('position_id', '') or '').strip()
        if position_id.isdigit():
            position = get_object_or_404(StaffPosition.objects.select_related('department', 'section'), pk=int(position_id))
            already_here = {employee.pk for employee in employees_of_position(position)}

        data = []
        for employee in employees:
            item = serialize_staff_employee(employee, request)
            item['in_position'] = employee.pk in already_here
            data.append(item)

        return Response({'success': True, 'message': 'Ходимлар муваффақиятли олинди', 'data': data})
