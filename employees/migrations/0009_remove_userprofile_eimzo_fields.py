from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0008_employee_date_of_birth'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='eimzo_pinfl',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='eimzo_serial_number',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='eimzo_subject_name',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='eimzo_tin',
        ),
    ]
