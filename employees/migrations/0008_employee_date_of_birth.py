from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0007_add_employee_user_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True, verbose_name='Дата рождения'),
        ),
    ]
