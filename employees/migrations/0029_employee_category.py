import django.db.models.deletion
from django.db import migrations, models

DEFAULT_CATEGORIES = [
    'Бошқарув ходимлари',
    'Мутахассислар',
    'Техник ходимлар',
    'Хизмат кўрсатувчи ходимлар',
    'Ишлаб чиқариш ходимлари',
]


def seed_categories(apps, schema_editor):
    EmployeeCategory = apps.get_model('employees', 'EmployeeCategory')
    for name in DEFAULT_CATEGORIES:
        EmployeeCategory.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0028_position_category'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='Название категории сотрудников')),
            ],
            options={'ordering': ['id']},
        ),
        # Nullable, default йўқ: мавжуд ходимларга NULL (танланмаган) ёзилади, маълумот қайта ёзилмайди.
        migrations.AddField(
            model_name='employee',
            name='employee_category',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='employees', to='employees.employeecategory', verbose_name='Категория сотрудников',
            ),
        ),
        # Бошланғич тоифалар. Жадвал янги, шунинг учун орқага қайтганда уни ўчириш ўзи тозалайди.
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]
