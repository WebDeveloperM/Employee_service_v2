from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0012_employee_new_size_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='employee',
            name='jshshir',
        ),
        migrations.RemoveField(
            model_name='employee',
            name='passport_number',
        ),
        migrations.RemoveField(
            model_name='employee',
            name='passport_series',
        ),
    ]