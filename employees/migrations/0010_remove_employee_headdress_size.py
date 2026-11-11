from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0009_remove_userprofile_eimzo_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='employee',
            name='headdress_size',
        ),
    ]