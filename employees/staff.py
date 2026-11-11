"""Подсчёт штата и вакансий для страницы «Штат бирлиги ва лавозим».

Ничего не пишет в базу и не меняет существующие таблицы: «занято» считается по
совпадению Employee.position с названием штатной должности в том же цеху/отделе.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Prefetch

from .models import Department, Employee, Section, StaffPosition
from .ordering import order_sections_by_code
from .sections_tree import SectionIndex


UNIT_DEPARTMENT = 'department'
UNIT_SECTION = 'section'

# Верх дерева как в HRM: сначала руководство и заводоуправление, дальше цеха по sort_order.
# Department.sort_order не меняется; по тому же порядку выстроена и таблица сотрудников (StaffTreeOrder).
# Цех подбирается по началу названия (так же, как Department.save берёт номер из названия).
PINNED_DEPARTMENTS = (
    re.compile(r'^\s*ра[хҳ]барият', re.IGNORECASE),  # «Раҳбарият» / «Рахбарият»
    re.compile(r'^\s*20(?!\d)'),                      # «20-Цех Завод бошқармаси», но не «200-…»
)


def staff_tree_order(department):
    for rank, pattern in enumerate(PINNED_DEPARTMENTS):
        if pattern.match(department.name or ''):
            return (0, rank, department.sort_order, department.name)
    return (1, 0, department.sort_order, department.name)


def normalize_position(value):
    """Название должности без лишних пробелов и регистра, чтобы «Слесарь  » == «слесарь»."""
    return ' '.join(str(value or '').split()).casefold()


def position_unit(department_id, section_id):
    """Ключ единицы, к которой относится должность или сотрудник."""
    if section_id:
        return (UNIT_SECTION, section_id)
    return (UNIT_DEPARTMENT, department_id)


@dataclass
class StaffStats:
    # (тип единицы, id единицы, нормализованная должность) -> сколько сотрудников её занимают
    occupied: dict = field(default_factory=lambda: defaultdict(int))
    # (тип единицы, id единицы) -> {'staff': штат, 'vacant': вакансии} только по своим должностям
    own: dict = field(default_factory=lambda: defaultdict(lambda: {'staff': 0, 'vacant': 0}))

    def occupied_for(self, department_id, section_id, name):
        unit_type, unit_id = position_unit(department_id, section_id)
        return self.occupied.get((unit_type, unit_id, normalize_position(name)), 0)


def vacant_places(staff_count, occupied):
    return max(int(staff_count) - int(occupied), 0)


def collect_staff_stats():
    """Один проход по должностям и по активным сотрудникам с заполненной должностью."""
    stats = StaffStats()
    positions = list(StaffPosition.objects.values('department_id', 'section_id', 'name', 'staff_count'))
    if not positions:
        return stats

    wanted_names = {normalize_position(position['name']) for position in positions}
    employees = (
        Employee.objects
        .filter(is_deleted=False, is_active=True)
        .exclude(position='')
        .values_list('department_id', 'section_id', 'position')
    )
    for department_id, section_id, position in employees.iterator():
        name = normalize_position(position)
        if name in wanted_names:
            unit_type, unit_id = position_unit(department_id, section_id)
            stats.occupied[(unit_type, unit_id, name)] += 1

    for position in positions:
        unit = position_unit(position['department_id'], position['section_id'])
        occupied = stats.occupied.get((*unit, normalize_position(position['name'])), 0)
        stats.own[unit]['staff'] += position['staff_count']
        stats.own[unit]['vacant'] += vacant_places(position['staff_count'], occupied)

    return stats


def employees_of_position(position):
    """Сотрудники, занимающие должность: те же правила, что и при подсчёте «занято» в collect_staff_stats."""
    queryset = Employee.objects.filter(is_deleted=False, is_active=True)
    if position.section_id:
        queryset = queryset.filter(section_id=position.section_id)
    else:
        queryset = queryset.filter(department_id=position.department_id, section__isnull=True)

    wanted = normalize_position(position.name)
    queryset = queryset.select_related('department', 'section').order_by('last_name', 'first_name', 'surname', 'tabel_number')
    return [employee for employee in queryset if normalize_position(employee.position) == wanted]


def assign_employee_to_position(employee, position):
    """Назначить сотрудника на должность: цех, отдел и название должности берутся из штатной единицы.

    Другие поля (в том числе «Дата изменения должности») не трогаем.
    """
    employee.department_id = position.department_id
    employee.section_id = position.section_id
    employee.position = position.name
    employee.save(update_fields=['department', 'section', 'position', 'updated_at'])


def remove_employee_from_position(employee):
    """Снять сотрудника с должности: очищаем только название должности, цех и отдел остаются."""
    employee.position = ''
    employee.save(update_fields=['position', 'updated_at'])


def unit_position_options(position, stats):
    """Все должности того же цеха/отдела с числом свободных мест — для перевода внутри единицы."""
    queryset = StaffPosition.objects.all()
    if position.section_id:
        queryset = queryset.filter(section_id=position.section_id)
    else:
        queryset = queryset.filter(department_id=position.department_id, section__isnull=True)

    return [
        {
            'id': item.pk,
            'name': item.name,
            'staff_count': item.staff_count,
            'vacant': vacant_places(item.staff_count, stats.occupied_for(item.department_id, item.section_id, item.name)),
        }
        for item in queryset.order_by('id')
    ]


def unit_totals(stats, unit_type, unit_id):
    """Итоги единицы: свои должности и вместе со всеми вложенными (цех — все его отделы, отдел — все его вложенные)."""
    own = stats.own.get((unit_type, unit_id), {'staff': 0, 'vacant': 0})
    totals = {
        'own_staff': own['staff'],
        'own_vacant': own['vacant'],
        'total_staff': own['staff'],
        'total_vacant': own['vacant'],
    }
    if unit_type == UNIT_DEPARTMENT:
        nested_ids = Section.objects.filter(department_id=unit_id).values_list('id', flat=True)
    else:
        nested_ids = SectionIndex().descendants(unit_id)

    for section_id in nested_ids:
        section_own = stats.own.get((UNIT_SECTION, section_id))
        if section_own:
            totals['total_staff'] += section_own['staff']
            totals['total_vacant'] += section_own['vacant']
    return totals


class StaffTreeOrder:
    """Порядок сотрудников как на странице «Штат бирлиги ва лавозим».

    Сверху вниз: цеха в порядке дерева (Раҳбарият, 20-Цех, дальше по sort_order); внутри цеха —
    сначала сотрудники самого цеха, затем отделы в порядке дерева («ID отдела», вложенные сразу
    после родителя); внутри единицы — по порядку штатных должностей, затем остальные.
    Сотрудники с одинаковым местом остаются в том порядке, в каком пришли из базы (по фамилии),
    потому что сортировка устойчивая.
    """

    def __init__(self):
        departments = sorted(Department.objects.all(), key=staff_tree_order)
        self.department_rank = {department.id: rank for rank, department in enumerate(departments)}
        self.unranked_department = len(departments)

        children = defaultdict(list)
        for section in order_sections_by_code(Section.objects.all(), group_by_department=False):
            children[(section.department_id, section.parent_id)].append(section)
        self.section_rank = {}
        for department in departments:
            stack = [(None, iter(children.get((department.id, None), ())))]
            while stack:
                parent_id, siblings = stack[-1]
                section = next(siblings, None)
                if section is None:
                    stack.pop()
                    continue
                if section.id in self.section_rank:
                    continue
                self.section_rank[section.id] = len(self.section_rank)
                stack.append((section.id, iter(children.get((department.id, section.id), ()))))
        self.unranked_section = len(self.section_rank)

        self.position_rank = {}
        positions = StaffPosition.objects.order_by('id').values_list('department_id', 'section_id', 'name')
        for department_id, section_id, name in positions:
            self.position_rank.setdefault((position_unit(department_id, section_id), normalize_position(name)), len(self.position_rank))
        self.unranked_position = len(self.position_rank)

    def key(self, department_id, section_id, position):
        department = self.department_rank.get(department_id, self.unranked_department)
        section = -1 if not section_id else self.section_rank.get(section_id, self.unranked_section)
        if self.position_rank:
            matched = self.position_rank.get((position_unit(department_id, section_id), normalize_position(position)))
            rank = self.unranked_position if matched is None else matched
        else:
            rank = 0
        return (department, section, rank)

    def sorted_ids(self, queryset):
        """id сотрудников из queryset в порядке дерева; внутри одного места сохраняется порядок queryset."""
        rows = list(queryset.values_list('id', 'department_id', 'section_id', 'position'))
        rows.sort(key=lambda row: self.key(row[1], row[2], row[3]))
        return [row[0] for row in rows]


def build_staff_tree(stats=None):
    """Дерево «цех → отделы» с итогами штата и вакансий для дерева-выбора."""
    stats = stats or collect_staff_stats()
    # Отделы внутри цеха — по «ID отдела», как на странице «Отделы».
    sections = order_sections_by_code(Section.objects.all(), group_by_department=False)
    departments = sorted(
        Department.objects.prefetch_related(Prefetch('sections', queryset=sections)),
        key=staff_tree_order,
    )

    def build_section_node(section, children_by_parent):
        # Итоги отдела включают все его вложенные отделы (как итоги цеха включают его отделы).
        children = [build_section_node(child, children_by_parent) for child in children_by_parent.get(section.id, ())]
        own = stats.own.get((UNIT_SECTION, section.id), {'staff': 0, 'vacant': 0})
        return {
            'type': UNIT_SECTION,
            'id': section.id,
            'name': section.name,
            'code': section.code,
            'parent_id': section.parent_id,
            'staff_total': own['staff'] + sum(child['staff_total'] for child in children),
            'vacant_total': own['vacant'] + sum(child['vacant_total'] for child in children),
            'children': children,
        }

    tree = []
    for department in departments:
        own = stats.own.get((UNIT_DEPARTMENT, department.id), {'staff': 0, 'vacant': 0})

        children_by_parent = defaultdict(list)
        for section in department.sections.all():
            children_by_parent[section.parent_id].append(section)
        children = [build_section_node(section, children_by_parent) for section in children_by_parent.get(None, ())]
        total_staff = own['staff'] + sum(child['staff_total'] for child in children)
        total_vacant = own['vacant'] + sum(child['vacant_total'] for child in children)

        tree.append({
            'type': UNIT_DEPARTMENT,
            'id': department.id,
            'name': department.name,
            'sort_order': department.sort_order,
            'staff_total': total_staff,
            'vacant_total': total_vacant,
            'children': children,
        })

    return tree
