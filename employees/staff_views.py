import re

from django.db import models, transaction
from django.db.models.functions import Lower
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
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
    assign_employee_to_section,
    build_staff_tree,
    collect_staff_stats,
    employee_rate,
    employees_of_position,
    format_rate,
    is_no_staff_section_name,
    occupied_rate,
    parse_staff_rate,
    plain_number,
    remove_employee_from_position,
    remove_employee_from_section,
    section_employees,
    unit_position_options,
    unit_totals,
    vacant_places,
)
from .sections_tree import SectionIndex
from .staff_export import build_staff_workbook, staff_export_file_name, staff_export_groups
from .template_views import StandardResultsSetPagination


STAFF_PERMISSIONS = [IsAuthenticated, IsAdminOrHrRole]
EMPLOYEE_LOOKUP_LIMIT = 10
MULTI_LOOKUP_LIMIT = 200   # бир сўровда бўшлиқ билан ажратилган табель рақамларининг кўпи билан шунча таси қидирилади


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
        'staff_rate': plain_number(employee_rate(employee)),   # неча штатда ишлайди: 1, 0.5 …
    }


def ensure_rate_fits(position, members, rate, *, target=False):
    """Новая доля ставки помещается в свободные ставки должности, иначе — понятная ошибка (400)."""
    vacant = vacant_places(position.staff_count, occupied_rate(members))
    where = 'Танланган лавозимда' if target else 'Бу лавозимда'
    if vacant <= 0:
        raise ValidationError({'detail': f'{where} бўш иш ўрни йўқ. Аввал штатлар сонини кўпайтиринг.'})
    if rate > vacant:
        raise ValidationError({'detail': (
            f'{where} бўш штат: {format_rate(vacant)}, ходимнинг улуши эса {format_rate(rate)}. '
            f'Улушни {format_rate(vacant)} ёки ундан кам қилинг ёки аввал штатлар сонини кўпайтиринг.'
        )})


def get_position(pk, *, for_update=False):
    queryset = StaffPosition.objects.select_related('department', 'section')
    if for_update:
        # of=('self',): строка section может быть NULL (LEFT JOIN), блокировать её нельзя.
        queryset = queryset.select_for_update(of=('self',))
    return get_object_or_404(queryset, pk=pk)


def section_unit_payload(section):
    """Описание отдела для правой панели: название, цех и путь «Цех / Родитель / Отдел»."""
    index = SectionIndex()
    return {
        'type': UNIT_SECTION,
        'id': section.id,
        'name': section.name,
        'department_id': section.department_id,
        'department_name': section.department.name,
        # «Цех / Юридик хизмати» — где в иерархии находится вложенный отдел.
        'breadcrumb': ' / '.join([section.department.name, *[index.rows[i]['name'] for i in index.ancestors(section.id)]]),
        'boss_full_name': '',
        # Штат ажратилмайдиган бўлим («Шогиртлар»): ходимлар рўйхати лавозимларсиз кўрсатилади.
        'no_staff': is_no_staff_section_name(section.name),
    }


def get_no_staff_section(pk):
    section = get_object_or_404(Section.objects.select_related('department'), pk=pk)
    if not is_no_staff_section_name(section.name):
        raise ValidationError({'detail': 'Бу бўлим учун штат ажратилади: ходимларни лавозим орқали қўшинг.'})
    return section


def section_employees_payload(section, employees, request, message):
    return {
        'success': True,
        'message': message,
        'unit': section_unit_payload(section),
        'count': len(employees),
        'data': [serialize_staff_employee(employee, request) for employee in employees],
    }


