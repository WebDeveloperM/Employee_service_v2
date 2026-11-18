from django.db import migrations

# Эски қатъий вариантлар (0027): код -> рўйхатдаги номи. Тартиб — рўйхатдаги тартиб.
CODE_TO_NAME = {
    'five_day': '5 кунлик иш ҳафтасида',
    'shift': 'Сменада',
}


def codes_to_schedules(apps, schema_editor):
    WorkSchedule = apps.get_model('employees', 'WorkSchedule')
    Employee = apps.get_model('employees', 'Employee')
    for code, name in CODE_TO_NAME.items():
        schedule, _ = WorkSchedule.objects.get_or_create(name=name)
        Employee.objects.filter(work_schedule_code=code).update(work_schedule=schedule)


def schedules_to_codes(apps, schema_editor):
    # Орқага қайтиш: фақат эски иккита вариант кодга қайтади. Администратор қўшган янги иш тартиби
    # матнли устунда ифодаланмайди — у ходимларда «танланмаган» бўлиб қолади.
    WorkSchedule = apps.get_model('employees', 'WorkSchedule')
    Employee = apps.get_model('employees', 'Employee')
    for code, name in CODE_TO_NAME.items():
        schedule = WorkSchedule.objects.filter(name=name).first()
        if schedule is not None:
            Employee.objects.filter(work_schedule=schedule).update(work_schedule_code=code)


class Migration(migrations.Migration):
    """Иш тартиби: 2/3 — бошланғич рўйхатни яратиш ва ходимларнинг эски қийматларини кўчириш (маълумот сақланади)."""

    dependencies = [
        ('employees', '0030_work_schedule_model'),
    ]

    operations = [
        migrations.RunPython(codes_to_schedules, schedules_to_codes),
    ]
