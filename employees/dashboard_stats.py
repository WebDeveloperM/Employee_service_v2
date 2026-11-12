"""Данные диаграмм главной страницы: штат и вакансии, возраст, пол.

Только читает базу. Считаются активные (не удалённые) сотрудники — так же, как «занято» в штате.
"""
from django.utils import timezone

from .models import Employee
from .staff import collect_staff_stats


# Сайт палитраси (base.html даги --chart-blue ва --accent билан бир хил): кўк ва тўқ сариқ, ёш гуруҳлари учун кўкнинг туслари.
BLUE = '#2a78d6'
LIGHT_BLUE = '#7fb2ec'
DARK_BLUE = '#1b4a96'
ORANGE = '#eb6834'
GRAY = '#94a3b8'

# (название, от, до включительно (None — без верхней границы), цвет)
AGE_GROUPS = (
    ('18 ёшгача', 0, 17, GRAY),
    ('18-24 ёш', 18, 24, LIGHT_BLUE),
    ('25-40 ёш', 25, 40, BLUE),
    ('41-60 ёш', 41, 60, DARK_BLUE),
    ('60 ёшдан катта', 61, None, ORANGE),
)


def active_employees():
    return Employee.objects.filter(is_deleted=False, is_active=True)


def age_on(today, born):
    """Полных лет на дату today."""
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def make_chart(key, title, rows, slices=None, note=''):
    """rows — строки таблицы под диаграммой; slices — доли круга (по умолчанию все строки)."""
    return {'key': key, 'title': title, 'rows': rows, 'slices': rows if slices is None else slices, 'note': note}


def row(label, value, color=None):
    return {'label': label, 'value': value, 'color': color}


def staff_chart():
    stats = collect_staff_stats()
    total = sum(unit['staff'] for unit in stats.own.values())
    vacant = sum(unit['vacant'] for unit in stats.own.values())
    occupied = total - vacant
    occupied_row = row('Банд ўринлар', occupied, BLUE)
    vacant_row = row('Бўш иш ўринлари', vacant, ORANGE)
    return make_chart(
        'staff', 'Штат бирлиги & Бўш иш ўринлари',
        rows=[row('Штат бирлиги', total), occupied_row, vacant_row],
        slices=[occupied_row, vacant_row],
    )


def age_chart(today):
    counts = [0] * len(AGE_GROUPS)
    unknown = 0
    for born in active_employees().values_list('date_of_birth', flat=True).iterator():
        age = age_on(today, born) if born else -1
        if age < 0:
            unknown += 1
            continue
        for index, (_, low, high, _) in enumerate(AGE_GROUPS):
            if age >= low and (high is None or age <= high):
                counts[index] += 1
                break

    # «18 ёшгача» (low == 0) фақат шундай ходим бўлса кўрсатилади; қолган гуруҳлар доим кўринади.
    rows = [row(label, count, color) for (label, low, _, color), count in zip(AGE_GROUPS, counts) if count or low > 0]
    note = f'Туғилган санаси киритилмаган: {unknown}' if unknown else ''
    return make_chart('age', 'Ёш бўйича статистика', rows, note=note)


def gender_chart():
    genders = {'M': 0, 'F': 0}
    unknown = 0
    for gender in active_employees().values_list('gender', flat=True).iterator():
        if gender in genders:
            genders[gender] += 1
        else:
            unknown += 1
    rows = [row('Эркаклар', genders['M'], BLUE), row('Аёллар', genders['F'], ORANGE)]
    note = f'Жинси кўрсатилмаган: {unknown}' if unknown else ''
    return make_chart('gender', 'Эркаклар & Аёллар', rows, note=note)


def build_dashboard_charts(*, include_staff, today=None):
    """Штат — фақат администратор/HR учун (худди штат API си каби)."""
    today = today or timezone.localdate()
    charts = [staff_chart()] if include_staff else []
    charts.extend([age_chart(today), gender_chart()])
    return charts