def position_employees_payload(position, employees, request, message):
    unit_name = position.section.name if position.section_id else position.department.name
    occupied = occupied_rate(employees)   # «Банд» — сумма долей ставки (0,5 + 1 = 1,5), а не число людей
    return {
        'success': True,
        'message': message,
        'position': {
            'id': position.pk,
            'name': position.name,
            'unit_name': unit_name,
            'staff_count': position.staff_count,
            'occupied': plain_number(occupied),
            'vacant': vacant_places(position.staff_count, occupied),
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


class StaffExportTemplateView(APIView):
    """Excel-файл «Штат бирлиги ва лавозим»: строка цеха/отдела, под ней его сотрудники (staff_export)."""

    permission_classes = STAFF_PERMISSIONS

    def get(self, request, *args, **kwargs):
        response = HttpResponse(
            build_staff_workbook(staff_export_groups()),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = content_disposition_header(True, staff_export_file_name())
        return response


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
            unit_payload = section_unit_payload(unit)
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
        # Неча штатда ишлайди: кўрсатилмаса 1 (тўлиқ ставка), 0,5 — ярим ставка.
        try:
            rate = parse_staff_rate(request.data.get('staff_rate'))
        except ValueError as error:
            raise ValidationError({'staff_rate': str(error)})

        with transaction.atomic():
            # Блокировка должности: два одновременных назначения не должны превысить штат.
            position = get_position(pk, for_update=True)
            employee = get_object_or_404(
                Employee.objects.select_related('department', 'section'),
                pk=employee_id, is_deleted=False, is_active=True,
            )
            # Ходим айнан шу қаторга бириктирилади. Бир хил номли бошқа қатордаги ходимни бу ерга қўшиш —
            # уни шу қаторга кўчириш (иш ўрни шу қаторда бўлиши керак).
            members = employees_of_position(position)
            if any(item.pk == employee.pk for item in members):
                raise ValidationError({'detail': 'Бу ходим аллақачон шу лавозимда.'})
            ensure_rate_fits(position, members, rate)

            assign_employee_to_position(employee, position, rate)
            employees = employees_of_position(position)

        return Response(
            position_employees_payload(position, employees, request, 'Ходим лавозимга қўшилди'),
            status=status.HTTP_201_CREATED,
        )


class StaffPositionEmployeeDetailTemplateView(APIView):
    """Один сотрудник должности (окно «Ходимни таҳрирлаш»): перевод на другую должность той же единицы и/или
    новая доля ставки (PUT/PATCH: position_id, staff_rate), снятие с должности (DELETE)."""

    permission_classes = STAFF_PERMISSIONS

    def put(self, request, pk, employee_id, *args, **kwargs):
        raw_rate = request.data.get('staff_rate')
        rate = None
        if raw_rate not in (None, ''):
            try:
                rate = parse_staff_rate(raw_rate)
            except ValueError as error:
                raise ValidationError({'staff_rate': str(error)})

        raw_target = request.data.get('position_id')
        if raw_target in (None, '') and rate is not None:
            target_id = pk   # только новая доля ставки, должность та же
        else:
            try:
                target_id = int(raw_target)
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

            members = employees_of_position(source)
            employee = next((item for item in members if item.pk == employee_id), None)
            if employee is None:
                raise NotFound('Ходим бу лавозимда топилмади.')
            current_rate = employee_rate(employee)

            if target.pk == source.pk:
                # Шу лавозимда қолади — фақат штат улуши ўзгаради.
                if rate is None:
                    raise ValidationError({'detail': 'Ходим аллақачон шу лавозимда.'})
                if rate == current_rate:
                    raise ValidationError({'detail': 'Ўзгариш йўқ: ходимнинг штат улуши аллақачон шундай.'})
                # Улушни камайтириш ҳар доим мумкин (штатдан ортиқча бўлса ҳам, ортиқчаси камаяди); оширишда
                # бошқаларнинг улушидан қолган бўш штат етиши керак.
                if rate > current_rate:
                    ensure_rate_fits(source, [item for item in members if item.pk != employee.pk], rate)
                assign_employee_to_position(employee, source, rate)
                message = 'Ходимнинг штат улуши ўзгартирилди'
            else:
                # Номи бир хил бўлган бошқа қаторга ҳам ўтказиш мумкин: ходим ўша қаторга бириктирилади.
                # Ходим ўз улуши (ёки янги киритилгани) билан ўтади: 0,5 штатлик ходимга 0,5 бўш штат етарли.
                ensure_rate_fits(target, employees_of_position(target), rate if rate is not None else current_rate, target=True)
                assign_employee_to_position(employee, target, rate)
                message = 'Ходим бошқа лавозимга ўтказилди'
            employees = employees_of_position(source)

        return Response(position_employees_payload(source, employees, request, message))

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


class StaffSectionEmployeesTemplateView(APIView):
    """Ходимлар бўлимда штатсиз («Шогиртлар»): рўйхат ва ходимни бўлимга қўшиш. Лавозим ва штат текширилмайди."""

    permission_classes = STAFF_PERMISSIONS

    def get(self, request, pk, *args, **kwargs):
        section = get_no_staff_section(pk)
        return Response(section_employees_payload(section, section_employees(section), request, 'Ходимлар муваффақиятли олинди'))

    def post(self, request, pk, *args, **kwargs):
        try:
            employee_id = int(request.data.get('employee_id'))
        except (TypeError, ValueError):
            raise ValidationError({'detail': 'Ходимни танланг.'})

        with transaction.atomic():
            section = get_no_staff_section(pk)
            employee = get_object_or_404(
                Employee.objects.select_related('department', 'section'),
                pk=employee_id, is_deleted=False, is_active=True,
            )
            if employee.section_id == section.pk:
                raise ValidationError({'detail': 'Бу ходим аллақачон шу бўлимда.'})
            assign_employee_to_section(employee, section)

        return Response(
            section_employees_payload(section, section_employees(section), request, 'Ходим бўлимга қўшилди'),
            status=status.HTTP_201_CREATED,
        )


class StaffSectionEmployeeDetailTemplateView(APIView):
    """Ходимни штатсиз бўлимдан чиқариш: у цехда қолади, фақат бўлим олиб ташланади."""

    permission_classes = STAFF_PERMISSIONS

    def delete(self, request, pk, employee_id, *args, **kwargs):
        with transaction.atomic():
            section = get_no_staff_section(pk)
            employee = Employee.objects.filter(pk=employee_id, section=section, is_deleted=False, is_active=True).first()
            if employee is None:
                raise NotFound('Ходим бу бўлимда топилмади.')
            remove_employee_from_section(employee)

        return Response(section_employees_payload(section, section_employees(section), request, 'Ходим бўлимдан чиқарилди'))


def split_tabel_numbers(term):
    """Табель рақамлари: бўшлиқ, вергул, нуқта-вергул ёки янги қатор билан ажратилган; такрорлари олиб ташланади (тартиб сақланади)."""
    seen, tokens = set(), []
    for token in re.split(r'[\s,;]+', term):
        if token and token.lower() not in seen:
            seen.add(token.lower())
            tokens.append(token)
    return tokens


class StaffEmployeeLookupTemplateView(APIView):
    """Поиск сотрудника по табельному номеру для добавления на должность.

    Один номер — поиск по вхождению (как раньше, до 10 результатов). Несколько номеров через пробел
    («6380 6263 6024») — точное совпадение каждого: найденные идут в порядке ввода, ненайденные — в not_found.
    """

    permission_classes = STAFF_PERMISSIONS

    def get(self, request, *args, **kwargs):
        term = str(request.query_params.get('tabel_number', '') or '').strip()
        if not term:
            return Response({'success': True, 'message': 'Табель рақамини киритинг', 'data': []})

        tokens = split_tabel_numbers(term)
        multiple = len(tokens) > 1
        truncated = len(tokens) > MULTI_LOOKUP_LIMIT
        not_found = []
        if multiple:
            tokens = tokens[:MULTI_LOOKUP_LIMIT]
            found = {
                employee.tabel_number.lower(): employee
                for employee in Employee.objects
                .filter(is_deleted=False, is_active=True)
                .annotate(tabel_lower=Lower('tabel_number'))
                .filter(tabel_lower__in=[token.lower() for token in tokens])
                .select_related('department', 'section')
            }
            employees = [found[token.lower()] for token in tokens if token.lower() in found]
            not_found = [token for token in tokens if token.lower() not in found]
        else:
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
            # «Қўшилган» — фақат айнан шу қатордагилар: бир хил номли бошқа қатордаги ходимни бу ерга кўчириш мумкин.
            already_here = {employee.pk for employee in employees_of_position(position)}
        section_id = str(request.query_params.get('section_id', '') or '').strip()
        if section_id.isdigit():
            # Штатсиз бўлим («Шогиртлар»): унда аллақачон бор ходимлар «Қўшилган» деб белгиланади.
            already_here |= set(
                Employee.objects.filter(section_id=int(section_id), is_deleted=False, is_active=True).values_list('pk', flat=True)
            )

        data = []
        for employee in employees:
            item = serialize_staff_employee(employee, request)
            item['in_position'] = employee.pk in already_here
            data.append(item)

        return Response({
            'success': True,
            'message': 'Ходимлар муваффақиятли олинди',
            'data': data,
            'multiple': multiple,        # бир нечта табель рақами киритилган
            'searched': len(tokens),     # нечта рақам қидирилди (multiple бўлганда)
            'not_found': not_found,      # топилмаган рақамлар (multiple бўлганда)
            'truncated': truncated,      # рақамлар MULTI_LOOKUP_LIMIT дан кўп эди — дастлабкилари қидирилди
        })
