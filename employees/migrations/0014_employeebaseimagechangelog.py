from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0013_remove_employee_document_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeBaseImageChangeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee_full_name', models.CharField(blank=True, default='', max_length=255)),
                ('employee_tabel_number', models.CharField(blank=True, default='', max_length=255)),
                ('changed_by_username', models.CharField(blank=True, default='', max_length=150)),
                ('changed_by_user_id', models.CharField(blank=True, default='', max_length=64)),
                ('changed_by_role', models.CharField(blank=True, default='', max_length=64)),
                ('old_image', models.CharField(blank=True, default='', max_length=500)),
                ('new_image', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='base_image_change_logs', to='employees.employee')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
