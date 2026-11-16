from django.db import migrations, models


def strip_grade_star(apps, schema_editor):
    # Орқага қайтаришда устун яна рақамга айланади: «12*» → «12», акс ҳолда cast хато беради.
    Employee = apps.get_model('employees', 'Employee')
    for employee in Employee.objects.filter(grade__endswith='*').only('pk', 'grade'):
        employee.grade = employee.grade.rstrip('*')
        employee.save(update_fields=['grade'])


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0025_employee_work_status'),
    ]

    operations = [
        # Разряд энди «12*» каби ёзилиши мумкин. Мавжуд рақамлар матнга ўтказилади (12 → '12'), NULL ўзгармайди.
        migrations.AlterField(
            model_name='employee',
            name='grade',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='Разряд'),
        ),
        migrations.RunPython(migrations.RunPython.noop, strip_grade_star),
    ]
