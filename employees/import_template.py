"""Ходимларни Excel орқали импорт қилиш: устунлар рўйхати (ягона манба) ва юклаб олинадиган шаблон.

IMPORT_COLUMNS дан учта жой фойдаланади: шаблон файл (build_import_template), импорт ойнасидаги устунлар жадвали
(employees_list.html) ва импорт сарлавҳаларни таниши (EmployeeImportExcelTemplateView.header_aliases — тест текширади).
Шаблондаги сарлавҳалар «Excel экспорти» билан бир хил: экспорт қилинган файлни ҳам қайта юклаш мумкин.
"""
from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .models import Department, Employee, EmployeeCategory, PositionCategory, Section, WorkSchedule
from .ordering import order_sections_by_code
from .sections_tree import SectionIndex, order_depth_first


KIND_TEXT = 'text'
KIND_DATE = 'date'
KIND_CHOICE = 'choice'

DATA_SHEET_TITLE = 'Ходимлар'
GUIDE_SHEET_TITLE = 'Йўриқнома'
UNITS_SHEET_TITLE = 'Цех ва бўлимлар'
LISTS_SHEET_TITLE = 'Рўйхатлар'
TEMPLATE_FILE_NAME = 'Ходимлар_импорт_шаблони.xlsx'

# Шунча маълумот қаторига текширув (рўйхат, сана, такрорланмас табель) қўйилади — импорт чегараси билан бир хил.
TEMPLATE_DATA_ROWS = 5000
DATE_FORMAT_HINT = 'КК.ОО.ЙЙЙЙ'
GENDER_CHOICES = ('Эркак', 'Аёл')


@dataclass(frozen=True)
class ImportColumn:
    header: str
    field: str
    required: bool = False
    kind: str = KIND_TEXT
    width: int = 18
    example: str = ''
    hint: str = ''


IMPORT_COLUMNS = (
    ImportColumn('Табель рақами', 'tabel_number', required=True, width=16, example='005400',
                 hint='Ноёб: файлда ҳам, базада ҳам такрорланмасин. Матн сифатида сақланади — бошидаги 0 йўқолмайди.'),
    ImportColumn('Фамилия', 'last_name', required=True, width=20, example='Каримов'),
    ImportColumn('Исм', 'first_name', required=True, width=18, example='Алишер'),
    ImportColumn('Отасининг исми', 'surname', width=22, example='Баҳодирович'),
    ImportColumn('Жинси', 'gender', kind=KIND_CHOICE, width=10, example='Эркак', hint='Эркак ёки Аёл — рўйхатдан танланг.'),
    ImportColumn('Туғилган сана', 'date_of_birth', kind=KIND_DATE, width=15, example='05.03.1990',
                 hint=f'Сана {DATE_FORMAT_HINT} кўринишида.'),
    ImportColumn('Телефон 1', 'phone_number_1', width=17, example='+998901234567', hint='Кўпи билан 20 белги.'),
    ImportColumn('Телефон 2', 'phone_number_2', width=17, example='+998712345678', hint='Кўпи билан 20 белги.'),
    ImportColumn('Цех', 'department_name', kind=KIND_CHOICE, width=32, example='1-Цех',
                 hint='Рўйхатдан танланг. Рўйхатда йўқ ном ёзилса, импортда янги цех яратилади (олдиндан кўрсатилади).'),
    ImportColumn('Бўлим', 'section_name', width=32, example='Таъмирлаш бўлими',
                 hint=f'Шу цехдаги бўлим номи («{UNITS_SHEET_TITLE}» варағида). Йўқ бўлса, яратилади. Цехсиз ёзилмайди.'),
    ImportColumn('Лавозим', 'position', width=30, example='Слесарь'),
    ImportColumn('Даража (разряд)', 'grade', width=14, example='4', hint='1–2 хонали рақам, охирида «*» бўлиши мумкин: 4, 12, 12*.'),
    ImportColumn('Лавозим тоифаси', 'position_category', kind=KIND_CHOICE, width=20, example='Ишчи', hint='Рўйхатдан танланг.'),
    ImportColumn('Ходимлар тоифаси', 'employee_category', kind=KIND_CHOICE, width=26, example='Техник ходимлар', hint='Рўйхатдан танланг.'),
    ImportColumn('Иш тартиби', 'work_schedule', kind=KIND_CHOICE, width=24, example='Сменада', hint='Рўйхатдан танланг.'),
    ImportColumn('Ишга қабул қилинган сана', 'date_of_employment', kind=KIND_DATE, width=15, example='01.09.2015',
                 hint=f'Сана {DATE_FORMAT_HINT} кўринишида.'),
    ImportColumn('Лавозим ўзгарган сана', 'date_of_change_position', kind=KIND_DATE, width=15, example='01.02.2021',
                 hint=f'Сана {DATE_FORMAT_HINT} кўринишида.'),
    ImportColumn('Махсус кийим', 'special_clothing_size', width=13, example='52'),
    ImportColumn('Оёқ кийим', 'shoe_size', width=11, example='42'),
    ImportColumn('Куртка', 'jacket_size', width=10, example='L'),
    ImportColumn('Футболка', 'tshirt_size', width=10, example='XL'),
    ImportColumn('Халат', 'clothe_size', width=10, example='54'),
)

