from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Employee, ROLE_ADMIN, UserProfile


EMPLOYEES_URL = '/api/template/employees/'


class EmployeeListStatusFilterTests(TestCase):
    """«Ходимлар рўйхати» сукут бўйича фақат фаол ходимларни кўрсатади; «Ҳолати» танлови билан бошқаларини ҳам."""

    def setUp(self):
        self.admin = User.objects.create_user(username='status_filter_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        Employee.objects.create(first_name='Ali', last_name='Faol', tabel_number='ACT-1')
        Employee.objects.create(first_name='Vali', last_name='Faolemas', tabel_number='OFF-1', is_active=False)
        Employee.objects.create(first_name='Gani', last_name='Ochirilgan', tabel_number='DEL-1', is_deleted=True)

    def tabels(self, **params):
        response = self.api.get(EMPLOYEES_URL, {'page_size': 100, **params})
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload['count'], len(payload['data']))
        return sorted(item['tabel_number'] for item in payload['data'])

    def test_each_status_choice_returns_the_right_employees(self):
        self.assertEqual(self.tabels(status='active'), ['ACT-1'])
        self.assertEqual(self.tabels(status='inactive'), ['OFF-1'])
        # «Барчаси» (параметрсиз) — API аввалгидек ҳаммасини қайтаради (ташқи интеграциялар учун ўзгармаган).
        self.assertEqual(self.tabels(), ['ACT-1', 'OFF-1'])

    def test_list_page_defaults_to_active_and_sends_the_status_to_the_api(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/employees/').content.decode()

        self.assertIn('<select id="statusFilter"', html)
        self.assertIn('<option value="active" selected>Фаол</option>', html)
        self.assertIn('<option value="inactive">Фаол эмас</option>', html)
        self.assertIn('<option value="">Барчаси</option>', html)
        self.assertIn("if (status) params.set('status', status);", html)
