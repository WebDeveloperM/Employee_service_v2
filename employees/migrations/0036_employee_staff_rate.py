"""Ходим неча штат бирлигида ишлайди (1 — тўлиқ ставка, 0,5 — ярим ставка).

Мавжуд ходимлар учун 1: «Банд» олдин ҳам ҳар бир ходимни 1 деб санарди, рақамлар ўзгармайди.
"""
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0035_employee_staff_position'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='staff_rate',
            field=models.DecimalField(decimal_places=2, default=Decimal('1'), max_digits=4, verbose_name='Доля ставки'),
        ),
    ]
