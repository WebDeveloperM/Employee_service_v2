from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0014_employeebaseimagechangelog'),
    ]

    operations = [
        migrations.CreateModel(
            name='BnpzIdClient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('client_id', models.CharField(max_length=100, unique=True, verbose_name='Client ID')),
                ('name', models.CharField(max_length=200, verbose_name='Название')),
                ('client_secret', models.CharField(max_length=255, verbose_name='Client Secret')),
                ('allowed_redirects', models.TextField(
                    blank=True,
                    default='',
                    help_text='По одному URI на строку',
                    verbose_name='Разрешённые redirect URI',
                )),
                ('access_check_url', models.URLField(
                    blank=True,
                    default='',
                    help_text='Оставьте пустым, чтобы разрешить доступ всем аутентифицированным пользователям',
                    verbose_name='URL проверки доступа',
                )),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлён')),
            ],
            options={
                'verbose_name': 'bnpzID клиент',
                'verbose_name_plural': 'bnpzID клиенты',
                'ordering': ['client_id'],
            },
        ),
    ]
