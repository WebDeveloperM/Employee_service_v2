"""Подсчёт штата и вакансий для страницы «Штат бирлиги ва лавозим».

«Занято» считается на лету по совпадению Employee.position с названием штатной должности в том же
цеху/отделе; среди одноимённых должностей сотрудник учитывается в строке, к которой привязан
(Employee.staff_position), а без привязки — по порядку строк (distribute_unbound).
Каждый сотрудник занимает свою долю ставки (Employee.staff_rate: 1, 0,5 …): «занято» — сумма долей.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from django.db.models import Prefetch

from .models import Department, Employee, Section, StaffPosition
from .ordering import order_sections_by_code
from .sections_tree import SectionIndex


UNIT_DEPARTMENT = 'department'
UNIT_SECTION = 'section'

# Верх дерева как в HRM: сначала руководство и заводоуправление, дальше цеха по sort_order.
# Department.sort_order не меняется; по тому же порядку выстроена и таблица сотрудников (StaffTreeOrder).
# Цех подбирается по началу названия (так же, как Department.save берёт номер из названия).
LEADERSHIP_DEPARTMENT = re.compile(r'^\s*ра[хҳ]барият', re.IGNORECASE)   # «Раҳбарият» / «Рахбарият»
PINNED_DEPARTMENTS = (
    LEADERSHIP_DEPARTMENT,
    re.compile(r'^\s*20(?!\d)'),                      # «20-Цех Завод бошқармаси», но не «200-…»
)


def is_leadership_department(department):
    return department is not None and bool(LEADERSHIP_DEPARTMENT.match(department.name or ''))


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


# Бўлимлар, учун штат ажратилмайди («Шогиртлар», «Св.шт.» — штатдан ташқари): уларнинг ходимлари рўйхатга
# қўшилади, лекин штат ва бўш иш ўринларига кирмайди. Бўлим номининг боши бўйича аниқланади (катта-кичик ҳарф ва
# қўшимча бўшлиқлар аҳамиятсиз), шунда ҳар бир цехдаги ва янги қўшиладиган шундай бўлим ўзи шундай ҳисобланади.
NO_STAFF_SECTION_PREFIXES = ('шогиртлар', 'шогирдлар', 'shogirtlar', 'shogirdlar')
# «св.шт.» — «сверх штата» (штатдан ташқари): «св.шт», «св. шт.», «СВ ШТ», «сверх штата», «sv.sht». «Св» ёки «шт»
# дан кейин ҳарф келса (масалан, «Св штатный…») мос келмайди — фақат шу қисқартма ва ибора.
NO_STAFF_ABBREVIATION = re.compile(r'^(?:св\.?\s*шт(?![а-яёўқғҳ])|сверх\s*штат|sv\.?\s*sht(?![a-z]))')
# Клавиатурада кирилл ўрнига лотин ҳарфи ёзилган бўлиши мумкин («CB.ШТ.»): ташқи кўриниши бир хил ҳарфлар.
LATIN_LOOKALIKES = str.maketrans({'c': 'с', 'b': 'в', 't': 'т'})


def is_no_staff_section_name(name):
    normalized = normalize_position(name)
    if normalized.startswith(NO_STAFF_SECTION_PREFIXES) or NO_STAFF_ABBREVIATION.match(normalized):
        return True
    # «cb.шт.» (лотин c, b) — кирилл «св.шт.» деб ўқилади; «sv.sht» эса юқорида алоҳида аниқланган.
    return bool(NO_STAFF_ABBREVIATION.match(normalized.translate(LATIN_LOOKALIKES)))


def no_staff_section_ids(section_ids):
    """Какие из отделов section_ids — без штата (по названию)."""
    names = Section.objects.filter(pk__in=section_ids).values_list('id', 'name')
    return {section_id for section_id, name in names if is_no_staff_section_name(name)}


@dataclass
class StaffStats:
    # (тип единицы, id единицы, нормализованная должность) -> сумма долей ставки сотрудников с таким названием
    occupied: dict = field(default_factory=lambda: defaultdict(int))
    # id штатной должности -> сколько ставок из них приходится именно на неё (привязанные + доля остальных)
    by_position: dict = field(default_factory=dict)
    # (тип единицы, id единицы) -> {'staff': штат, 'vacant': вакансии} только по своим должностям
    own: dict = field(default_factory=lambda: defaultdict(lambda: {'staff': 0, 'vacant': 0}))

    def occupied_for(self, position):
        """Занятые места именно этой штатной должности (если в единице есть одноимённые — их доля)."""
        return self.by_position.get(position.pk, 0)


def to_decimal(value):
    """Decimal из int/Decimal/float/str (float — через str, чтобы 0.1 не превращалось в 0.1000000000000000055…)."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def vacant_places(staff_count, occupied):
    """Свободные места: штат (может быть дробным: 0,5 / 1,25) минус занятые, но не меньше нуля."""
    return max(to_decimal(staff_count) - to_decimal(occupied), Decimal(0))