COLUMN_LABELS = {column.field: column.header for column in IMPORT_COLUMNS}

# Рўйхатли устунлар: қийматлар «Рўйхатлар» варағидан (жинси — шу ерда ёзилган).
CHOICE_SOURCES = {
    'department_name': Department,
    'position_category': PositionCategory,
    'employee_category': EmployeeCategory,
    'work_schedule': WorkSchedule,
}

REQUIRED_FILL = PatternFill('solid', fgColor='B91C1C')
HEADER_FILL = PatternFill('solid', fgColor='092482')      # «Excel экспорти» шапкаси билан бир хил
GUIDE_HEADER_FILL = PatternFill('solid', fgColor='DBE4F5')
THIN_SIDE = Side(style='thin', color='CBD5E1')
CELL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
WRAP = Alignment(wrap_text=True, vertical='top')

GUIDE_RULES = (
    f'Ходимларни «{DATA_SHEET_TITLE}» варағига 2-қатордан бошлаб, ҳар бир ходимни алоҳида қаторга киритинг. '
    'Сарлавҳалар қаторини ва варақ номини ўзгартирманг.',
    'Қизил сарлавҳали устунлар мажбурий: Табель рақами, Фамилия, Исм. Қолганлари бўш қолиши мумкин.',
    'Табель рақами ноёб бўлиши керак: файлда ҳам, базада ҳам такрорланмасин. Устун матн форматида — '
    'бошидаги 0 сақланади.',
    f'Саналар {DATE_FORMAT_HINT} кўринишида: 05.03.1990. Йилни тўрт хонали ёзинг.',
    'Жинси, Цех, тоифалар ва Иш тартиби — катакни танлаганда чиқадиган рўйхатдан танланг.',
    'Цех рўйхатда бўлмаса, импортда янги цех яратилади. Бўлим — шу цехдаги бўлимнинг номи '
    f'(«{UNITS_SHEET_TITLE}» варағи); йўқ бўлса, яратилади. Импортдан олдин янги цех ва бўлимлар рўйхати кўрсатилади.',
    'Логин ва парол ҳар бир ходимга автоматик яратилади. Импорт тугагач, уларни Excel файлга юклаб олинг — '
    'пароллар қайта кўрсатилмайди.',
    'Фотосуратлар ихтиёрий: файл номи табель рақамига тенг бўлсин (масалан, 005400.jpg). Бир нечта файл ёки битта '
    'ZIP-архив танлаш мумкин.',
    'Импортдан олдин файл тўлиқ текширилади. Битта қаторда хато бўлса ҳам, ҳеч ким қўшилмайди: хатолар қатор ва '
    'устун номи билан кўрсатилади.',
)


def _write(sheet, row, column, value):
    """Матнни катакка ёзади. «=» билан бошланадиган ном формула бўлиб қолмасин."""
    cell = sheet.cell(row=row, column=column, value=value)
    if isinstance(value, str) and value.startswith('='):
        cell.data_type = 's'
    return cell


def _max_length(field):
    try:
        return Employee._meta.get_field(field).max_length
    except Exception:
        return None


def _choice_values():
    values = {field: list(model.objects.values_list('name', flat=True)) for field, model in CHOICE_SOURCES.items()}
    values['gender'] = list(GENDER_CHOICES)
    return values


