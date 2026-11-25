from django.db import migrations


class Migration(migrations.Migration):
    """Табелчи токенлари олиб ташланди: табелчи номидан бошқа тизим сервис калити билан ишлайди (API_TIMEKEEPER.md)."""

    dependencies = [
        ('employees', '0040_timekeeper_api_token'),
    ]

    operations = [
        migrations.DeleteModel(name='TimekeeperApiToken'),
    ]
