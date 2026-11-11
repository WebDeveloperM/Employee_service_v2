from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0016_employeecertificate'),
    ]

    operations = [
        # Nullable with no default: existing rows simply get NULL, no data is
        # rewritten and no value has to be backfilled.
        migrations.AddField(
            model_name='employee',
            name='grade',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Разряд'),
        ),
    ]