def _add_lists_sheet(workbook, choice_values):
    """Яширин «Рўйхатлар» варағи: ҳар бир рўйхатли устун учун битта устун. Қайтаради: майдон -> Excel манзили."""
    sheet = workbook.create_sheet(LISTS_SHEET_TITLE)
    sheet.sheet_state = 'hidden'
    ranges = {}
    for index, column in enumerate((column for column in IMPORT_COLUMNS if column.kind == KIND_CHOICE), start=1):
        values = choice_values.get(column.field) or []
        _write(sheet, 1, index, column.header).font = Font(bold=True)
        for row, value in enumerate(values, start=2):
            _write(sheet, row, index, value)
        if values:
            letter = get_column_letter(index)
            ranges[column.field] = f"'{LISTS_SHEET_TITLE}'!${letter}$2:${letter}${len(values) + 1}"
        sheet.column_dimensions[get_column_letter(index)].width = column.width
    return ranges


def _validation(column, letter, list_ranges):
    """Устун учун Excel текшируви ва катак танланганда чиқадиган изоҳ."""
    first, last = 2, TEMPLATE_DATA_ROWS + 1
    cells = f'{letter}{first}:{letter}{last}'
    prompt = column.hint[:250] or None
    if column.field == 'tabel_number':
        validation = DataValidation(
            type='custom', formula1=f'COUNTIF(${letter}${first}:${letter}${last},{letter}{first})=1',
            error='Бу табель рақами шу файлда аллақачон бор. Табель рақами ноёб бўлиши керак.',
        )
    elif column.kind == KIND_DATE:
        validation = DataValidation(
            type='date', operator='between', formula1='DATE(1900,1,1)', formula2='DATE(2100,12,31)',
            error=f'Бу сана эмас. Санани {DATE_FORMAT_HINT} кўринишида ёзинг, масалан 05.03.1990.',
        )
    elif column.kind == KIND_CHOICE:
        if column.field == 'gender':
            formula = '"' + ','.join(GENDER_CHOICES) + '"'
        elif column.field in list_ranges:
            formula = list_ranges[column.field]
        else:
            return None   # рўйхат бўш (масалан, иш тартиблари ҳали қўшилмаган)
        validation = DataValidation(type='list', formula1=formula, error='Қийматни рўйхатдан танланг.')
        if column.field == 'department_name':
            # Янги цех ёзиш мумкин: Excel фақат огоҳлантиради, импорт эса уни олдиндан кўрсатади.
            validation.errorStyle = 'warning'
            validation.error = 'Бундай цех рўйхатда йўқ. Давом этсангиз, импортда янги цех яратилади.'
    else:
        max_length = 3 if column.field == 'grade' else _max_length(column.field)
        if not max_length and not prompt:
            return None
        if max_length:
            validation = DataValidation(
                type='textLength', operator='lessThanOrEqual', formula1=str(max_length),
                error=f'Кўпи билан {max_length} белги ёзиш мумкин.',
            )
        else:
            validation = DataValidation()
    validation.allow_blank = True
    validation.showErrorMessage = validation.type is not None
    validation.errorTitle = column.header[:32]
    if prompt:
        validation.showInputMessage = True
        validation.promptTitle = column.header[:32]
        validation.prompt = prompt
    validation.add(cells)
    return validation


def _add_data_sheet(sheet, list_ranges):
    sheet.title = DATA_SHEET_TITLE
    for index, column in enumerate(IMPORT_COLUMNS, start=1):
        letter = get_column_letter(index)
        cell = _write(sheet, 1, index, column.header)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = REQUIRED_FILL if column.required else HEADER_FILL
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = CELL_BORDER
        note = ('Мажбурий. ' if column.required else '') + column.hint
        if note.strip():
            cell.comment = Comment(note.strip(), 'bnpzID', width=260, height=110)

        dimension = sheet.column_dimensions[letter]
        dimension.width = column.width
        # Бутун устун форматида: Excel янги ёзилган катакни шу форматда сақлайди (табель/телефон — матн, сана — сана).
        dimension.number_format = 'DD.MM.YYYY' if column.kind == KIND_DATE else '@'

        validation = _validation(column, letter, list_ranges)
        if validation is not None:
            sheet.add_data_validation(validation)
    sheet.row_dimensions[1].height = 36
    sheet.freeze_panes = 'D2'   # сарлавҳа ва Табель рақами / Фамилия / Исм айланмайди


