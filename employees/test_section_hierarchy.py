from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, Section, StaffPosition, UserProfile
from .sections_tree import MAX_SECTION_DEPTH, SectionIndex, order_depth_first


SECTIONS_URL = '/api/template/sections/'
TREE_URL = '/api/template/staff/tree/'


class HierarchyTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='tree_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.department = Department.objects.create(name='20-Цех')
        self.other_department = Department.objects.create(name='21-Цех')

    def make(self, name, parent=None, code='', department=None):
        return Section.objects.create(department=department or (parent.department if parent else self.department), name=name, code=code, parent=parent)

    def post(self, **payload):
        payload.setdefault('department_id', self.department.pk)
        return self.client.post(SECTIONS_URL, payload, format='json')

    def put(self, section, **payload):
        body = {'name': section.name, 'department_id': section.department_id, 'parent_id': section.parent_id, **payload}
        return self.client.put(f'{SECTIONS_URL}{section.pk}/', body, format='json')


class CreateChildSectionTests(HierarchyTestCase):
    def test_child_section_can_be_created_and_reports_path_depth_and_parent(self):
        parent = self.make('Юридик хизмати')

        response = self.post(name='Юридик бўлим', parent_id=parent.pk)

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()['data']
        self.assertEqual((data['parent_id'], data['path'], data['depth'], data['children_count']), (parent.pk, 'Юридик хизмати / Юридик бўлим', 1, 0))
        self.assertEqual(Section.objects.get(pk=data['id']).parent_id, parent.pk)

    def test_existing_style_section_without_parent_still_works(self):
        response = self.post(name='Оддий бўлим')

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()['data']
        self.assertEqual((data['parent_id'], data['path'], data['depth']), (None, 'Оддий бўлим', 0))

    def test_nesting_works_on_several_levels(self):
        first = self.make('1')
        second = self.make('2', parent=first)

        response = self.post(name='3', parent_id=second.pk)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['data']['path'], response.json()['data']['depth']), ('1 / 2 / 3', 2))

    def test_parent_must_belong_to_the_same_department(self):
        foreign_parent = self.make('Бошқа цех бўлими', department=self.other_department)

        response = self.post(name='Бола', parent_id=foreign_parent.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('parent_id', response.json())
        self.assertFalse(Section.objects.filter(name='Бола').exists())

    def test_unknown_parent_is_rejected(self):
        self.assertEqual(self.post(name='Бола', parent_id=999999).status_code, 400)

    def test_name_stays_unique_inside_the_department_even_for_children(self):
        parent = self.make('Ота')
        self.make('Такрор', parent=parent)

        self.assertEqual(self.post(name='Такрор', parent_id=parent.pk).status_code, 400)
        self.assertEqual(self.post(name='Такрор').status_code, 400)  # цех бўйича ном ноёб (Excel импорт шунга таянади)

    def test_code_is_unique_only_among_siblings(self):
        parent = self.make('Ота', code='1')
        self.assertEqual(self.post(name='Бола 1', code='1', parent_id=parent.pk).status_code, 201)  # ота билан бир хил ID — мумкин

        self.assertEqual(self.post(name='Бола 2', code='1', parent_id=parent.pk).status_code, 400)  # аканинг ID си банд
        self.assertEqual(self.post(name='Бола 3', code='2', parent_id=parent.pk).status_code, 201)

    def test_depth_is_limited(self):
        section = self.make('0')
        for level in range(1, MAX_SECTION_DEPTH):
            section = self.make(str(level), parent=section)

        too_deep = self.post(name='ортиқча', parent_id=section.pk)

        self.assertEqual(too_deep.status_code, 400, too_deep.content)
        self.assertIn('parent_id', too_deep.json())


class MoveSectionTests(HierarchyTestCase):
    def test_section_cannot_become_its_own_parent_or_move_under_its_own_descendant(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)
        grandchild = self.make('Невара', parent=child)

        self.assertEqual(self.put(root, parent_id=root.pk).status_code, 400)
        self.assertEqual(self.put(root, parent_id=child.pk).status_code, 400)
        self.assertEqual(self.put(root, parent_id=grandchild.pk).status_code, 400)
        self.assertEqual(self.put(child, parent_id=grandchild.pk).status_code, 400)
        root.refresh_from_db()
        self.assertIsNone(root.parent_id)

    def test_whole_subtree_can_be_moved_under_another_section(self):
        target = self.make('Нишон')
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)

        response = self.put(root, parent_id=target.pk)

        self.assertEqual(response.status_code, 200, response.content)
        root.refresh_from_db()
        self.assertEqual(root.parent_id, target.pk)
        self.assertEqual(SectionIndex().path(child.pk), 'Нишон / Илдиз / Бола')

    def test_moving_a_deep_subtree_respects_the_depth_limit(self):
        anchor = self.make('a0')
        for level in range(1, MAX_SECTION_DEPTH - 1):
            anchor = self.make(f'a{level}', parent=anchor)
        subtree_root = self.make('s0')
        self.make('s1', parent=subtree_root)

        response = self.put(subtree_root, parent_id=anchor.pk)  # поддерево из 2 поғона чуқурлик чегарасидан ошади

        self.assertEqual(response.status_code, 400, response.content)

    def test_section_can_be_detached_to_the_top_level(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)

        response = self.put(child, parent_id=None)

        self.assertEqual(response.status_code, 200, response.content)
        child.refresh_from_db()
        self.assertIsNone(child.parent_id)

    def test_keeping_everything_the_same_is_not_a_conflict(self):
        root = self.make('Илдиз', code='1')
        child = self.make('Бола', parent=root, code='2')

        self.assertEqual(self.put(child).status_code, 200)

    def test_update_without_parent_field_keeps_the_parent(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)

        response = self.client.put(f'{SECTIONS_URL}{child.pk}/', {'name': 'Бола 2', 'department_id': self.department.pk}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        child.refresh_from_db()
        self.assertEqual((child.name, child.parent_id), ('Бола 2', root.pk))

    def test_response_after_update_has_the_fresh_path(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)

        response = self.put(root, name='Янги ном')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.client.get(f'{SECTIONS_URL}{child.pk}/').json()['data']['path'], 'Янги ном / Бола')

    def test_department_of_a_section_with_children_cannot_change(self):
        root = self.make('Илдиз')
        self.make('Бола', parent=root)

        response = self.put(root, department_id=self.other_department.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('department_id', response.json())

    def test_leaf_can_move_to_another_department_only_without_a_parent_there(self):
        root = self.make('Илдиз')
        leaf = self.make('Бода', parent=root)

        stays_in_old_parent = self.put(leaf, department_id=self.other_department.pk)
        detached = self.put(leaf, department_id=self.other_department.pk, parent_id=None)

        self.assertEqual(stays_in_old_parent.status_code, 400)
        self.assertEqual(detached.status_code, 200, detached.content)


class SectionListTreeTests(HierarchyTestCase):
    def names(self, query=''):
        response = self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=100{query}')
        self.assertEqual(response.status_code, 200, response.content)
        return [(item['name'], item['depth']) for item in response.json()['data']]

    def build_tree(self):
        # «ID отдела» уникален в пределах цеха, а порядок соседей берётся из него (11 < 12, 111 одна).
        a = self.make('A', code='1')
        b = self.make('B', code='2')
        self.make('A2', parent=a, code='12')
        a1 = self.make('A1', parent=a, code='11')
        self.make('A1a', parent=a1, code='111')
        self.make('B1', parent=b, code='21')
        return a, b

    def test_list_is_a_depth_first_tree_ordered_by_id_inside_each_level(self):
        self.build_tree()

        self.assertEqual(self.names(), [('A', 0), ('A1', 1), ('A1a', 2), ('A2', 1), ('B', 0), ('B1', 1)])

    def test_pages_cut_the_same_tree(self):
        self.build_tree()
        seen = []
        for page in (1, 2, 3):
            response = self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=2&page={page}').json()
            seen += [item['name'] for item in response['data']]

        self.assertEqual(seen, ['A', 'A1', 'A1a', 'A2', 'B', 'B1'])

    def test_search_returns_a_flat_list_with_depth_and_path(self):
        self.build_tree()

        response = self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&search=A1a').json()['data']

        self.assertEqual([(item['name'], item['depth'], item['path']) for item in response], [('A1a', 2, 'A / A1 / A1a')])

    def test_children_count_is_reported(self):
        a, _ = self.build_tree()

        data = {item['name']: item['children_count'] for item in self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=100').json()['data']}

        self.assertEqual((data['A'], data['A1'], data['A1a'], data['B1']), (2, 1, 0, 0))

    def test_public_api_lists_the_hierarchy_fields_too(self):
        a, _ = self.build_tree()

        rows = self.client.get('/api/v1/sections/').json()

        rows = rows['results'] if isinstance(rows, dict) else rows
        by_name = {row['name']: row for row in rows}
        self.assertEqual((by_name['A1']['parent_id'], by_name['A1']['path'], by_name['A1']['depth']), (a.pk, 'A / A1', 1))

    def test_order_depth_first_treats_a_missing_parent_as_top_level(self):
        a, _ = self.build_tree()
        only_children = list(Section.objects.filter(name__in=['A1', 'A1a']).order_by('name'))

        self.assertEqual([s.name for s in order_depth_first(only_children)], ['A1', 'A1a'])


class DeleteSectionTests(HierarchyTestCase):
    def test_section_with_children_cannot_be_deleted_and_nothing_is_lost(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)

        response = self.client.delete(f'{SECTIONS_URL}{root.pk}/')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('ички бўлимлари бор', str(response.json()['detail']))
        self.assertTrue(Section.objects.filter(pk__in=[root.pk, child.pk]).count() == 2)

    def test_after_children_are_gone_the_parent_can_be_deleted(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)

        self.assertEqual(self.client.delete(f'{SECTIONS_URL}{child.pk}/').status_code, 204)
        self.assertEqual(self.client.delete(f'{SECTIONS_URL}{root.pk}/').status_code, 204)
        self.assertFalse(Section.objects.exists())

    def test_public_api_gives_the_same_clear_error(self):
        root = self.make('Илдиз')
        self.make('Бола', parent=root)

        response = self.client.delete(f'/api/v1/sections/{root.pk}/')

        self.assertEqual(response.status_code, 400, response.content)

    def test_deleting_a_department_removes_its_whole_section_tree(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)
        self.make('Невара', parent=child)

        response = self.client.delete(f'/api/template/departments/{self.department.pk}/')

        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(Section.objects.exists())

    def test_employees_of_a_deleted_section_are_kept_without_a_section(self):
        leaf = self.make('Барг')
        employee = Employee.objects.create(first_name='A', last_name='B', tabel_number='1', department=self.department, section=leaf)

        self.client.delete(f'{SECTIONS_URL}{leaf.pk}/')

        employee.refresh_from_db()
        self.assertIsNone(employee.section_id)


class EmployeesOfTheTreeTests(HierarchyTestCase):
    def test_filtering_by_a_parent_section_includes_employees_of_all_nested_sections(self):
        root = self.make('Илдиз')
        child = self.make('Бола', parent=root)
        grandchild = self.make('Невара', parent=child)
        sibling = self.make('Қўшни')
        for number, section in enumerate([root, child, grandchild, sibling], 1):
            Employee.objects.create(first_name='E', last_name=str(number), tabel_number=f'T{number}', department=self.department, section=section)

        def tabels(section):
            response = self.client.get('/api/template/employees/', {'section_id': section.pk, 'page_size': 100})
            return sorted(item['tabel_number'] for item in response.json()['data'])

        self.assertEqual(tabels(root), ['T1', 'T2', 'T3'])
        self.assertEqual(tabels(child), ['T2', 'T3'])
        self.assertEqual(tabels(grandchild), ['T3'])
        self.assertEqual(tabels(sibling), ['T4'])

    def test_employee_forms_show_the_full_path_of_nested_sections(self):
        root = self.make('Юридик хизмати')
        self.make('Юридик бўлим', parent=root)
        client = Client(HTTP_HOST='192.168.101.6')
        client.force_login(self.admin)

        html = client.get('/employees/create/').content.decode()

        self.assertIn('>Юридик хизмати / Юридик бўлим</option>', html)
        self.assertIn('>Юридик хизмати</option>', html)


class StaffTreeNestingTests(HierarchyTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make('Юридик хизмати')
        self.child = self.make('Юридик бўлим', parent=self.root)
        self.grandchild = self.make('Шартномалар гуруҳи', parent=self.child)
        StaffPosition.objects.create(department=self.department, name='Директор', staff_count=1)
        StaffPosition.objects.create(department=self.department, section=self.root, name='Хизмат бошлиғи', staff_count=1)
        StaffPosition.objects.create(department=self.department, section=self.child, name='Юрист', staff_count=2)
        StaffPosition.objects.create(department=self.department, section=self.grandchild, name='Ёрдамчи', staff_count=4)
        Employee.objects.create(first_name='E', last_name='1', tabel_number='1', department=self.department, section=self.grandchild, position='Ёрдамчи')

    def node(self, tree, name):
        stack = list(tree)
        while stack:
            current = stack.pop()
            if current['name'] == name:
                return current
            stack.extend(current.get('children', []))
        raise AssertionError(name)

    def test_tree_is_nested_and_totals_include_all_descendants(self):
        tree = self.client.get(TREE_URL).json()['data']

        department = self.node(tree, '20-Цех')
        root = self.node(tree, 'Юридик хизмати')
        child = self.node(tree, 'Юридик бўлим')
        grandchild = self.node(tree, 'Шартномалар гуруҳи')

        self.assertEqual([c['name'] for c in department['children']], ['Юридик хизмати'])
        self.assertEqual([c['name'] for c in root['children']], ['Юридик бўлим'])
        self.assertEqual([c['name'] for c in child['children']], ['Шартномалар гуруҳи'])
        self.assertEqual(grandchild['children'], [])
        self.assertEqual((grandchild['staff_total'], grandchild['vacant_total']), (4, 3))
        self.assertEqual((child['staff_total'], child['vacant_total']), (6, 5))
        self.assertEqual((root['staff_total'], root['vacant_total']), (7, 6))
        self.assertEqual((department['staff_total'], department['vacant_total']), (8, 7))
        self.assertEqual(child['parent_id'], root.get('id'))

    def test_positions_of_a_section_are_its_own_but_the_summary_includes_nested_ones(self):
        response = self.client.get(f'/api/template/staff/positions/?section_id={self.child.pk}').json()

        self.assertEqual([item['name'] for item in response['data']], ['Юрист'])
        self.assertEqual(response['summary'], {'own_staff': 2, 'own_vacant': 2, 'total_staff': 6, 'total_vacant': 5})
        self.assertEqual(response['unit']['breadcrumb'], '20-Цех / Юридик хизмати')

    def test_breadcrumb_of_a_top_level_section_is_just_the_department(self):
        response = self.client.get(f'/api/template/staff/positions/?section_id={self.root.pk}').json()

        self.assertEqual(response['unit']['breadcrumb'], '20-Цех')

    def test_department_summary_still_counts_every_section_of_every_level(self):
        response = self.client.get(f'/api/template/staff/positions/?department_id={self.department.pk}').json()

        self.assertEqual(response['summary'], {'own_staff': 1, 'own_vacant': 1, 'total_staff': 8, 'total_vacant': 7})
