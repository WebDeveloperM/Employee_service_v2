"""Excel импорти: юклаб олинадиган шаблон, файлни олдиндан текшириш (dry_run), тушунарли хатолар ва тезлик."""
import json
import zipfile
from datetime import date, timedelta
from io import BytesIO
from unittest import mock

import openpyxl
from django.contrib.auth.hashers import MD5PasswordHasher, check_password
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from .import_template import COLUMN_LABELS, DATA_SHEET_TITLE, IMPORT_COLUMNS, LISTS_SHEET_TITLE, UNITS_SHEET_TITLE
from .models import Department, Employee, EmployeeCategory, PositionCategory, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section, UserProfile, WorkSchedule
from .template_views import EmployeeImportExcelTemplateView


IMPORT_URL = '/api/template/employees/import-excel/'
TEMPLATE_URL = '/api/template/employees/import-template/'
FAST_HASHER = ['django.contrib.auth.hashers.MD5PasswordHasher']   # PBKDF2 (~0,3 с) тестларни секинлаштирмасин


def jpeg_bytes(color='white'):
    buffer = BytesIO()
    Image.new('RGB', (32, 32), color=color).save(buffer, format='JPEG')
    return buffer.getvalue()


def make_user(username, role):
    user = User.objects.create_user(username=username, password='secret123')
    if role is not None:
        UserProfile.objects.create(user=user, role=role)
    return user


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def person(tabel, last_name='Каримов', first_name='Алишер', row=None, **columns):
    data = {'Табель рақами': tabel, 'Фамилия': last_name, 'Исм': first_name, **columns}
    if row is not None:
        data['__row'] = row
    return data


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class ImportTemplateDownloadTests(TestCase):
    def setUp(self):
        self.admin = make_user('tpl_admin', ROLE_ADMIN)
        self.first = Department.objects.create(name='1-Цех', sort_order=1)
        parent = Section.objects.create(department=self.first, name='Юридик хизмати', code='1')
        Section.objects.create(department=self.first, name='Юридик бўлим', code='1', parent=parent)
        Department.objects.create(name='=HYPERLINK("x")', sort_order=90)

    def workbook(self, user=None):
        response = api_as(user or self.admin).get(TEMPLATE_URL)
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attachment', response['Content-Disposition'])
        return openpyxl.load_workbook(BytesIO(response.content))

    def test_admin_and_hr_download_it_and_others_cannot(self):
        self.workbook()
        self.workbook(make_user('tpl_hr', ROLE_HR))
        self.assertEqual(api_as(make_user('tpl_plain', ROLE_USER)).get(TEMPLATE_URL).status_code, 403)
        self.assertIn(APIClient().get(TEMPLATE_URL).status_code, (401, 403))

    def test_first_sheet_holds_exactly_the_import_columns(self):
        book = self.workbook()

        self.assertEqual(book.sheetnames[0], DATA_SHEET_TITLE)   # браузер «Ходимлар» варағини (бўлмаса — биринчисини) ўқийди
        sheet = book[DATA_SHEET_TITLE]
        self.assertEqual([cell.value for cell in sheet[1]], [column.header for column in IMPORT_COLUMNS])
        self.assertEqual(sheet.max_row, 1)                        # мисол қатори йўқ: у ходим бўлиб импорт бўлиб кетарди
        self.assertEqual(sheet.freeze_panes, 'D2')
        self.assertEqual(book[LISTS_SHEET_TITLE].sheet_state, 'hidden')

    def test_columns_are_typed_so_excel_does_not_spoil_the_values(self):
        sheet = self.workbook()[DATA_SHEET_TITLE]
        letters = {column.field: openpyxl.utils.get_column_letter(index) for index, column in enumerate(IMPORT_COLUMNS, 1)}

        for field in ('tabel_number', 'phone_number_1', 'phone_number_2'):
            self.assertEqual(sheet.column_dimensions[letters[field]].number_format, '@', field)   # бошидаги 0, узун рақам
        for field in ('date_of_birth', 'date_of_employment', 'date_of_change_position'):
            self.assertEqual(sheet.column_dimensions[letters[field]].number_format, 'DD.MM.YYYY', field)

    def test_lists_come_from_the_database(self):
        book = self.workbook()
        sheet = book[DATA_SHEET_TITLE]
        letters = {column.field: openpyxl.utils.get_column_letter(index) for index, column in enumerate(IMPORT_COLUMNS, 1)}
        validations = {str(item.sqref).split(':')[0][:-1]: item for item in sheet.data_validations.dataValidation}

        self.assertEqual(validations[letters['gender']].formula1, '"Эркак,Аёл"')
        self.assertEqual(validations[letters['department_name']].type, 'list')
        self.assertEqual(validations[letters['department_name']].errorStyle, 'warning')   # янги цех ёзиш мумкин
        self.assertEqual(validations[letters['tabel_number']].type, 'custom')            # файлдаги такрор табель
        lists = book[LISTS_SHEET_TITLE]
        list_values = {cell.value for column in lists.iter_cols() for cell in column}
        self.assertTrue({'1-Цех', 'Раҳбар', 'Техник ходимлар', 'Сменада'} <= list_values)
        formula_like = next(cell for column in lists.iter_cols() for cell in column if cell.value == '=HYPERLINK("x")')
        self.assertEqual(formula_like.data_type, 's')   # цех номи формула бўлиб ишламайди

    def test_units_sheet_lists_sections_with_nested_ones_after_their_parent(self):
        rows = [tuple(cell.value for cell in row) for row in self.workbook()[UNITS_SHEET_TITLE].iter_rows(min_row=2)]

        self.assertIn(('1-Цех', 'Юридик хизмати', None), rows)
        self.assertIn(('1-Цех', 'Юридик бўлим', 'Юридик хизмати'), rows)
        self.assertLess(rows.index(('1-Цех', 'Юридик хизмати', None)), rows.index(('1-Цех', 'Юридик бўлим', 'Юридик хизмати')))

    def test_every_template_header_is_recognised_by_the_importer(self):
        importer = EmployeeImportExcelTemplateView()

        for column in IMPORT_COLUMNS:
            with self.subTest(header=column.header):
                self.assertEqual(importer.header_aliases.get(importer._normalize_header(column.header)), column.field)


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class ImportCheckTests(TestCase):
    """dry_run: бутун файл текширилади, ҳеч нарса сақланмайди."""

    def setUp(self):
        self.api = api_as(make_user('check_admin', ROLE_ADMIN))
        self.department = Department.objects.create(name='1-Цех', sort_order=1)
        self.section = Section.objects.create(department=self.department, name='Таъмирлаш бўлими', code='1')

    def check(self, rows, **extra):
        return self.api.post(IMPORT_URL, {'dry_run': True, 'rows': rows, **extra}, format='json')

    def test_a_valid_file_is_reported_and_nothing_is_saved(self):
        response = self.check([
            person('C-1', row=2, **{'Цех': '1-Цех', 'Бўлим': 'Таъмирлаш бўлими'}),
            person('C-2', 'Валиев', 'Ботир', row=3, **{'Цех': 'Янги цех', 'Бўлим': 'Янги бўлим'}),
        ], image_names=['C-1.jpg', 'C-9.jpg', 'notes.txt'])

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body['dry_run'])
        self.assertEqual(body['total_rows'], 2)
        self.assertEqual(body['images_matched'], 1)
        self.assertEqual(body['new_departments'], ['Янги цех'])
        self.assertEqual(body['new_sections'], [{'department': 'Янги цех', 'section': 'Янги бўлим'}])
        self.assertTrue(any('C-9' in warning for warning in body['warnings']))       # табели йўқ фото
        self.assertTrue(any('notes.txt' in warning for warning in body['warnings']))  # тасвир эмас — ўтказиб юборилади
        self.assertFalse(Employee.objects.exists())
        self.assertFalse(Department.objects.filter(name='Янги цех').exists())
        self.assertFalse(User.objects.filter(username__startswith='алишер').exists())

    def test_department_and_section_names_match_regardless_of_case_and_spaces(self):
        response = self.check([person('C-3', **{'Цех': '  1-ЦЕХ ', 'Бўлим': 'таъмирлаш   бўлими'})])

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['new_departments'], response.json()['new_sections']), ([], []))

    def test_errors_name_the_excel_row_and_the_column(self):
        future = (date.today() + timedelta(days=40)).strftime('%d.%m.%Y')
        response = self.check([
            person('E-1', row=5, **{'Туғилган сана': '31.02.1990'}),
            person('E-2', row=6, **{'Туғилган сана': '1990'}),
            person('E-3', row=7, **{'Туғилган сана': future}),
            person('E-4', row=8, **{'Жинси': 'X'}),
            person('E-5', row=9, **{'Телефон 1': '9.98901E+11'}),
            person('E-6', row=10, **{'Телефон 2': '+998 90 123 45 67, +998 91 765 43 21'}),
            person('E-7', row=11, **{'Лавозим тоифаси': 'Директор'}),
            person('E-8', row=12, **{'Бўлим': 'Таъмирлаш бўлими'}),
            person('', row=13),
        ])

        self.assertEqual(response.status_code, 400, response.content)
        errors = {item['row']: item for item in response.json()['errors']}
        self.assertEqual(sorted(errors), [5, 6, 7, 8, 9, 10, 11, 12, 13])
        expectations = {
            5: ('Туғилган сана', '31.02.1990'),
            6: ('Туғилган сана', 'КК.ОО.ЙЙЙЙ'),        # фақат йил — 1905 йилги сана бўлиб кетмайди
            7: ('Туғилган сана', 'келажакда'),
            8: ('Жинси', 'Эркак'),
            9: ('Телефон 1', 'Матн'),
            10: ('Телефон 2', '20 белги'),
            11: ('Лавозим тоифаси', 'Мавжудлари: Раҳбар, Мутахассис, Ишчи, Хизматчи'),
            12: ('Бўлим', '«Цех» бўш'),
        }
        for row, (column, text) in expectations.items():
            with self.subTest(row=row):
                self.assertEqual(errors[row]['column'], column)
                self.assertIn(text, errors[row]['message'])
        self.assertEqual(errors[13]['fields'], ['tabel_number'])
        self.assertIn('Табель рақами', errors[13]['message'])

    def test_existing_and_deleted_tabel_numbers_name_their_owner(self):
        Employee.objects.create(tabel_number='OLD-1', first_name='Бобур', last_name='Эгаси', department=self.department)
        Employee.objects.create(tabel_number='OLD-2', first_name='Ўчган', last_name='Ходим', is_deleted=True)

        errors = self.check([person('OLD-1'), person('OLD-2')]).json()['errors']

        self.assertIn('Эгаси Бобур', errors[0]['message'])
        self.assertIn('1-Цех', errors[0]['message'])
        self.assertIn('ўчирилган', errors[1]['message'])
        self.assertEqual(errors[0]['errors']['tabel_number'], ['Бундай табель рақамли ходим аллақачон қўшилган.'])

    def test_duplicate_tabel_numbers_list_all_their_rows(self):
        errors = self.check([person('D-1', row=4), person('D-1', row=9)]).json()['errors']

        self.assertEqual(errors[0]['rows'], [4, 9])
        self.assertIn('4, 9', errors[0]['message'])

    def test_a_missing_required_column_is_one_clear_error(self):
        response = self.check([{'Фамилия': 'Каримов', 'Исм': 'Алишер'}, {'Фамилия': 'Валиев', 'Исм': 'Ботир'}])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_count'], 1)
        self.assertIn('«Табель рақами»', response.json()['message'])

    def test_duplicate_photo_names_stop_the_check(self):
        response = self.check([person('P-1')], image_names=['P-1.jpg', 'P-1.png'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('P-1', response.json()['errors'][0]['message'])

    def test_unknown_columns_are_reported_as_ignored(self):
        response = self.check([person('U-1', **{'Ҳолати': 'Фаол', '№': 1, 'Логин': 'x'})])

        warnings = ' '.join(response.json()['warnings'])
        self.assertIn('Ҳолати', warnings)
        self.assertNotIn('№', warnings)
        self.assertNotIn('Логин', warnings)   # танилади, лекин логин доим автоматик яратилади


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class ImportSaveTests(TestCase):
    def setUp(self):
        self.api = api_as(make_user('save_admin', ROLE_ADMIN))
        self.department = Department.objects.create(name='1-Цех', sort_order=1)
        self.section = Section.objects.create(department=self.department, name='Таъмирлаш бўлими', code='1')

    def post(self, rows, images=()):
        return self.api.post(IMPORT_URL, {'rows': json.dumps(rows), 'images': list(images)}, format='multipart')

    def test_all_template_columns_are_saved(self):
        response = self.api.post(IMPORT_URL, {'rows': [person(
            '005400', 'Каримов', 'Алишер',
            **{
                'Отасининг исми': 'Баҳодирович', 'Жинси': 'Эркак', 'Туғилган сана': '1990-01-02',
                'Телефон 1': '998901234567', 'Телефон 2': '+998712345678', 'Цех': '1-цех', 'Бўлим': 'ТАЪМИРЛАШ бўлими',
                'Лавозим': 'Слесарь', 'Даража (разряд)': '12*', 'Лавозим тоифаси': 'ишчи',
                'Ходимлар тоифаси': 'Техник  ходимлар', 'Иш тартиби': 'сменада', 'Ишга қабул қилинган сана': '01.09.2015',
                'Лавозим ўзгарган сана': '01.02.2021', 'Махсус кийим': '52', 'Оёқ кийим': '42.5', 'Куртка': 'L',
                'Футболка': 'XL', 'Халат': '54',
            },
        )]}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        employee = Employee.objects.get(tabel_number='005400')
        self.assertEqual((employee.last_name, employee.first_name, employee.surname, employee.gender), ('Каримов', 'Алишер', 'Баҳодирович', 'M'))
        self.assertEqual((employee.department, employee.section), (self.department, self.section))   # янги цех яратилмади
        self.assertEqual(Department.objects.count(), 1)
        self.assertEqual((employee.date_of_birth, employee.date_of_employment, employee.date_of_change_position),
                         (date(1990, 1, 2), date(2015, 9, 1), date(2021, 2, 1)))
        self.assertEqual((employee.phone_number_1, employee.phone_number_2), ('998901234567', '+998712345678'))
        self.assertEqual((employee.position, employee.grade), ('Слесарь', '12*'))
        self.assertEqual(employee.position_category, PositionCategory.objects.get(name='Ишчи'))
        self.assertEqual(employee.employee_category, EmployeeCategory.objects.get(name='Техник ходимлар'))
        self.assertEqual(employee.work_schedule, WorkSchedule.objects.get(name='Сменада'))
        self.assertEqual(
            (employee.special_clothing_size, employee.shoe_size, employee.jacket_size, employee.tshirt_size, employee.clothe_size),
            ('52', '42.5', 'L', 'XL', '54'),
        )
        self.assertEqual(response.json()['created_departments'], [])

    def test_the_password_is_hashed_once_and_fits_both_the_employee_and_his_account(self):
        encode = MD5PasswordHasher.encode
        with mock.patch.object(MD5PasswordHasher, 'encode', autospec=True, side_effect=encode) as hashing:
            response = self.post([person('H-1'), person('H-2', 'Валиев', 'Ботир'), person('H-3', 'Шодиев', 'Олим')])

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(hashing.call_count, 3)   # илгари 6 та: ходим ва фойдаланувчиси учун алоҳида
        for item in response.json()['employees']:
            employee = Employee.objects.select_related('user').get(tabel_number=item['tabel_number'])
            self.assertEqual(employee.password_hash, employee.user.password)
            self.assertTrue(employee.user.check_password(item['generated_password']))
            self.assertTrue(check_password(item['generated_password'], employee.password_hash))
            self.assertEqual(employee.user.profile.role, ROLE_USER)

    def test_batches_share_new_departments_even_if_spelled_differently(self):
        first = self.post([person('B-1', **{'Цех': 'Янги цех', 'Бўлим': 'Янги бўлим'})])
        second = self.post([person('B-2', 'Валиев', 'Ботир', **{'Цех': 'янги ЦЕХ', 'Бўлим': ' янги  бўлим'})])

        self.assertEqual((first.status_code, second.status_code), (201, 201), second.content)
        self.assertEqual(first.json()['created_departments'], ['Янги цех'])
        self.assertEqual(first.json()['created_sections'], [{'department': 'Янги цех', 'section': 'Янги бўлим'}])
        self.assertEqual(second.json()['created_departments'], [])
        self.assertEqual(Department.objects.count(), 2)   # 1-Цех ва Янги цех
        self.assertEqual(Section.objects.filter(name='Янги бўлим').count(), 1)
        self.assertEqual(Employee.objects.get(tabel_number='B-2').department.name, 'Янги цех')

    def test_one_file_with_two_spellings_creates_one_department(self):
        response = self.post([person('S-1', **{'Цех': 'Сув цехи'}), person('S-2', 'Валиев', 'Ботир', **{'Цех': 'СУВ  цехи'})])

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Department.objects.filter(name='Сув цехи').count(), 1)
        self.assertEqual(Department.objects.count(), 2)

    def test_a_broken_photo_does_not_stop_the_import(self):
        response = self.post(
            [person('F-1'), person('F-2', 'Валиев', 'Ботир')],
            images=[SimpleUploadedFile('F-1.jpg', b'not an image', content_type='image/jpeg'),
                    SimpleUploadedFile('F-2.jpg', jpeg_bytes(), content_type='image/jpeg')],
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(Employee.objects.get(tabel_number='F-1').base_image)
        self.assertTrue(Employee.objects.get(tabel_number='F-2').base_image)
        self.assertEqual(response.json()['imported_with_images'], 1)
        self.assertTrue(any('F-1' in warning for warning in response.json()['warnings']))

    def test_service_files_inside_a_zip_are_skipped(self):
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w') as handle:
            handle.writestr('photos/Z-1.jpg', jpeg_bytes())
            handle.writestr('photos/Thumbs.db', b'x')
            handle.writestr('__MACOSX/photos/._Z-1.jpg', b'x')
            handle.writestr('photos/readme.txt', b'x')

        response = self.post([person('Z-1')], images=[SimpleUploadedFile('photos.zip', archive.getvalue(), content_type='application/zip')])

        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(Employee.objects.get(tabel_number='Z-1').base_image)
        self.assertEqual(response.json()['warnings'], ['Тасвир бўлмаган файллар ўтказиб юборилди: readme.txt.'])

    def test_a_failing_row_rolls_back_the_whole_request(self):
        response = self.post([person('R-1', row=2), person('R-2', 'Валиев', 'Ботир', row=3, **{'Даража (разряд)': '123'})])

        self.assertEqual(response.status_code, 400)
        error = response.json()['errors'][0]
        self.assertEqual((error['row'], error['column']), (3, 'Даража (разряд)'))
        self.assertFalse(Employee.objects.filter(tabel_number__in=['R-1', 'R-2']).exists())

    def test_numbers_sent_as_json_numbers_keep_their_digits(self):
        response = self.api.post(IMPORT_URL, {'rows': [{'Табель рақами': 5400.0, 'Фамилия': 'Каримов', 'Исм': 'Алишер',
                                                         'Телефон 1': 998901234567}]}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Employee.objects.get(tabel_number='5400').phone_number_1, '998901234567')


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class ImportModalPageTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='192.168.101.6')
        self.client.force_login(make_user('modal_admin', ROLE_ADMIN))

    def test_the_modal_offers_the_template_and_lists_the_columns(self):
        html = self.client.get('/employees/').content.decode()

        self.assertIn(f'href="{TEMPLATE_URL}"', html)
        self.assertIn('Шаблонни юклаб олиш', html)
        modal = html[html.index('id="importModal"'):html.index('id="phoneImportModal"')]
        for column in IMPORT_COLUMNS:
            self.assertIn(column.header, modal)
        self.assertIn(f'{COLUMN_LABELS["tabel_number"]} *', modal)

    def test_the_page_checks_first_and_imports_in_batches(self):
        html = self.client.get('/employees/').content.decode()

        self.assertIn('dry_run: true', html)
        self.assertIn('const IMPORT_BATCH_SIZE = 50;', html)
        self.assertIn("cellDates: false, cellNF: true", html)   # санани ва узун рақамни SheetJS матнидан олмаймиз
        self.assertIn('row.__row = r + 1;', html)
        self.assertIn('downloadImportCredentials', html)

    def test_the_page_finds_the_header_row_with_the_importer_own_tabel_headers(self):
        html = self.client.get('/employees/').content.decode()

        start = html.index('<script id="import-tabel-headers" type="application/json">') + len('<script id="import-tabel-headers" type="application/json">')
        headers = json.loads(html[start:html.index('</script>', start)])
        importer = EmployeeImportExcelTemplateView()
        self.assertIn('табель_рақами', headers)
        self.assertEqual({importer.header_aliases[key] for key in headers}, {'tabel_number'})
        self.assertEqual(len(headers), sum(1 for field in importer.header_aliases.values() if field == 'tabel_number'))
