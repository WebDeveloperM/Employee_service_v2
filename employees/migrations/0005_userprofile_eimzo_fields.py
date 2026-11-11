from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0004_userprofile_face_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='eimzo_pinfl',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='E-IMZO ПИНФЛ'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='eimzo_serial_number',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='E-IMZO серийный номер'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='eimzo_subject_name',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='E-IMZO владелец сертификата'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='eimzo_tin',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='E-IMZO ИНН'),
        ),
    ]