def _add_guide_sheet(workbook, choice_values):
    sheet = workbook.create_sheet(GUIDE_SHEET_TITLE)
    for letter, width in zip('ABCD', (28, 12, 72, 22)):
        sheet.column_dimensions[letter].width = width

    title = _write(sheet, 1, 1, 'Ходимларни Excel орқали импорт қилиш — йўриқнома')
    title.font = Font(bold=True, size=14)
    row = 3
    _write(sheet, row, 1, 'Қоидалар').font = Font(bold=True, size=12)
    for number, rule in enumerate(GUIDE_RULES, start=1):
        row += 1
        _write(sheet, row, 1, f'{number}.').alignment = Alignment(horizontal='right', vertical='top')
        cell = _write(sheet, row, 2, rule)
        cell.alignment = WRAP
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        sheet.row_dimensions[row].height = 32

    row += 2
    _write(sheet, row, 1, 'Устунлар').font = Font(bold=True, size=12)
    row += 1
    for index, header in enumerate(('Устун', 'Мажбурий', 'Нима ёзилади', 'Мисол'), start=1):
        cell = _write(sheet, row, index, header)
        cell.font = Font(bold=True)
        cell.fill = GUIDE_HEADER_FILL
        cell.border = CELL_BORDER
    for column in IMPORT_COLUMNS:
        row += 1
        hint = column.hint
        values = choice_values.get(column.field)
        if column.kind == KIND_CHOICE and values:
            shown = ', '.join(values[:12]) + (f' ва яна {len(values) - 12} та' if len(values) > 12 else '')
            hint = f'{hint} Рўйхат: {shown}.'.strip()
        cells = (
            _write(sheet, row, 1, column.header),
            _write(sheet, row, 2, 'Ҳа' if column.required else ''),
            _write(sheet, row, 3, hint),
            _write(sheet, row, 4, column.example),
        )
        for cell in cells:
            cell.alignment = WRAP
            cell.border = CELL_BORDER
        cells[0].font = Font(bold=True, color='B91C1C' if column.required else '0F172A')
        cells[3].number_format = '@'


def _add_units_sheet(workbook):
    """Мавжуд цех ва бўлимлар: «Бўлим» устунига айнан шу ном ёзилади."""
    sheet = workbook.create_sheet(UNITS_SHEET_TITLE)
    headers = (('Цех', 36), ('Бўлим (шу номни ёзинг)', 40), ('Қайси бўлим ичида', 48))
    for index, (header, width) in enumerate(headers, start=1):
        cell = _write(sheet, 1, index, header)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = HEADER_FILL
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = 'A2'

    index = SectionIndex()
    sections_by_department = {}
    for section in order_sections_by_code(Section.objects.all()):
        sections_by_department.setdefault(section.department_id, []).append(section)
    # Ички бўлим ўз юқори бўлимидан кейин («Штат бирлиги ва лавозим» дарахтидаги каби).
    sections_by_department = {key: order_depth_first(value) for key, value in sections_by_department.items()}

    row = 1
    for department in Department.objects.all():
        sections = sections_by_department.get(department.pk) or [None]
        for section in sections:
            row += 1
            _write(sheet, row, 1, department.name)
            if section is not None:
                _write(sheet, row, 2, section.name)
                _write(sheet, row, 3, index.path(section.pk, include_self=False))


def build_import_template():
    """Импорт шаблони (.xlsx байтлари): «Ходимлар» (биринчи варақ — импорт уни ўқийди), йўриқнома, цех ва бўлимлар."""
    workbook = Workbook()
    choice_values = _choice_values()
    data_sheet = workbook.active
    list_ranges = _add_lists_sheet(workbook, choice_values)
    _add_data_sheet(data_sheet, list_ranges)
    _add_guide_sheet(workbook, choice_values)
    _add_units_sheet(workbook)
    # Варақлар тартиби: Ходимлар, Йўриқнома, Цех ва бўлимлар, (яширин) Рўйхатлар.
    workbook.move_sheet(LISTS_SHEET_TITLE, offset=len(workbook.sheetnames) - 1 - workbook.sheetnames.index(LISTS_SHEET_TITLE))
    workbook.active = 0

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
