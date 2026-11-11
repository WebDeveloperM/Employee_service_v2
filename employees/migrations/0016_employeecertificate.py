from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0015_bnpzidclient'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeCertificate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('serial_number', models.CharField(max_length=64, unique=True, verbose_name='Серийный номер')),
                ('fingerprint_sha256', models.CharField(max_length=64, unique=True, verbose_name='Отпечаток SHA-256')),
                ('issued_at', models.DateTimeField(auto_now_add=True, verbose_name='Выдан')),
                ('expires_at', models.DateTimeField(verbose_name='Действителен до')),
                ('revoked', models.BooleanField(default=False, verbose_name='Отозван')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='Дата отзыва')),
                ('issued_by_username', models.CharField(blank=True, default='', max_length=150, verbose_name='Кем выдан')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='certificates', to='employees.employee', verbose_name='Сотрудник')),
            ],
            options={
                'verbose_name': 'Ключ доступа сотрудника',
                'verbose_name_plural': 'Ключи доступа сотрудников',
                'ordering': ['-issued_at'],
            },
        ),
    ]
