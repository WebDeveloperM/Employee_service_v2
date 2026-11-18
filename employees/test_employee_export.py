import re

from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, EmployeeCategory, PositionCategory, ROLE_ADMIN, Section, UserProfile, WorkSchedule


EMPLOYEES_URL = '/api/template/employees/'
SERVER_MAX_PAGE_SIZE = 100


class EmployeeExcelExportTests(TestCase):
    """«Excel экспорти» жадвалдаги барча ходимларни (фақат кўриниб турган саҳифани эмас) файлга ёзади."""

    def setUp(self):
        self.admin = User.objects.create_user(username='export_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)

    def page(self, **params):
        response = self.api.get(EMPLOYEES_URL, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_server_caps_a_page_at_100_so_the_export_has_to_walk_every_page(self):
        for index in range(1, 106):   # create(): slug save() ичида ҳосил бўлади (bulk_create уни ўтказиб юборади)
            Employee.objects.create(first_name='Ism', last_name=f'Familiya{index:03d}', tabel_number=f'X{index:03d}')

        # Бир сўровда 500 сўралса ҳам 100 тадан ортиқ келмайди — шунинг учун экспорт барча саҳифаларни кетма-кет олади.
        first = self.page(page=1, page_size=500)
        second = self.page(page=2, page_size=500)

        self.assertEqual((len(first['data']), first['count']), (SERVER_MAX_PAGE_SIZE, 105))
        self.assertEqual((len(second['data']), second['count']), (5, 105))
        tabels = [item['tabel_number'] for item in first['data'] + second['data']]
        self.assertEqual(len(tabels), 105)
        self.assertEqual(len(set(tabels)), 105)      # саҳифалар орасида такрор ҳам, тушиб қолган ходим ҳам йўқ

    def test_filters_narrow_the_count_that_the_export_walks(self):
        for index in range(3):
            Employee.objects.create(first_name='A', last_name='Faol', tabel_number=f'A{index}')
        for index in range(2):
            Employee.objects.create(first_name='B', last_name='Nofaol', tabel_number=f'B{index}', is_active=False)

        self.assertEqual(self.page(page=1, page_size=100, status='active')['count'], 3)
        self.assertEqual(self.page(page=1, page_size=100, status='inactive')['count'], 2)
        self.assertEqual(self.page(page=1, page_size=100)['count'], 5)

    def html(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)
        return web.get('/employees/').content.decode()

    def test_export_loads_every_page_with_the_tables_filters_instead_of_using_the_visible_rows(self):
        html = self.html()
        export = html[html.index('async function exportEmployees()'):html.index('function writeEmployeesWorkbook(rows)')]

        self.assertIn('const EXPORT_PAGE_SIZE = 100;', html)                      # сервер чегараси (max_page_size = 100)
        self.assertIn('async function fetchAllEmployeesForExport()', html)
        self.assertIn('for (let page = 2; page <= pages; page++) {', html)       # 1-саҳифадан кейин қолганларини олади
        self.assertIn('await fetchAllEmployeesForExport()', export)
        # Эски хато: экспорт фақат экрандаги саҳифани (filteredEmployees / employeesCache) ёзарди.
        self.assertNotIn('filteredEmployees', export)
        self.assertNotIn('employeesCache', export)

    def test_table_and_export_share_one_set_of_filters(self):
        html = self.html()
        helper = html[html.index('function employeeFilterParams()'):html.index('let employeesRequestSeq = 0;')]

        for param in ('search', 'department_id', 'section_id', 'status', 'tabel_number', 'phone'):
            self.assertIn(f"params.set('{param}'", helper)
        # Жадвал ҳам, экспорт ҳам шу битта функциядан фойдаланади (ва фақат бир марта ўқийди).
        self.assertEqual(html.count('employeeFilterParams()'), 3)                 # таъриф + жадвал + экспорт
        self.assertEqual(html.count("params.set('tabel_number'"), 1)

    def test_export_has_every_column_of_the_employee_card(self):
        html = self.html()
        row = html[html.index('function employeeExportRow(employee, index)'):html.index('function writeEmployeesWorkbook(rows)')]

        columns = re.findall(r"^\s+'([^']+)': ", row, re.M)
        self.assertEqual(columns, [
            '№', 'Табель рақами', 'Исм', 'Фамилия', 'Отасининг исми', 'Жинси', 'Туғилган сана', 'Телефон 1', 'Телефон 2',
            'Цех', 'Цех раҳбари', 'Бўлим', 'Лавозим', 'Даража (разряд)', 'Лавозим тоифаси', 'Ходимлар тоифаси', 'Иш тартиби',
            'Ҳолати', 'Ишга қабул қилинган сана', 'Лавозим ўзгарган сана', 'Логин', 'Махсус кийим', 'Оёқ кийим', 'Куртка',
            'Футболка', 'Халат', 'Фотосурат (ҳавола)',
        ])
        # Парол ва техник майдонлар (хеш, metadata, Face ID) файлга тушмайди.
        for secret in ('password', 'metadata', 'requires_face_id'):
            self.assertNotIn(secret, row)

    def test_every_exported_column_is_returned_by_the_employees_api(self):
        """Экспорт жадвал сўрови (/api/template/employees/) жавобидан ўқийди: керакли майдонлар шу жавобда бўлиши шарт."""
        department =Department.objects.create(name='4-Цех', boss_full_name='Бобоев Б.Б.')
        section = Section.objects.create(department=department, name='Лаборатория')
        Employee.objects.create(
            first_name='Аҳмад', last_name='Раҳматов', surname='Хасан', tabel_number='E1', gender='M', date_of_birth='1985-03-07',
            phone_number_1='+998901234567', phone_number_2='+998712345678', department=department, section=section, position='Слесарь',
            grade='12*', position_category=PositionCategory.objects.get_or_create(name='Мутахассис')[0],
            employee_category=EmployeeCategory.objects.get_or_create(name='Техник ходимлар')[0],
            work_schedule=WorkSchedule.objects.get_or_create(name='Сменада')[0], date_of_employment='2015-01-20',
            date_of_change_position='2020-06-01', login='rahmatov', special_clothing_size='52', shoe_size='42',
            jacket_size='L', tshirt_size='XL', clothe_size='54', work_status='vacation',
        )

        item = self.page(page=1, page_size=100)['data'][0]

        self.assertEqual((item['first_name'], item['last_name'], item['surname'], item['tabel_number'], item['gender']), ('Аҳмад', 'Раҳматов', 'Хасан', 'E1', 'M'))
        self.assertEqual((item['date_of_birth'], item['date_of_employment'], item['date_of_change_position']), ('07/03/1985', '20/01/2015', '01/06/2020'))
        self.assertEqual((item['phone_number_1'], item['phone_number_2'], item['login']), ('+998901234567', '+998712345678', 'rahmatov'))
        self.assertEqual((item['department']['name'], item['department']['boss_full_name'], item['section']['name'], item['position']), ('4-Цех', 'Бобоев Б.Б.', 'Лаборатория', 'Слесарь'))
        self.assertEqual((item['grade'], item['position_category']['name'], item['employee_category']['name'], item['work_schedule']['name']), ('12*', 'Мутахассис', 'Техник ходимлар', 'Сменада'))
        self.assertEqual((item['is_active'], item['work_status']), (True, 'vacation'))
        self.assertEqual(
            (item['special_clothing_size'], item['shoe_size'], item['jacket_size'], item['tshirt_size'], item['clothe_size']),
            ('52', '42', 'L', 'XL', '54'),
        )
        self.assertIn('base_image_url', item)
        self.assertNotIn('password_hash', item)

    def test_export_button_shows_progress_and_blocks_double_clicks(self):
        html = self.html()

        self.assertIn('id="exportButton" onclick="exportEmployees()"', html)
        self.assertIn('id="exportButtonIcon"', html)
        self.assertIn('if (exportInProgress) {', html)
        self.assertIn("icon.className = 'fas fa-spinner fa-spin text-emerald-600';", html)
        self.assertIn('button.disabled = true;', html)
        self.assertIn('writeEmployeesWorkbook(rows);', html)
