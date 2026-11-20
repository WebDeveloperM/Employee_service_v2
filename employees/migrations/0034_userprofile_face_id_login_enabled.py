from django.db import migrations, models


class Migration(migrations.Migration):
    """«Face ID орқали кириш» ходим учун ёқилади/ўчирилади. Мавжуд профиллар учун ёқилган (аввалги хатти-ҳаракат сақланади)."""

    dependencies = [
        ('employees', '0033_staffposition_allow_same_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='face_id_login_enabled',
            field=models.BooleanField(default=True, verbose_name='Вход с проверкой Face ID'),
        ),
    ]
