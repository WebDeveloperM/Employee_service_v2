from django.db import migrations, models


def timekeeper_accounts_to_plain_users(apps, schema_editor):
    """Олдинги «Табелчи» ҳисоблари оддий «Фойдаланувчи» бўлади (ҳисоб ва тайинлов — Timekeeper — сақланади)."""
    UserProfile = apps.get_model('employees', 'UserProfile')
    UserProfile.objects.filter(role='timekeeper').update(role='user')


class Migration(migrations.Migration):
    """«Табелчи» роли олиб ташланди: табелчи ES га логин/парол билан кирмайди, бошқа тизим орқали ишлайди (API_TIMEKEEPER.md).

    Орқага қайтарилса, рол қайтмайди (ким табелчи бўлгани Timekeeper жадвалида сақланади).
    """

    dependencies = [
        ('employees', '0041_remove_timekeeper_api_token'),
    ]

    operations = [
        migrations.RunPython(timekeeper_accounts_to_plain_users, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(choices=[('admin', 'Администратор'), ('hr', 'HR менежер'), ('user', 'Фойдаланувчи')], default='user', max_length=20, verbose_name='Роль'),
        ),
    ]