DEFAULT_STAFF_RATE = Decimal('1')
MAX_STAFF_RATE = Decimal('2')
# Порядок сотрудников в единице: по нему одноимённые должности делят непривязанных (и в подсчёте, и в списке).
EMPLOYEE_ORDER = ('last_name', 'first_name', 'surname', 'tabel_number')


def employee_rate(employee):
    """Доля ставки сотрудника (Employee или само значение); пусто — полная ставка."""
    value = getattr(employee, 'staff_rate', employee)
    return DEFAULT_STAFF_RATE if value in (None, '') else to_decimal(value)


def occupied_rate(employees):
    """Сколько ставок занимают сотрудники: сумма их долей (1 + 0,5 = 1,5)."""
    return sum((employee_rate(employee) for employee in employees), Decimal(0))


def format_rate(value):
    """0.5 -> «0,5», 1.00 -> «1»: для сообщений (на странице числа тоже с запятой)."""
    return str(plain_number(value)).replace('.', ',')


def parse_staff_rate(value):
    """Доля ставки из формы: «1», «0.5», «0,5», 0.5; пусто — 1. Ошибка — ValueError с текстом для пользователя."""
    if value is None or str(value).strip() == '':
        return DEFAULT_STAFF_RATE
    try:
        rate = Decimal(str(value).strip().replace(',', '.'))
    except InvalidOperation:
        raise ValueError('Штат улушини сон билан киритинг (масалан, 1 ёки 0,5).') from None
    if not rate.is_finite() or rate <= 0:
        raise ValueError('Штат улуши 0 дан катта бўлиши керак (масалан, 1 ёки 0,5).')
    if rate > MAX_STAFF_RATE:
        raise ValueError(f'Штат улуши {format_rate(MAX_STAFF_RATE)} дан ошмаслиги керак.')
    if rate != rate.quantize(Decimal('0.01')):
        raise ValueError('Штат улушида вергулдан кейин энг кўпи билан 2 та рақам бўлиши мумкин (масалан, 0,25).')
    return rate.quantize(Decimal('0.01'))


def place_capacity(staff_count):
    """Сколько сотрудников вмещает должность: штат округляется вверх (0,5 → 1; 1,5 → 2).

    Так же работает и назначение: сотрудника можно добавить, пока занято меньше, чем штат.
    """
    return int(to_decimal(staff_count).to_integral_value(rounding=ROUND_CEILING))


def distribute_unbound(staff_counts, bound_counts, unbound):
    """Сколько непривязанных сотрудников достаётся каждой из одноимённых должностей одной единицы.

    Сотрудник, назначенный через страницу штата, привязан к своей строке (Employee.staff_position) и
    в этом распределении не участвует. Остальные (должность введена в карточке или импортом) различаются
    только по названию: они занимают свободные места строк по порядку id, всё сверх штата — на последней.

    bound_counts — сколько ставок уже занято привязанными в каждой строке; unbound — доли ставки
    непривязанных по порядку (EMPLOYEE_ORDER) или просто их число (каждый — полная ставка).
    Строка берёт очередного сотрудника, пока в ней есть свободное место (как и раньше: 0,5 штата — один
    человек с полной ставкой, 1,5 — двое), поэтому при долях 1 результат прежний.
    """
    rates = [DEFAULT_STAFF_RATE] * unbound if isinstance(unbound, int) else [to_decimal(rate) for rate in unbound]
    shares = []
    start = 0
    for index, (staff_count, bound) in enumerate(zip(staff_counts, bound_counts)):
        if index == len(staff_counts) - 1:
            taken = len(rates) - start
        else:
            used, limit, taken = to_decimal(bound), to_decimal(staff_count), 0
            while start + taken < len(rates) and used < limit:
                used += rates[start + taken]
                taken += 1
        shares.append(taken)
        start += taken
    return shares


