from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, Section, StaffPosition, UserProfile


EMPLOYEES_URL = '/api/template/employees/'


class EmployeeTableFollowsStaffTreeOrderTests(TestCase):
    """Таблица сотрудников идёт в том же порядке, что и «Штат бирлиги ва лавозим»."""

    def setUp(self):
        admin = User.objects.create_user(username='staff_order_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def employee(self, tabel, last_name='Karimov', department=None, section=None, position=''):
        return Employee.objects.create(
            first_name='Ali', last_name=last_name, tabel_number=tabel,
            department=department or (section.department if section else None), section=section, position=position,
        )

    def tabels(self, **params):
        response = self.client.get(EMPLOYEES_URL, {'page_size': 100, **params})
        self.assertEqual(response.status_code, 200, response.content)
        return [item['tabel_number'] for item in response.json()['data']]

    def test_departments_follow_the_tree_with_leadership_and_20th_on_top(self):
        first = Department.objects.create(name='1-Цех', sort_order=1)
        second = Department.objects.create(name='2-Цех', sort_order=2)
        twentieth = Department.objects.create(name='20-Цех Завод бошқармаси', sort_order=20)
        leadership = Department.objects.create(name='Раҳбарият', sort_order=42)
        for tabel, department in (('D1', first), ('D2', second), ('D20', twentieth), ('DL', leadership), ('NO', None)):
            self.employee(tabel, department=department)

        self.assertEqual(self.tabels(), ['DL', 'D20', 'D1', 'D2', 'NO'])

    def test_department_staff_come_first_then_sections_by_id_with_inner_sections_after_their_parent(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        # Названия нарочно идут не в порядке ID: «Я блок» (ID 1) раньше «А блок» (ID 2).
        ya = Section.objects.create(department=department, name='Я блок', code='1')
        inner = Section.objects.create(department=department, name='Ички', code='1', parent=ya)
        a = Section.objects.create(department=department, name='А блок', code='2')
        b = Section.objects.create(department=department, name='Б блок', code='10')
        self.employee('S-B', section=b)
        self.employee('S-A', section=a)
        self.employee('S-INNER', section=inner)
        self.employee('S-YA', section=ya)
        self.employee('S-DEPT', department=department)

        self.assertEqual(self.tabels(), ['S-DEPT', 'S-YA', 'S-INNER', 'S-A', 'S-B'])

    def test_positions_follow_the_staff_list_then_the_rest_by_name(self):
        leadership = Department.objects.create(name='Раҳбарият', sort_order=1)
        StaffPosition.objects.create(department=leadership, name='Завод директори')
        StaffPosition.objects.create(department=leadership, name='Бош муҳандис')
        self.employee('P-NONE', 'Aaa', department=leadership, position='')
        self.employee('P-OTHER', 'Bbb', department=leadership, position='Бошқа лавозим')
        self.employee('P-ENG', 'Ccc', department=leadership, position='  бош   МУҲАНДИС ')   # регистр и пробелы не важны
        self.employee('P-DIR', 'Ddd', department=leadership, position='Завод директори')

        self.assertEqual(self.tabels(), ['P-DIR', 'P-ENG', 'P-NONE', 'P-OTHER'])

    def test_positions_are_matched_inside_their_own_unit_only(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        section = Section.objects.create(department=department, name='Блок', code='1')
        StaffPosition.objects.create(department=department, name='Мастер')
        StaffPosition.objects.create(section=section, department=department, name='Ҳисобчи')
        StaffPosition.objects.create(section=section, department=department, name='Мастер')
        self.employee('U-SEC-MASTER', 'Aaa', section=section, position='Мастер')
        self.employee('U-SEC-ACC', 'Bbb', section=section, position='Ҳисобчи')
        self.employee('U-DEPT-ACC', 'Ccc', department=department, position='Ҳисобчи')   # у цеха такой штатной должности нет
        self.employee('U-DEPT-MASTER', 'Ddd', department=department, position='Мастер')

        # цех: сначала «Мастер» (есть в штате цеха), затем остальные; отдел: по порядку штатных должностей
        self.assertEqual(self.tabels(), ['U-DEPT-MASTER', 'U-DEPT-ACC', 'U-SEC-ACC', 'U-SEC-MASTER'])

    def test_without_staff_positions_people_stay_in_name_order_inside_the_unit(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        for tabel, last_name in (('N3', 'Ccc'), ('N1', 'Aaa'), ('N2', 'Bbb')):
            self.employee(tabel, last_name, department=department, position='Слесарь')

        self.assertEqual(self.tabels(), ['N1', 'N2', 'N3'])

    def test_pages_are_cut_from_the_ordered_list_and_the_count_is_unchanged(self):
        first = Department.objects.create(name='1-Цех', sort_order=1)
        leadership = Department.objects.create(name='Раҳбарият', sort_order=42)
        for number in range(3):
            self.employee(f'F{number}', f'A{number}', department=first)
            self.employee(f'L{number}', f'A{number}', department=leadership)

        first_page = self.client.get(f'{EMPLOYEES_URL}?page_size=4&page=1').json()
        second_page = self.client.get(f'{EMPLOYEES_URL}?page_size=4&page=2').json()

        self.assertEqual([item['tabel_number'] for item in first_page['data']], ['L0', 'L1', 'L2', 'F0'])
        self.assertEqual([item['tabel_number'] for item in second_page['data']], ['F1', 'F2'])
        self.assertEqual((first_page['count'], second_page['count']), (6, 6))
        self.assertTrue(first_page['next'])
        self.assertIsNone(second_page['next'])

    def test_filters_keep_the_tree_order(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        other = Department.objects.create(name='2-Цех', sort_order=2)
        ya = Section.objects.create(department=department, name='Я блок', code='1')
        inner = Section.objects.create(department=department, name='Ички', code='1', parent=ya)
        a = Section.objects.create(department=department, name='А блок', code='2')
        self.employee('X-A', 'Aaa', section=a, position='Мастер')
        self.employee('X-INNER', 'Bbb', section=inner, position='Мастер')
        self.employee('X-YA', 'Ccc', section=ya, position='Мастер')
        self.employee('X-OTHER', 'Ddd', department=other, position='Мастер')

        self.assertEqual(self.tabels(department_id=department.pk), ['X-YA', 'X-INNER', 'X-A'])
        self.assertEqual(self.tabels(section_id=ya.pk), ['X-YA', 'X-INNER'])
        self.assertEqual(self.tabels(search='Мастер'), ['X-YA', 'X-INNER', 'X-A', 'X-OTHER'])

    def test_payload_carries_what_the_group_banners_need(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        parent = Section.objects.create(department=department, name='Юридик хизмати', code='1')
        inner = Section.objects.create(department=department, name='Юридик бўлим', code='1', parent=parent)
        self.employee('B-INNER', section=inner)

        item = self.client.get(EMPLOYEES_URL, {'page_size': 10}).json()['data'][0]

        self.assertEqual((item['department']['id'], item['department']['name']), (department.pk, '1-Цех'))
        self.assertEqual((item['section']['id'], item['section']['name'], item['section']['depth']), (inner.pk, 'Юридик бўлим', 1))
        self.assertEqual(item['section']['path'], 'Юридик хизмати / Юридик бўлим')

    def test_dashboard_page_draws_the_group_banners(self):
        from django.test import Client

        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(User.objects.get(username='staff_order_admin'))

        html = web.get('/employees/').content.decode()

        self.assertIn('staff-group-department', html)
        self.assertIn('staff-group-section', html)

    def test_dashboard_does_not_draw_a_banner_for_the_plant_administration_department(self):
        from django.test import Client

        from .staff import PINNED_DEPARTMENTS

        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(User.objects.get(username='staff_order_admin'))

        html = web.get('/employees/').content.decode()

        # «20-Цех Завод бошқармаси» цехини таблицада штат дарахтидаги қоида (staff.py) билан бир хил аниқлаймиз
        self.assertIn(r'const NO_BANNER_DEPARTMENT = /^\s*20(?!\d)/;', html)
        self.assertEqual(PINNED_DEPARTMENTS[1].pattern, r'^\s*20(?!\d)')
        # бўлим ва ички бўлим сарлавҳалари цех сарлавҳаси каби ўртада, кўк фонда
        self.assertIn("const BANNER_CELL = 'bg-[#0a9de3] px-4 text-center font-medium text-white';", html)

    def test_deleted_employees_are_still_hidden(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        self.employee('KEEP', department=department)
        Employee.objects.filter(pk=self.employee('GONE', department=department).pk).update(is_deleted=True)

        self.assertEqual(self.tabels(), ['KEEP'])
