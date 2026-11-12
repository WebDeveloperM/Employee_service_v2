from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, ROLE_USER, Section, StaffPosition, UserProfile


TREE_URL = '/api/template/staff/tree/'


class DashboardStatsLineTests(TestCase):
    """Строка «Штатлар сони / Ходимлар / Бўш иш ўринлари» берёт итоги из дерева штата."""

    def setUp(self):
        self.admin = User.objects.create_user(username='stats_line_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)

    def test_tree_carries_the_totals_the_dashboard_reads_with_inner_sections_included(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        parent = Section.objects.create(department=department, name='Юридик хизмати', code='1')
        inner = Section.objects.create(department=department, name='Юридик бўлим', code='1', parent=parent)
        StaffPosition.objects.create(department=department, name='Бошлиқ', staff_count=3)
        StaffPosition.objects.create(department=department, section=parent, name='Юрист', staff_count=2)
        StaffPosition.objects.create(department=department, section=inner, name='Ёрдамчи', staff_count=4)
        Employee.objects.create(first_name='A', last_name='B', tabel_number='T1', department=department, position='Бошлиқ')
        Employee.objects.create(first_name='C', last_name='D', tabel_number='T2', department=department, section=inner, position='Ёрдамчи')

        data = self.api.get(TREE_URL).json()['data']

        node = next(item for item in data if item['id'] == department.pk)
        parent_node = next(child for child in node['children'] if child['id'] == parent.pk)
        inner_node = next(child for child in parent_node['children'] if child['id'] == inner.pk)
        self.assertEqual((node['type'], node['staff_total'], node['vacant_total']), ('department', 9, 7))
        self.assertEqual((parent_node['type'], parent_node['staff_total'], parent_node['vacant_total']), ('section', 6, 5))
        self.assertEqual((inner_node['type'], inner_node['staff_total'], inner_node['vacant_total']), ('section', 4, 3))

    def test_dashboard_page_builds_the_hrm_line_from_the_staff_tree(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/employees/').content.decode()

        self.assertIn('Штатлар сони', html)
        self.assertIn('Бўш иш ўринлари', html)
        self.assertIn('/template/staff/tree/', html)
        self.assertNotIn('total_departments', html)

    def test_people_without_staff_rights_get_403_and_the_page_falls_back_to_the_employees_count(self):
        plain = User.objects.create_user(username='stats_line_plain', password='secret123')
        UserProfile.objects.create(user=plain, role=ROLE_USER)
        client = APIClient()
        client.force_authenticate(user=plain)

        self.assertEqual(client.get(TREE_URL).status_code, 403)
