"""Общая сортировка отделов по «ID отдела» (поле Section.code).

Лежит отдельно от template_views, чтобы её могли подключать и staff.py, и представления
(template_views -> serializers -> staff, поэтому staff.py не может импортировать template_views).
"""
from django.db import models
from django.db.models.functions import Cast


NUMERIC_CODE_REGEX = r'^[0-9]{1,18}$'


def order_sections_by_code(queryset, group_by_department=True):
    """Отделы по «ID отдела» (поле code).

    code — текст, поэтому обычная сортировка дала бы 1, 10, 2. Порядок такой:
    числовые коды по возрастанию (1, 2, 10), затем нечисловые, затем отделы без
    кода (по названию). Для стабильной пагинации последним идёт pk.

    group_by_department=True сначала группирует по цехам (для общих списков); для отделов
    одного цеха (например, внутри Prefetch) группировка не нужна.
    """
    is_numeric = models.Q(code__regex=NUMERIC_CODE_REGEX)
    ordering = ['code_group', 'code_number', 'code', 'name', 'pk']
    if group_by_department:
        ordering = ['department__sort_order', 'department__name', *ordering]

    return queryset.annotate(
        code_group=models.Case(
            models.When(is_numeric, then=models.Value(0)),
            models.When(code='', then=models.Value(2)),
            default=models.Value(1),
            output_field=models.IntegerField(),
        ),
        code_number=models.Case(
            models.When(is_numeric, then=Cast('code', models.BigIntegerField())),
            default=None,
            output_field=models.BigIntegerField(),
        ),
    ).order_by(*ordering)
