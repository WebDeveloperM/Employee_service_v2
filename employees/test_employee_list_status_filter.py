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

    def test_tabel_number_filter_matches_part_of_the_number_and_combines_with_status(self):
        Employee.objects.create(first_name='Sami', last_name='Faol2', tabel_number='ACT-22')
        Employee.objects.create(first_name='Nodir', last_name='Faolemas2', tabel_number='OFF-22', is_active=False)

        self.assertEqual(self.tabels(status='active', tabel_number='ACT'), ['ACT-1', 'ACT-22'])
        self.assertEqual(self.tabels(status='active', tabel_number='22'), ['ACT-22'])
        self.assertEqual(self.tabels(status='inactive', tabel_number='22'), ['OFF-22'])
        self.assertEqual(self.tabels(tabel_number='22'), ['ACT-22', 'OFF-22'])
        self.assertEqual(self.tabels(status='active', tabel_number='NOPE'), [])

    def html(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)
        return web.get('/employees/').content.decode()

    def test_list_page_defaults_to_active_and_sends_the_status_to_the_api(self):
        html = self.html()

        self.assertIn('<select id="statusFilter"', html)
        self.assertIn('<option value="active" selected>Фаол</option>', html)
        self.assertIn('<option value="inactive">Фаол эмас</option>', html)
        self.assertIn('<option value="">Барчаси</option>', html)
        self.assertIn("if (status) params.set('status', status);", html)
        self.assertIn("if (tabelNumber) params.set('tabel_number', tabelNumber);", html)

    def test_filter_button_opens_an_accordion_above_the_table_holding_tabel_and_status(self):
        html = self.html()

        button = html.index('id="filterToggleButton"')
        panel = html.index('id="filterPanel"')
        tabel = html.index('id="tabelFilter"')
        status = html.index('id="statusFilter"')
        table = html.index('<table')
        header = html.index('id="pageSizeSelect"')
        # Тугма жадвал карточкасининг юқори қаторида, accordion унинг остида ва жадвалдан олдин; майдонлар accordion ичида.
        self.assertLess(header, button)
        self.assertLess(button, html.index('id="paginationTop"'))
        self.assertLess(html.index('id="paginationTop"'), panel)
        self.assertLess(panel, tabel)
        self.assertLess(tabel, status)
        self.assertLess(status, table)
        self.assertIn('Филтр', html[button:panel])
        self.assertIn('aria-controls="filterPanel"', html[button:panel])
        self.assertRegex(html[panel:tabel], r'^id="filterPanel" inert aria-hidden="true"')   # ёпиқ ва Tab билан танланмайди
        self.assertIn('Табель рақами', html[panel:status])
        self.assertIn('Ҳолати:', html[panel:table])

    def test_status_label_is_gone_from_the_table_header_row(self):
        html = self.html()

        header_row = html[html.index('id="pageSizeSelect"'):html.index('id="filterPanel"')]
        self.assertNotIn('Ҳолати', header_row)
        self.assertNotIn('statusFilter', header_row)

    def test_django_comments_do_not_leak_into_the_page_as_text(self):
        # Django {# … #} изоҳи фақат бир қаторда ишлайди; кўп қаторлиси саҳифага матн бўлиб чиқиб қолади.
        self.assertNotIn('{#', self.html())
