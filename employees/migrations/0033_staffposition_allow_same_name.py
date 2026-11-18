from django.db import migrations


class Migration(migrations.Migration):
    """Штат: в одном цехе/отделе можно завести должность с тем же названием ещё раз.

    Убираем два условных UniqueConstraint (цех+название и отдел+название). Данные не меняются.
    Орқага қайтиш фақат бир хил номли қатор йўқ бўлса ишлайди (унда чеклов яна қўшилади).
    """

    dependencies = [
        ('employees', '0032_remove_work_schedule_code'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='staffposition',
            name='employees_staffposition_department_name_unique',
        ),
        migrations.RemoveConstraint(
            model_name='staffposition',
            name='employees_staffposition_section_name_unique',
        ),
    ]
