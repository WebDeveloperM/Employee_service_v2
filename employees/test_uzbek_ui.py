import re
from datetime import date
from io import BytesIO
from pathlib import Path

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section, UserProfile
from .template_views import EmployeeImportExcelTemplateView


TEMPLATES_DIR = Path(__file__).parent / 'templates'

# Заголовки, которые интерфейс показывает в инструкции импорта и пишет в экспорт Excel.
UZBEK_IMPORT_HEADERS = {
    'Табель рақами': 'tabel_number',
    'Исм': 'first_name',
    'Фамилия': 'last_name',
    'Отасининг исми': 'surname',
    'Цех': 'department_name',
    'Бўлим': 'section_name',
    'Лавозим': 'position',
    'Ишга қабул қилинган сана': 'date_of_employment',
}


class UzbekImportHeadersTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = User.objects.create_user(username='uz_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        self.client.force_authenticate(user=user)

    def test_every_header_shown_in_the_ui_and_export_is_recognised_by_the_importer(self):
        importer = EmployeeImportExcelTemplateView()

        for label, field in UZBEK_IMPORT_HEADERS.items():
            with self.subTest(header=label):
                self.assertEqual(importer.header_aliases.get(importer._normalize_header(label)), field)

    def test_uzbek_letters_survive_header_normalisation(self):
        importer = EmployeeImportExcelTemplateView()

        self.assertEqual(importer._normalize_header('Ишга қабул қилинган сана'), 'ишга_қабул_қилинган_сана')
        self.assertEqual(importer._normalize_header('  Туғилган  САНА '), 'туғилган_сана')

    def test_russian_headers_still_work(self):
        importer = EmployeeImportExcelTemplateView()

        self.assertEqual(importer.header_aliases[importer._normalize_header('Табельный номер')], 'tabel_number')
        self.assertEqual(importer.header_aliases[importer._normalize_header('Дата приема на работу')], 'date_of_employment')

    def test_employees_can_be_imported_with_uzbek_headers_and_values(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {'rows': [{
                'Табель рақами': 'UZ-1',
                'Исм': 'Olim',
                'Фамилия': 'Karimov',
                'Отасининг исми': 'Bahodirovich',
                'Цех': 'Тест цехи',
                'Бўлим': 'Тест бўлими',
                'Лавозим': 'Слесарь',
                'Ишга қабул қилинган сана': '2024-02-01',
                'Жинси': 'Эркак',
            }]},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.content)
        employee = Employee.objects.get(tabel_number='UZ-1')
        self.assertEqual((employee.first_name, employee.last_name, employee.surname), ('Olim', 'Karimov', 'Bahodirovich'))
        self.assertEqual((employee.department.name, employee.section.name, employee.position), ('Тест цехи', 'Тест бўлими', 'Слесарь'))
        self.assertEqual(employee.date_of_employment, date(2024, 2, 1))
        self.assertEqual(employee.gender, 'M')

    def test_uzbek_gender_and_status_words_are_understood(self):
        importer = EmployeeImportExcelTemplateView()

        self.assertEqual(importer._normalize_gender('Эркак'), 'M')
        self.assertEqual(importer._normalize_gender('Аёл'), 'F')
        self.assertIs(importer._normalize_bool('Фаол'), True)
        self.assertIs(importer._normalize_bool('Фаол эмас'), False)
        self.assertIs(importer._normalize_bool('Ҳа'), True)
        self.assertIs(importer._normalize_bool('Йўқ'), False)

    def test_phone_import_finds_uzbek_headers(self):
        Employee.objects.create(first_name='A', last_name='B', tabel_number='5001')
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['Табель рақами', 'Телефон 1', 'Туғилган сана'])
        sheet.append(['5001', '998901234567', '15.03.1990'])
        buffer = BytesIO()
        workbook.save(buffer)

        response = self.client.post(
            '/api/template/employees/import-phones/',
            {'excel_file': SimpleUploadedFile('phones.xlsx', buffer.getvalue())},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200, response.content)
        employee = Employee.objects.get(tabel_number='5001')
        self.assertIn('901234567', employee.phone_number_1)
        self.assertEqual(employee.date_of_birth, date(1990, 3, 15))


class UzbekPagesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='uz_page_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.department = Department.objects.create(name='1-Цех')
        self.section = Section.objects.create(department=self.department, name='Блок', code='1')
        self.employee = Employee.objects.create(
            first_name='Olim', last_name='Karimov', tabel_number='UZ-9', department=self.department, section=self.section,
        )
        self.client = Client(HTTP_HOST='192.168.2.72')
        self.client.force_login(self.admin)

    def assertPage(self, url, present, absent=()):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        html = response.content.decode()
        for text in present:
            self.assertIn(text, html, f'{url}: expected {text!r}')
        for text in absent:
            self.assertNotIn(text, html, f'{url}: unexpected {text!r}')
        self.assertIn('<html lang="uz-Cyrl">', html)

    def test_dashboard_menu_and_layout_are_in_uzbek(self):
        self.assertPage(
            '/',
            present=['Ходимлар рўйхати', 'Excel импорти', 'Ходим қўшиш', 'Бош саҳифа', 'Созламалар', 'Чиқиш',
                     'Штат бирлиги ва лавозим', 'Цехлар', 'Бўлимлар', 'Фойдаланувчилар рухсатлари'],
            absent=['Список сотрудников', 'Главная страница', 'Настройка', 'Выход', 'Доступ пользователей'],
        )

    def test_other_pages_are_in_uzbek(self):
        self.assertPage('/departments/', ['Цех қўшиш', 'Бўлим қўшиш', 'Бўлим ID си'], ['Добавить цех', 'Добавить отдел'])
        self.assertPage('/employees/create/', ['Шахсий маълумотлар', 'Табель рақами', 'Даража (разряд)'], ['Личные данные', 'Табельный номер'])
        self.assertPage(f'/employees/{self.employee.slug}/', ['Ходимни кўриш', 'Ўлчамлар'], ['Просмотр сотрудника'])
        self.assertPage(f'/employees/{self.employee.slug}/edit/', ['Ходимни таҳрирлаш'], ['Редактировать сотрудника'])
        self.assertPage(f'/employees/{self.employee.slug}/delete/', ['Ходимни ўчириш', 'Ўчиришни тасдиқлаш'], ['Подтверждение удаления'])
        self.assertPage('/settings/users/', ['Фойдаланувчи қўшиш', 'Фойдаланувчилар'], ['Добавить пользователя'])
        self.assertPage('/settings/access/', ['Фойдаланувчилар рухсатлари', 'Барча роллар'], ['Все роли'])
        self.assertPage('/profile/edit/', ['Профилни таҳрирлаш'], ['Редактирование профиля'])
        self.assertPage('/staff/', ['Штат бирлиги ва лавозим'])

    def test_login_page_is_in_uzbek(self):
        anonymous = Client(HTTP_HOST='192.168.2.72')
        html = anonymous.get('/login/').content.decode()

        self.assertIn('Тизимга кириш', html)
        self.assertIn('Логин', html)
        self.assertIn('Парол', html)
        self.assertNotIn('Вход в систему', html)
        self.assertNotIn('Пароль', html)

    def test_wrong_password_message_is_in_uzbek(self):
        anonymous = Client(HTTP_HOST='192.168.2.72')

        response = anonymous.post('/login/', {'username': 'uz_page_admin', 'password': 'wrong'})

        self.assertContains(response, 'Логин ёки парол нотўғри.')
        self.assertNotContains(response, 'Неверный логин или пароль.')

    def test_role_labels_are_uzbek_in_the_header_and_everywhere(self):
        hr = User.objects.create_user(username='uz_hr', password='secret123')
        profile = UserProfile.objects.create(user=hr, role=ROLE_HR)

        self.assertEqual(profile.get_role_display(), 'HR менежер')
        self.assertEqual(UserProfile(role=ROLE_ADMIN).get_role_display(), 'Администратор')
        self.assertEqual(UserProfile(role=ROLE_USER).get_role_display(), 'Фойдаланувчи')
        self.assertContains(self.client.get('/profile/edit/'), 'value="Администратор"')

    def test_api_messages_for_the_pages_are_in_uzbek(self):
        api = APIClient()
        api.force_authenticate(user=self.admin)

        created = api.post('/api/template/departments/', {'name': '2-Цех'}, format='json')
        listed = api.get('/api/template/sections/')
        no_access = APIClient()
        no_access.force_authenticate(user=User.objects.create_user(username='uz_plain', password='secret123'))
        denied = no_access.get('/api/template/users/')

        self.assertEqual(created.json()['message'], 'Цех муваффақиятли яратилди')
        self.assertEqual(listed.json()['message'], 'Бўлимлар муваффақиятли олинди')
        self.assertEqual(denied.status_code, 403)

    def test_serializer_errors_are_in_uzbek(self):
        api = APIClient()
        api.force_authenticate(user=self.admin)

        # В setUp в этом цехе уже есть отдел с ID «1».
        response = api.post('/api/template/sections/', {'name': 'Другой блок', 'code': '1', 'department_id': self.department.pk}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], ['Бу цехда шундай ID ли асосий бўлим аллақачон мавжуд.'])


class TemplatesHaveNoRussianTextTests(TestCase):
    """Страховка от возврата русского текста: в узбекской кириллице нет букв «ы» и «щ»."""

    SEGMENT = re.compile(r'[^<>"\'`{}$\n]*[А-Яа-яЁё][^<>"\'`{}$\n]*')
    RUSSIAN_ONLY = re.compile(r'[ыщЫЩ]')
    TRANSLIT_ROW = re.compile(r'(, )?[а-яё]:')   # таблица транслитерации логина — это логика, а не текст
    TRAILING_COMMENT = re.compile(r'(?<![:\w])//.*$|/\*.*?\*/')

    def test_no_russian_only_letters_in_user_visible_template_text(self):
        offenders = []
        for path in sorted(TEMPLATES_DIR.glob('*.html')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('<!--'):
                    continue  # комментарии кода — не интерфейс
                line = self.TRAILING_COMMENT.sub('', line)   # «код; // комментарий» и «/* комментарий */»
                for match in self.SEGMENT.finditer(line):
                    text = match.group(0).strip()
                    if text and not self.TRANSLIT_ROW.fullmatch(text) and self.RUSSIAN_ONLY.search(text):
                        offenders.append(f'{path.name}:{number}: {text[:80]}')

        self.assertEqual(offenders, [])
