import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Иш тартиби: қатъий вариантлар ўрнига рўйхат (жадвал). 1/3 — фақат схема.

    Эски матнли устун (five_day/shift) вақтинча work_schedule_code бўлиб қолади; маълумот 0031 да кўчирилади,
    устун 0032 да ўчирилади. Схема ва маълумот алоҳида миграцияларда: PostgreSQL бир транзакцияда
    «жадвални ўзгартириш + қаторларни янгилаш»ни «pending trigger events» хатоси билан рад этиши мумкин.
    """

    dependencies = [
        ('employees', '0029_employee_category'),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='Название режима работы')),
            ],
            options={'ordering': ['id']},
        ),
        migrations.RenameField(
            model_name='employee',
            old_name='work_schedule',
            new_name='work_schedule_code',
        ),
        migrations.AddField(
            model_name='employee',
            name='work_schedule',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='employees', to='employees.workschedule', verbose_name='Режим работы',
            ),
        ),
    ]
