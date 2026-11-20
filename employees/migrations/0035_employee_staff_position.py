"""Ходим айнан қайси штат қаторига бириктирилган (бир хил номли лавозимлар учун).

Бир цех/бўлимда бир хил номли лавозимлар бўлса, ҳозир кўриниб турган тақсимот (қаторлар id тартибида,
ходимлар фамилия бўйича) шу ерда қотирилади: миграциядан кейин саҳифадаги рақамлар ва рўйхатлар ўзгармайди,
ходимларни эса бир қатордан иккинчисига аниқ кўчириш мумкин бўлади. Ягона номли лавозимлар ходимларига
тегилмайди (уларда тақсимлаш йўқ).
"""
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

import django.db.models.deletion
from django.db import migrations, models


# Миграция кейинги код ўзгаришларига боғлиқ бўлмаслиги учун staff.py дагилардан нусха.
NO_STAFF_SECTION_PREFIXES = ('шогиртлар', 'шогирдлар', 'shogirtlar', 'shogirdlar')


def normalize(value):
    return ' '.join(str(value or '').split()).casefold()


def capacity(staff_count):
    value = staff_count if isinstance(staff_count, Decimal) else Decimal(str(staff_count))
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def split(staff_counts, total):
    shares, remaining = [], total
    for index, staff_count in enumerate(staff_counts):
        taken = remaining if index == len(staff_counts) - 1 else min(remaining, capacity(staff_count))
        shares.append(taken)
        remaining -= taken
    return shares


def bind_same_name_rows(apps, schema_editor):
    StaffPosition = apps.get_model('employees', 'StaffPosition')
    Employee = apps.get_model('employees', 'Employee')
    Section = apps.get_model('employees', 'Section')

    no_staff = {pk for pk, name in Section.objects.values_list('id', 'name') if normalize(name).startswith(NO_STAFF_SECTION_PREFIXES)}
    groups = defaultdict(list)
    for row in StaffPosition.objects.order_by('id').values('id', 'department_id', 'section_id', 'name', 'staff_count'):
        if row['section_id'] in no_staff:
            continue
        unit = ('section', row['section_id']) if row['section_id'] else ('department', row['department_id'])
        groups[(unit, normalize(row['name']))].append(row)

    for (unit, name), rows in groups.items():
        if len(rows) < 2:
            continue
        employees = Employee.objects.filter(is_deleted=False, is_active=True)
        if unit[0] == 'section':
            employees = employees.filter(section_id=unit[1])
        else:
            employees = employees.filter(department_id=unit[1], section__isnull=True)
        ordered = employees.order_by('last_name', 'first_name', 'surname', 'tabel_number').values_list('id', 'position')
        pool = [pk for pk, position in ordered if normalize(position) == name]

        start = 0
        for row, share in zip(rows, split([row['staff_count'] for row in rows], len(pool))):
            ids = pool[start:start + share]
            start += share
            if ids:
                Employee.objects.filter(pk__in=ids).update(staff_position_id=row['id'])


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0034_userprofile_face_id_login_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='staff_position',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='assigned_employees', to='employees.staffposition', verbose_name='Штатная должность',
            ),
        ),
        migrations.RunPython(bind_same_name_rows, migrations.RunPython.noop),
    ]