def split_occupancy(staff_counts, total):
    """Распределение `total` сотрудников без привязки между одноимёнными должностями (см. distribute_unbound)."""
    return distribute_unbound(staff_counts, [0] * len(staff_counts), total)


def is_bound_to(employee_staff_position_id, group_ids):
    """Привязка учитывается, только если строка — одна из одноимённых должностей той же единицы.

    Если сотруднику потом поменяли цех, отдел или название должности в карточке, старая привязка
    просто перестаёт действовать, и сотрудник считается по названию, как раньше.
    """
    return employee_staff_position_id is not None and employee_staff_position_id in group_ids


def plain_number(value):
    """Decimal → int, если число целое, иначе float: шаблон и JSON показывают 3 или 3.5, а не 3.00 или строку '3.50'."""
    value = to_decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def collect_staff_stats():
    """Один проход по должностям и по активным сотрудникам с заполненной должностью."""
    stats = StaffStats()
    positions = list(StaffPosition.objects.order_by('id').values('id', 'department_id', 'section_id', 'name', 'staff_count'))
    # Штат ажратилмайдиган бўлимлар («Шогиртлар») ҳисобга кирмайди: уларда лавозим бўлса ҳам, штат ва бўш жой 0 қолади.
    skipped = no_staff_section_ids({position['section_id'] for position in positions if position['section_id']})
    if skipped:
        positions = [position for position in positions if position['section_id'] not in skipped]
    if not positions:
        return stats

    # Одноимённые должности одной единицы — одна группа (порядок строк — по id).
    groups = defaultdict(list)
    for position in positions:
        unit = position_unit(position['department_id'], position['section_id'])
        groups[(*unit, normalize_position(position['name']))].append(position)
    group_ids = {key: {row['id'] for row in rows} for key, rows in groups.items()}

    bound = defaultdict(Decimal)   # id строки -> ставки привязанных к ней сотрудников
    unbound = defaultdict(list)    # группа -> доли ставки сотрудников без (действующей) привязки, по EMPLOYEE_ORDER
    employees = (
        Employee.objects
        .filter(is_deleted=False, is_active=True)
        .exclude(position='')
        .order_by(*EMPLOYEE_ORDER)   # тот же порядок, что в employees_of_position: доли делятся одинаково
        .values_list('department_id', 'section_id', 'position', 'staff_position_id', 'staff_rate')
    )
    for department_id, section_id, position, staff_position_id, staff_rate in employees.iterator():
        key = (*position_unit(department_id, section_id), normalize_position(position))
        if key not in groups:
            continue
        rate = employee_rate(staff_rate)
        stats.occupied[key] += rate
        if is_bound_to(staff_position_id, group_ids[key]):
            bound[staff_position_id] += rate
        else:
            unbound[key].append(rate)

    for key, rows in groups.items():
        unit = key[:2]
        rates = unbound[key]
        shares = distribute_unbound(
            [row['staff_count'] for row in rows], [bound[row['id']] for row in rows], rates,
        )
        start = 0
        for row, share in zip(rows, shares):
            occupied = bound[row['id']] + sum(rates[start:start + share], Decimal(0))
            start += share
            stats.by_position[row['id']] = occupied
            stats.own[unit]['staff'] += row['staff_count']
            stats.own[unit]['vacant'] += vacant_places(row['staff_count'], occupied)

    return stats


def unit_positions(position):
    """Штатные должности той же единицы (цеха или отдела), что и position, по порядку id."""
    queryset = StaffPosition.objects.all()
    if position.section_id:
        queryset = queryset.filter(section_id=position.section_id)
    else:
        queryset = queryset.filter(department_id=position.department_id, section__isnull=True)
    return queryset.order_by('id')


