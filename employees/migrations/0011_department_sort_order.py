import re

from django.db import migrations, models


def populate_department_sort_order(apps, schema_editor):
    Department = apps.get_model('employees', 'Department')
    departments = list(Department.objects.all().order_by('name', 'id'))
    max_sort_order = 0

    for department in departments:
        match = re.match(r'^\s*(\d+)', department.name or '')
        if not match:
            continue

        department.sort_order = int(match.group(1))
        department.save(update_fields=['sort_order'])
        if department.sort_order > max_sort_order:
            max_sort_order = department.sort_order

    for department in departments:
        department.refresh_from_db(fields=['sort_order'])
        if department.sort_order:
            continue

        max_sort_order += 1
        department.sort_order = max_sort_order
        department.save(update_fields=['sort_order'])


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0010_remove_employee_headdress_size'),
    ]

    operations = [
        migrations.AddField(
            model_name='department',
            name='sort_order',
            field=models.PositiveIntegerField(db_index=True, default=0, verbose_name='Порядок сортировки'),
        ),
        migrations.AlterModelOptions(
            name='department',
            options={'ordering': ['sort_order', 'name']},
        ),
        migrations.AlterModelOptions(
            name='section',
            options={'ordering': ['department__sort_order', 'department__name', 'name'], 'unique_together': {('department', 'name')}},
        ),
        migrations.RunPython(populate_department_sort_order, migrations.RunPython.noop),
    ]