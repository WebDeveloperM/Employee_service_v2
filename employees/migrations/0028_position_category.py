import django.db.models.deletion
from django.db import migrations, models

DEFAULT_CATEGORIES = ['Раҳбар', 'Мутахассис', 'Ишчи', 'Хизматчи']


def seed_categories(apps, schema_editor):
    PositionCategory = apps.get_model('employees', 'PositionCategory')
    for name in DEFAULT_CATEGORIES:
        PositionCategory.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0027_employee_work_schedule'),
    ]

    operations = [
        migrations.CreateModel(
            name='PositionCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='Название категории должности')),
            ],
            options={'ordering': ['id']},
        ),
        # Nullable, default йўқ: мавжуд ходимларга NULL (танланмаган) ёзилади, маълумот қайта ёзилмайди.
        migrations.AddField(
            model_name='employee',
            name='position_category',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='employees', to='employees.positioncategory', verbose_name='Категория должности',
            ),
        ),
        # Бошланғич тоифалар. Жадвал янги, шунинг учун орқага қайтганда уни ўчириш ўзи тозалайди.
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]
