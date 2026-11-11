from django.db import migrations, models


def copy_clothe_size_to_special_clothing(apps, schema_editor):
    Employee = apps.get_model('employees', 'Employee')
    for employee in Employee.objects.exclude(clothe_size='').iterator():
        employee.special_clothing_size = employee.clothe_size
        employee.save(update_fields=['special_clothing_size'])


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0011_department_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='special_clothing_size',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Спецодежда'),
        ),
        migrations.AddField(
            model_name='employee',
            name='jacket_size',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Куртка'),
        ),
        migrations.AddField(
            model_name='employee',
            name='tshirt_size',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Футболка'),
        ),
        migrations.RunPython(copy_clothe_size_to_special_clothing, migrations.RunPython.noop),
    ]