def employees_with_position_name(position):
    """Все сотрудники единицы с названием должности, как у position: общий пул одноимённых строк.

    Те же правила, что и при подсчёте «занято» в collect_staff_stats.
    """
    queryset = Employee.objects.filter(is_deleted=False, is_active=True)
    if position.section_id:
        queryset = queryset.filter(section_id=position.section_id)
    else:
        queryset = queryset.filter(department_id=position.department_id, section__isnull=True)

    wanted = normalize_position(position.name)
    queryset = queryset.select_related('department', 'section').order_by(*EMPLOYEE_ORDER)
    return [employee for employee in queryset if normalize_position(employee.position) == wanted]


def employees_of_position(position):
    """Сотрудники, занимающие именно эту штатную должность.

    Если в единице есть одноимённые должности: привязанные к этой строке сотрудники + её доля
    непривязанных (distribute_unbound) — так же, как «занято» в collect_staff_stats, поэтому число
    сотрудников в списке всегда совпадает с таблицей.
    """
    employees = employees_with_position_name(position)
    wanted = normalize_position(position.name)
    group = [item for item in unit_positions(position) if normalize_position(item.name) == wanted]
    if len(group) < 2:
        return employees

    group_ids = {item.pk for item in group}
    unbound = [employee for employee in employees if not is_bound_to(employee.staff_position_id, group_ids)]
    bound_rates = [occupied_rate(employee for employee in employees if employee.staff_position_id == item.pk) for item in group]
    shares = distribute_unbound(
        [item.staff_count for item in group], bound_rates, [employee_rate(employee) for employee in unbound],
    )
    index = next(i for i, item in enumerate(group) if item.pk == position.pk)
    start = sum(shares[:index])
    members = {employee.pk for employee in unbound[start:start + shares[index]]}
    # Порядок как в общем списке (по фамилии): привязанные и доля непривязанных вперемешку.
    return [employee for employee in employees if employee.staff_position_id == position.pk or employee.pk in members]


def section_employees(section):
    """Все активные сотрудники отдела — список отдела без штата («Шогиртлар»), где должности не нужны."""
    return list(
        Employee.objects
        .filter(section=section, is_deleted=False, is_active=True)
        .select_related('department', 'section')
        .order_by('last_name', 'first_name', 'surname', 'tabel_number')
    )


def assign_employee_to_section(employee, section):
    """Добавить сотрудника в отдел без штата: меняются только цех и отдел, название должности остаётся как было."""
    employee.department_id = section.department_id
    employee.section_id = section.pk
    employee.staff_position = None   # в отделе без штата штатной строки нет
    employee.save(update_fields=['department', 'section', 'staff_position', 'updated_at'])


def remove_employee_from_section(employee):
    """Убрать сотрудника из отдела без штата: он остаётся в цехе, очищается только отдел."""
    employee.section_id = None
    employee.save(update_fields=['section', 'updated_at'])


def assign_employee_to_position(employee, position, rate=None):
    """Назначить сотрудника на должность: цех, отдел и название должности берутся из штатной единицы.

    Сотрудник привязывается именно к этой строке (staff_position): среди одноимённых должностей
    он остаётся там, куда его добавили. rate — доля ставки (None — оставить прежнюю, например при переводе).
    Другие поля (в том числе «Дата изменения должности») не трогаем.
    """
    employee.department_id = position.department_id
    employee.section_id = position.section_id
    employee.position = position.name
    employee.staff_position = position
    if rate is not None:
        employee.staff_rate = rate
    employee.save(update_fields=['department', 'section', 'position', 'staff_position', 'staff_rate', 'updated_at'])


def remove_employee_from_position(employee):
    """Снять сотрудника с должности: очищаем название должности и привязку, цех и отдел остаются.

    Доля ставки возвращается к полной: она относилась к этой должности.
    """
    employee.position = ''
    employee.staff_position = None
    employee.staff_rate = DEFAULT_STAFF_RATE
    employee.save(update_fields=['position', 'staff_position', 'staff_rate', 'updated_at'])


def unit_position_options(position, stats):
    """Все должности того же цеха/отдела с числом свободных мест — для перевода внутри единицы."""
    return [
        {
            'id': item.pk,
            'name': item.name,
            'staff_count': item.staff_count,
            'vacant': vacant_places(item.staff_count, stats.occupied_for(item)),
        }
        for item in unit_positions(position)
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
            'no_staff': is_no_staff_section_name(section.name),
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
