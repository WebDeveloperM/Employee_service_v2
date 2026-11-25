"""Excel-выгрузка страницы «Штат бирлиги ва лавозим».

Порядок как в дереве страницы: строка цеха и его сотрудники, затем его отделы (вложенные — сразу после
родителя): у каждого отдела сначала строка с названием, под ней его сотрудники. Внутри единицы сотрудники идут
по порядку штатных должностей, остальные — в конце; при одинаковой должности — по фамилии.
Цех или отдел без сотрудников (с учётом вложенных) в файл не попадает.
"""
from collections import defaultdict
from copy import copy
from dataclasses import dataclass, field
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Department, Employee, Section
from .ordering import order_sections_by_code
from .staff import StaffTreeOrder, staff_tree_order


EXPORT_COLUMNS = (('№', 6), ('Ф.И.Ш.', 42), ('Табель рақами', 16), ('Лавозим', 70))
UNASSIGNED_TITLE = 'Цех ва бўлим кўрсатилмаган ходимлар'
SHEET_TITLE = 'Штат'

THIN_SIDE = Side(style='thin', color='CBD5E1')
CELL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
HEADER_FILL = PatternFill('solid', fgColor='092482')      # как шапка «Excel экспорти» в списке сотрудников
DEPARTMENT_FILL = PatternFill('solid', fgColor='DBE4F5')
SECTION_FILL = PatternFill('solid', fgColor='F1F5F9')


@dataclass
class ExportGroup:
    level: int                                     # 0 — цех, 1 — отдел, 2+ — вложенный отдел
    title: str
    employees: list = field(default_factory=list)


def staff_export_groups():
    """Единицы в порядке дерева, каждая со своими активными сотрудниками."""
    order = StaffTreeOrder()

    by_unit = defaultdict(list)
    employees = (
        Employee.objects
        .filter(is_deleted=False, is_active=True)
        .order_by('last_name', 'first_name', 'surname', 'tabel_number')
    )
    for employee in employees:
        if employee.section_id:
            by_unit[('section', employee.section_id)].append(employee)
        elif employee.department_id:
            by_unit[('department', employee.department_id)].append(employee)
        else:
            by_unit[None].append(employee)

    def take(unit):
        # Сортировка устойчивая: при одинаковой должности остаётся порядок по фамилии.
        members = by_unit.pop(unit, [])
        members.sort(key=lambda employee: order.key(employee.department_id, employee.section_id, employee.position)[2])
        return members

    sections = list(order_sections_by_code(Section.objects.select_related('department'), group_by_department=False))
    children = defaultdict(list)
    for section in sections:
        children[(section.department_id, section.parent_id)].append(section)
    visited = set()

    def section_groups(department_id, parent_id, trail, level):
        groups = []
        for section in children.get((department_id, parent_id), ()):
            if section.id in visited:
                continue
            visited.add(section.id)
            path = [*trail, section.name]
            own = take(('section', section.id))
            nested = section_groups(department_id, section.id, path, level + 1)
            if own or nested:
                # Вложенный отдел — с путём от цеха («Хизмат / Бўлим»): одноимённые отделы не путаются.
                groups.append(ExportGroup(level, ' / '.join(path), own))
                groups.extend(nested)
        return groups

    groups = []
    for department in sorted(Department.objects.all(), key=staff_tree_order):
        own = take(('department', department.id))
        nested = section_groups(department.id, None, [], 1)
        if own or nested:
            groups.append(ExportGroup(0, department.name, own))
            groups.extend(nested)

    # Отдел вне дерева (родитель в другом цехе и т. п.) — в конце, с названием цеха, чтобы сотрудники не потерялись.
    for section in sections:
        if ('section', section.id) in by_unit:
            groups.append(ExportGroup(0, f'{section.department.name} / {section.name}', take(('section', section.id))))
    if by_unit.get(None):
        groups.append(ExportGroup(0, UNASSIGNED_TITLE, take(None)))
    return groups


def keep_as_text(sheet, row):
    """openpyxl строку, начинающуюся с «=», записывает как формулу: имя «=HYPERLINK(…)» в карточке
    превратилось бы в рабочую формулу у того, кто откроет файл (formula injection). Храним как текст."""
    for column in range(1, len(EXPORT_COLUMNS) + 1):
        cell = sheet.cell(row=row, column=column)
        if isinstance(cell.value, str) and cell.value.startswith('='):
            cell.data_type = 's'


class RowStyler:
    """Оформление строк. Каждый набор стилей (рамка + шрифт/заливка/выравнивание) собирается один раз, дальше
    ячейкам копируется готовый индекс стиля: присваивание cell.font/border/… каждый раз хэширует объекты стиля,
    и на ~3500 строк это занимало больше секунды."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.arrays = {}

    def style(self, row, column, key, **attrs):
        cell = self.sheet.cell(row=row, column=column)
        array = self.arrays.get(key)
        if array is None:
            cell.border = CELL_BORDER
            for name, value in attrs.items():
                setattr(cell, name, value)
            self.arrays[key] = copy(cell._style)
        else:
            cell._style = copy(array)

    def row(self, row, key, **attrs):
        for column in range(1, len(EXPORT_COLUMNS) + 1):
            self.style(row, column, key, **attrs)


CENTER = Alignment(horizontal='center')


def build_staff_workbook(groups):
    """xlsx (байты): шапка, затем по каждой единице — строка с названием и её сотрудники."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    styler = RowStyler(sheet)

    sheet.append([title for title, _ in EXPORT_COLUMNS])
    styler.row(
        1, 'header',
        font=Font(bold=True, color='FFFFFF'),
        fill=HEADER_FILL,
        alignment=Alignment(horizontal='center', vertical='center'),
    )
    for column, (_, width) in enumerate(EXPORT_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width

    # Номер строки считаем сами: sheet.max_row каждый раз перебирает все ячейки листа (квадратичное время).
    row = 1
    for group in groups:
        sheet.append([group.title])
        row += 1
        keep_as_text(sheet, row)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(EXPORT_COLUMNS))
        indent = max(group.level - 1, 0) * 2
        styler.row(
            row, ('group', group.level == 0, indent),
            font=Font(bold=True, size=12 if group.level == 0 else 11),
            fill=DEPARTMENT_FILL if group.level == 0 else SECTION_FILL,
            alignment=Alignment(vertical='center', indent=indent),
        )

        for number, employee in enumerate(group.employees, start=1):
            sheet.append([
                number,
                str(employee),
                employee.tabel_number or '',
                ' '.join((employee.position or '').split()),
            ])
            row += 1
            keep_as_text(sheet, row)
            styler.style(row, 1, 'employee-center', alignment=CENTER)
            styler.style(row, 2, 'employee')
            styler.style(row, 3, 'employee-center', alignment=CENTER)
            styler.style(row, 4, 'employee')

    sheet.freeze_panes = 'A2'
    # Печать: шапка на каждой странице, по ширине — одна страница.
    sheet.print_title_rows = '1:1'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def staff_export_file_name():
    return f'Штат бирлиги ва лавозим - {timezone.localdate():%d.%m.%Y}.xlsx'
