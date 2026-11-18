from django.db import migrations


class Migration(migrations.Migration):
    """Иш тартиби: 3/3 — маълумот кўчирилгач, эски матнли устунни ўчириш."""

    dependencies = [
        ('employees', '0031_work_schedule_data'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='employee',
            name='work_schedule_code',
        ),
    ]
