from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0026_employee_grade_text'),
    ]

    operations = [
        # Nullable, default йўқ: мавжуд ходимларга NULL (танланмаган) ёзилади, маълумот қайта ёзилмайди.
        migrations.AddField(
            model_name='employee',
            name='work_schedule',
            field=models.CharField(blank=True, choices=[('five_day', '5 кунлик иш ҳафтасида'), ('shift', 'Сменада')], max_length=20, null=True, verbose_name='Иш тартиби'),
        ),
    ]
