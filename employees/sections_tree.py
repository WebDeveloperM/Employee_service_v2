"""Иерархия отделов (Section.parent): индекс в памяти, пути, поддеревья и порядок «в глубину».

Все отделы (их сотни) читаются одним запросом, поэтому глубину, путь и число вложенных
можно считать без запросов на каждую строку.
"""
from collections import defaultdict

from rest_framework.exceptions import ValidationError

from django.db.models import RestrictedError

from .models import Section


MAX_SECTION_DEPTH = 10      # уровней вложенности (отдел верхнего уровня = 0)
PATH_SEPARATOR = ' / '


class SectionIndex:
    def __init__(self):
        self.rows = {
            row['id']: row
            for row in Section.objects.values('id', 'parent_id', 'name', 'department_id', 'code')
        }
        self._children = defaultdict(list)
        for row in self.rows.values():
            if row['parent_id'] is not None:
                self._children[row['parent_id']].append(row['id'])

    def ancestors(self, section_id):
        """Id предков от верхнего уровня до непосредственного родителя (без самого отдела)."""
        chain = []
        seen = {section_id}
        parent_id = self.rows.get(section_id, {}).get('parent_id')
        while parent_id is not None and parent_id not in seen and len(chain) <= MAX_SECTION_DEPTH * 2:
            chain.append(parent_id)
            seen.add(parent_id)
            parent_id = self.rows.get(parent_id, {}).get('parent_id')
        chain.reverse()
        return chain

    def depth(self, section_id):
        return len(self.ancestors(section_id))

    def path(self, section_id, include_self=True):
        ids = self.ancestors(section_id) + ([section_id] if include_self else [])
        return PATH_SEPARATOR.join(self.rows[i]['name'] for i in ids if i in self.rows)

    def children_count(self, section_id):
        return len(self._children.get(section_id, ()))

    def descendants(self, section_id):
        """Id всех вложенных отделов любой глубины (сам отдел не включается)."""
        result = []
        stack = list(self._children.get(section_id, ()))
        while stack:
            current = stack.pop()
            result.append(current)
            stack.extend(self._children.get(current, ()))
        return result

    def subtree(self, section_id):
        """Сам отдел и все вложенные — например, для фильтра сотрудников по родительскому отделу."""
        return [section_id, *self.descendants(section_id)]

    def height(self, section_id):
        """Сколько уровней вложенных ниже этого отдела (0 — листовой)."""
        levels = 0
        frontier = list(self._children.get(section_id, ()))
        while frontier:
            levels += 1
            frontier = [child for node in frontier for child in self._children.get(node, ())]
        return levels


def order_depth_first(sections):
    """Порядок «родитель, затем его вложенные»; порядок среди соседей берётся из входного списка.

    Если родителя нет во входных данных (результат поиска/фильтра), такой отдел считается верхним.
    """
    sections = list(sections)
    present = {section.pk for section in sections}
    by_parent = defaultdict(list)
    for section in sections:
        parent_id = section.parent_id if section.parent_id in present else None
        by_parent[parent_id].append(section)

    ordered = []

    def walk(parent_id):
        for section in by_parent.get(parent_id, ()):
            ordered.append(section)
            walk(section.pk)

    # Верхний уровень не смешиваем между цехами: порядок цехов уже задан входным списком.
    walk(None)
    return ordered


def annotate_path_labels(sections, index=None):
    """Проставляет каждому отделу .path_label («Юридик хизмати / Юридик бўлим») для выпадающих списков."""
    index = index or SectionIndex()
    sections = list(sections)
    for section in sections:
        section.path_label = index.path(section.pk)
    return sections


def delete_section_or_400(section):
    """Удаление с понятной ошибкой вместо 500, если у отдела есть вложенные."""
    try:
        section.delete()
    except RestrictedError:
        raise ValidationError({'detail': 'Бу бўлимнинг ички бўлимлари бор. Аввал уларни ўчиринг ёки бошқа бўлимга кўчиринг.'})
