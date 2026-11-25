import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0039_employee_replacement'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TimekeeperApiToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key_hash', models.CharField(max_length=64, unique=True, verbose_name='Токен хэши (SHA-256)')),
                ('auth_stamp', models.CharField(help_text='Парол ўзгарса, токен ўз-ўзидан ярамай қолади', max_length=128, verbose_name='Парол белгиси')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Яратилган')),
                ('expires_at', models.DateTimeField(db_index=True, verbose_name='Амал қилиш муддати')),
                ('last_used_at', models.DateTimeField(blank=True, null=True, verbose_name='Охирги марта ишлатилган')),
                ('created_ip', models.CharField(blank=True, default='', max_length=45, verbose_name='Олинган IP')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='timekeeper_api_tokens', to=settings.AUTH_USER_MODEL, verbose_name='Фойдаланувчи')),
            ],
            options={
                'verbose_name': 'API токен (табелчи)',
                'verbose_name_plural': 'API токенлар (табелчи)',
                'ordering': ['-created_at'],
            },
        ),
    ]
