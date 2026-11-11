from io import BytesIO
import json
import os
import tempfile
from unittest.mock import patch
from pathlib import Path

from PIL import Image
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .bnpzid import issue_bnpzid_code
from .models import Department, Employee, EmployeeBaseImageChangeLog, EmployeeCertificate, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section, UserProfile
from .pki import generate_employee_pfx
from .serializers import EmployeeSerializer


def build_test_image(name='face.jpg'):
    image_buffer = BytesIO()
    Image.new('RGB', (32, 32), color='white').save(image_buffer, format='JPEG')
    image_buffer.seek(0)
    return SimpleUploadedFile(name, image_buffer.getvalue(), content_type='image/jpeg')


class BnpzIdTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_bnpzid_exchange_returns_claims_for_valid_code(self):
        user = User.objects.create_user(username='provider_user', password='secret123', first_name='Ali', last_name='Valiyev')
        profile = UserProfile.objects.create(user=user, role='admin')
        employee = Employee.objects.create(
            first_name='Ali',
            last_name='Valiyev',
            tabel_number='TB-100',
            user=user,
        )
        code = issue_bnpzid_code(
            user=user,
            profile=profile,
            client_id='tb-project',
            redirect_uri='http://localhost:5175/auth/signin',
        )

        response = self.client.post('/api/v1/auth/bnpzid/exchange/', {
            'client_id': 'tb-project',
            'client_secret': 'dev-bnpzid-secret',
            'redirect_uri': 'http://localhost:5175/auth/signin',
            'code': code,
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], 'provider_user')
        self.assertEqual(response.data['role'], 'admin')
        self.assertEqual(response.data['employee_slug'], employee.slug)
        self.assertEqual(response.data['tabel_number'], 'TB-100')

    @override_settings(BNPZID_ACCESS_CHECK_VERIFY_SSL=False, BNPZID_ACCESS_CHECK_CA_BUNDLE='')
    @patch('employees.web_views.requests.post')
    def test_bnpzid_access_check_uses_configured_ssl_verification_flag(self, post_mock):
        user = User.objects.create_user(username='provider_user', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        employee = Employee.objects.create(
            first_name='Ali',
            last_name='Valiyev',
            tabel_number='TB-100',
            user=user,
        )
        post_mock.return_value.status_code = 200
        post_mock.return_value.json.return_value = {'allowed': True, 'face_id_required': False}

        from .web_views import resolve_bnpzid_client_access

        result = resolve_bnpzid_client_access(user, {
            'client_id': 'tb-project',
            'redirect_uri': 'https://192.168.2.72:5175/auth/signin',
        })

        self.assertTrue(result['allowed'])
        self.assertEqual(post_mock.call_args.kwargs['verify'], False)
        self.assertEqual(post_mock.call_args.kwargs['json']['employee_slug'], employee.slug)

    @patch('employees.web_views.resolve_bnpzid_client_access', return_value={'allowed': True, 'requires_face_id': False, 'error': ''})
    def test_bnpzid_authorize_redirects_back_without_face_id_when_not_required(self, _access_mock):
        user = User.objects.create_user(username='provider_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)

        response = self.client.post('/bnpzid/authorize/', {
            'username': 'provider_admin',
            'password': 'secret123',
            'client_id': 'tb-project',
            'redirect_uri': 'http://localhost:5175/auth/signin',
            'state': 'demo-state',
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn('http://localhost:5175/auth/signin', response['Location'])
        self.assertIn('bnpzid_code=', response['Location'])
        self.assertIn('state=demo-state', response['Location'])

    @patch('employees.web_views.resolve_bnpzid_client_access', return_value={'allowed': True, 'requires_face_id': True, 'error': ''})
    def test_bnpzid_authorize_requires_face_id_on_employee_service_when_client_demands_it(self, _access_mock):
        user = User.objects.create_user(username='provider_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_USER, face_image=build_test_image('provider-face.jpg'))

        response = self.client.post('/bnpzid/authorize/', {
            'username': 'provider_admin',
            'password': 'secret123',
            'client_id': 'tb-project',
            'redirect_uri': 'http://localhost:5175/auth/signin',
            'state': 'demo-state',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Face ID тасдиғи')
        session = self.client.session
        self.assertEqual(session.get('pending_face_auth_user_id'), user.id)
        self.assertEqual(session.get('pending_bnpzid_request', {}).get('client_id'), 'tb-project')


class EmployeeSlugTests(TestCase):
    def test_employee_slug_is_generated_from_tabel_number_first_name_and_last_name(self):
        employee = Employee.objects.create(
            source_system='default',
            first_name='Ali',
            last_name='Valiyev',
            tabel_number='TB-100',
        )

        self.assertEqual(employee.slug, 'tb-100-ali-valiyev')

    def test_employee_slug_stays_unique_with_suffix(self):
        Employee.objects.create(
            first_name='Ali',
            last_name='Valiyev',
            tabel_number='TB-100',
        )
        second_employee = Employee.objects.create(
            first_name='Ali',
            last_name='Valiyev',
            tabel_number='TB 100',
        )

        self.assertEqual(second_employee.slug, 'tb-100-ali-valiyev-2')


class EmployeeDashboardSortingTests(TestCase):
    def test_template_employee_list_defaults_to_department_number_order(self):
        admin_user = User.objects.create_user(username='dashboard_sort_admin', password='secret123')
        UserProfile.objects.create(user=admin_user, role=ROLE_ADMIN)

        department_28 = Department.objects.create(name='28-Цех Рақамли технологиялар хизмати', boss_full_name='Boss 28')
        department_1 = Department.objects.create(name='1-Цех. Технология', boss_full_name='Boss 1')
        section_28 = Section.objects.create(name='Блок 28', department=department_28)
        section_1 = Section.objects.create(name='Блок 1', department=department_1)

        employee_28 = Employee.objects.create(
            first_name='Twenty',
            last_name='Eight',
            tabel_number='EMP-028',
            department=department_28,
            section=section_28,
        )
        employee_1 = Employee.objects.create(
            first_name='One',
            last_name='First',
            tabel_number='EMP-001',
            department=department_1,
            section=section_1,
        )
        employee_without_department = Employee.objects.create(
            first_name='No',
            last_name='Department',
            tabel_number='EMP-999',
        )

        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=admin_user)

        response = client.get('/api/template/employees/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['id'] for item in response.data['data'][:3]],
            [employee_1.id, employee_28.id, employee_without_department.id],
        )


class ImportEmployeeImagesCommandTests(TestCase):
    @override_settings(MEDIA_ROOT=None)
    def test_command_imports_images_by_tabel_number_and_skips_existing_without_overwrite(self):
        with tempfile.TemporaryDirectory() as media_root, tempfile.TemporaryDirectory() as images_dir:
            with override_settings(MEDIA_ROOT=media_root):
                employee_without_photo = Employee.objects.create(
                    first_name='Ali',
                    last_name='Valiyev',
                    tabel_number='1001',
                    login='ali.valiyev',
                )
                employee_with_photo = Employee.objects.create(
                    first_name='Zarina',
                    last_name='Karimova',
                    tabel_number='1002',
                    login='zarina.karimova',
                    base_image=build_test_image('existing.jpg'),
                )

                Path(images_dir, '1001.jpg').write_bytes(build_test_image('1001.jpg').read())
                Path(images_dir, '1002.jpg').write_bytes(build_test_image('1002.jpg').read())
                Path(images_dir, '9999.jpg').write_bytes(build_test_image('9999.jpg').read())
                Path(images_dir, 'notes.txt').write_text('ignore me', encoding='utf-8')

                call_command('import_employee_images', images_dir)

                employee_without_photo.refresh_from_db()
                employee_with_photo.refresh_from_db()

                self.assertTrue(bool(employee_without_photo.base_image))
                self.assertIn('1001', employee_without_photo.base_image.name)
                self.assertIn('existing.jpg', employee_with_photo.base_image.name)

    def test_command_overwrites_existing_photo_when_requested(self):
        with tempfile.TemporaryDirectory() as media_root, tempfile.TemporaryDirectory() as images_dir:
            with override_settings(MEDIA_ROOT=media_root):
                employee = Employee.objects.create(
                    first_name='Ali',
                    last_name='Valiyev',
                    tabel_number='1003',
                    login='ali.valiyev.1003',
                    base_image=build_test_image('old-photo.jpg'),
                )

                Path(images_dir, '1003.png').write_bytes(build_test_image('1003.jpg').read())

                old_name = employee.base_image.name
                call_command('import_employee_images', images_dir, '--overwrite')

                employee.refresh_from_db()
                self.assertTrue(bool(employee.base_image))
                self.assertNotEqual(employee.base_image.name, old_name)
                self.assertTrue(employee.base_image.name.endswith('1003.png'))


class UserTemplateViewPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(username='admin_user', password='secret123')
        UserProfile.objects.create(user=self.admin_user, role=ROLE_ADMIN)

        self.hr_user = User.objects.create_user(username='hr_user', password='secret123')
        UserProfile.objects.create(user=self.hr_user, role=ROLE_HR)

        self.regular_user = User.objects.create_user(username='regular_user', password='secret123')
        self.regular_profile = UserProfile.objects.create(user=self.regular_user, role=ROLE_USER)

        self.other_user = User.objects.create_user(username='other_user', password='secret123')
        self.other_profile = UserProfile.objects.create(user=self.other_user, role=ROLE_USER)
        self.other_employee = Employee.objects.create(
            first_name='Other',
            last_name='User',
            tabel_number='EMP-102',
            login='other_user',
            password_hash=self.other_user.password,
            user=self.other_user,
        )

    def test_admin_can_update_another_user_profile(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.patch(
            f'/api/template/users/{self.other_profile.pk}/',
            {'first_name': 'Updated'},
            format='multipart',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 200)
        self.other_user.refresh_from_db()
        self.assertEqual(self.other_user.first_name, 'Updated')

    def test_admin_can_open_user_access_page_and_api(self):
        self.client.force_login(self.admin_user)

        web_response = self.client.get('/settings/access/')
        api_response = self.client.get('/api/template/user-access/')

        self.assertEqual(web_response.status_code, 200)
        self.assertEqual(api_response.status_code, 200)

    def test_hr_cannot_open_user_access_page_or_api(self):
        self.client.force_login(self.hr_user)

        web_response = self.client.get('/settings/access/')
        api_response = self.client.get('/api/template/user-access/')

        self.assertEqual(web_response.status_code, 302)
        self.assertEqual(web_response['Location'], '/')
        self.assertEqual(api_response.status_code, 403)

    def test_hr_can_update_another_user_profile(self):
        self.client.force_authenticate(user=self.hr_user)

        response = self.client.patch(
            f'/api/template/users/{self.other_profile.pk}/',
            {'last_name': 'Manager'},
            format='multipart',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 200)
        self.other_user.refresh_from_db()
        self.assertEqual(self.other_user.last_name, 'Manager')

    def test_regular_user_cannot_update_another_user_profile(self):
        self.client.force_authenticate(user=self.regular_user)

        response = self.client.patch(
            f'/api/template/users/{self.other_profile.pk}/',
            {'first_name': 'Blocked'},
            format='multipart',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 403)

    def test_regular_user_can_update_own_profile(self):
        self.client.force_authenticate(user=self.regular_user)

        response = self.client.patch(
            f'/api/template/users/{self.regular_profile.pk}/',
            {'first_name': 'Self'},
            format='multipart',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 200)
        self.regular_user.refresh_from_db()
        self.assertEqual(self.regular_user.first_name, 'Self')

    def test_admin_delete_clears_employee_access_fields(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(
            f'/api/template/users/{self.other_profile.pk}/',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 200)
        self.other_employee.refresh_from_db()
        self.assertIsNone(self.other_employee.user)
        self.assertEqual(self.other_employee.login, '')
        self.assertEqual(self.other_employee.password_hash, '')

    def test_admin_update_syncs_linked_employee_credentials(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.patch(
            f'/api/template/users/{self.other_profile.pk}/',
            {
                'username': 'other_user_new',
                'password': 'newpass123',
                'is_active': False,
            },
            format='multipart',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 200)
        self.other_user.refresh_from_db()
        self.other_employee.refresh_from_db()
        self.assertEqual(self.other_user.username, 'other_user_new')
        self.assertEqual(self.other_employee.login, 'other_user_new')
        self.assertFalse(self.other_employee.is_active)
        self.assertTrue(check_password('newpass123', self.other_employee.password_hash))

    def test_user_detail_uses_linked_employee_image(self):
        self.client.force_authenticate(user=self.admin_user)
        self.other_employee.base_image = build_test_image('employee-base.jpg')
        self.other_employee.save(update_fields=['base_image'])

        response = self.client.get(
            f'/api/template/users/{self.other_profile.pk}/',
            HTTP_HOST='192.168.2.72',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['data']['has_face_image'])
        self.assertIn('employee_base_images', response.data['data']['face_image_url'])


class EmployeeCredentialSyncTests(TestCase):
    def test_employee_serializer_accepts_dotted_date_format(self):
        serializer = EmployeeSerializer(
            data={
                'first_name': 'Date',
                'last_name': 'Tester',
                'tabel_number': 'EMP-099',
                'date_of_birth': '30.12.2002',
                'date_of_employment': '15.01.2024',
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        employee = serializer.save()

        self.assertEqual(employee.date_of_birth.isoformat(), '2002-12-30')
        self.assertEqual(employee.date_of_employment.isoformat(), '2024-01-15')
        self.assertEqual(EmployeeSerializer(employee).data['date_of_birth'], '30.12.2002')
        self.assertEqual(EmployeeSerializer(employee).data['date_of_employment'], '15.01.2024')

    def test_employee_serializer_accepts_blank_optional_dates_on_partial_update(self):
        employee = Employee.objects.create(
            first_name='Blank',
            last_name='Date',
            tabel_number='EMP-110',
            date_of_birth='2002-12-30',
            date_of_employment='2024-01-15',
        )

        serializer = EmployeeSerializer(
            employee,
            data={
                'date_of_birth': '',
                'date_of_employment': '',
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated_employee = serializer.save()

        self.assertIsNone(updated_employee.date_of_birth)
        self.assertIsNone(updated_employee.date_of_employment)

    def test_employee_update_syncs_linked_user_credentials(self):
        user = User.objects.create_user(username='worker_old', password='oldpass123', first_name='Old', last_name='Name')
        UserProfile.objects.create(user=user, role=ROLE_USER)
        employee = Employee.objects.create(
            first_name='Old',
            last_name='Name',
            tabel_number='EMP-100',
            login='worker_old',
            user=user,
        )

        serializer = EmployeeSerializer(
            employee,
            data={
                'first_name': 'New',
                'last_name': 'Worker',
                'login': 'worker_new',
                'password': 'newpass123',
                'password_confirm': 'newpass123',
                'is_active': False,
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        user.refresh_from_db()
        employee.refresh_from_db()

        self.assertEqual(user.username, 'worker_new')
        self.assertEqual(user.first_name, 'New')
        self.assertEqual(user.last_name, 'Worker')
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password('newpass123'))
        self.assertTrue(check_password('newpass123', employee.password_hash))

    def test_employee_update_creates_linked_user_when_login_added(self):
        employee = Employee.objects.create(
            first_name='Sardor',
            last_name='Example',
            tabel_number='EMP-101',
        )

        serializer = EmployeeSerializer(
            employee,
            data={
                'login': 'sardor_example',
                'password': 'sardor123',
                'password_confirm': 'sardor123',
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        employee.refresh_from_db()
        self.assertIsNotNone(employee.user)
        self.assertEqual(employee.user.username, 'sardor_example')
        self.assertTrue(employee.user.check_password('sardor123'))
        self.assertEqual(employee.user.profile.role, ROLE_USER)


class EmployeeExcelImportTemplateViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='import_admin', password='secret123')
        UserProfile.objects.create(user=self.user, role=ROLE_ADMIN)
        self.client.force_authenticate(user=self.user)

    def test_import_excel_creates_employees_and_org_structure(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': [
                    {
                        'Табельный номер': 'EMP-901',
                        'Имя': 'Ali',
                        'Фамилия': 'Valiyev',
                        'Отчество': 'Karimovich',
                        'Цех': 'Литейный цех',
                        'Отдел': 'Смена A',
                        'Должность': 'Оператор',
                        'Дата приема на работу': '2024-01-15',
                        'Активен': 'Да',
                    },
                    {
                        'Табельный номер': 'EMP-902',
                        'Имя': 'Zarina',
                        'Фамилия': 'Karimova',
                        'Цех': 'Литейный цех',
                        'Отдел': 'Смена B',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['created_count'], 2)
        employee_1 = Employee.objects.get(tabel_number='EMP-901')
        employee_2 = Employee.objects.get(tabel_number='EMP-902')
        self.assertTrue(Department.objects.filter(name='Литейный цех').exists())
        self.assertTrue(Section.objects.filter(name='Смена A', department__name='Литейный цех').exists())
        self.assertTrue(Section.objects.filter(name='Смена B', department__name='Литейный цех').exists())
        self.assertEqual(employee_1.login, 'ali.valiyev')
        self.assertEqual(employee_2.login, 'zarina.karimova')
        self.assertIsNotNone(employee_1.user)
        self.assertEqual(employee_1.user.profile.role, ROLE_USER)
        credentials = {item['tabel_number']: item for item in response.data['employees']}
        self.assertTrue(employee_1.user.check_password(credentials['EMP-901']['generated_password']))
        self.assertTrue(check_password(credentials['EMP-901']['generated_password'], employee_1.password_hash))
        self.assertEqual(credentials['EMP-901']['login'], 'ali.valiyev')
        self.assertEqual(credentials['EMP-901']['role'], ROLE_USER)

    def test_import_excel_rejects_duplicate_tabel_numbers_in_file(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': [
                    {
                        'Табельный номер': 'EMP-903',
                        'Имя': 'First',
                        'Фамилия': 'Worker',
                    },
                    {
                        'Табельный номер': 'EMP-903',
                        'Имя': 'Second',
                        'Фамилия': 'Worker',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])
        self.assertEqual(Employee.objects.filter(tabel_number='EMP-903').count(), 0)

    def test_import_excel_ignores_provided_login_password_and_role(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': [
                    {
                        'Табельный номер': 'EMP-904',
                        'Имя': 'Ali',
                        'Фамилия': 'Valiyev',
                        'Логин': 'custom_admin',
                        'Пароль': 'admin123',
                        'Роль': 'admin',
                        'Активен': 'Нет',
                        'Источник системы': 'tb-project',
                        'Внешний ID': 'remote-904',
                        'Метаданные': '{"source":"excel"}',
                    },
                    {
                        'Табельный номер': 'EMP-905',
                        'Имя': 'Ali',
                        'Фамилия': 'Valiyev',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        employee_1 = Employee.objects.get(tabel_number='EMP-904')
        employee_2 = Employee.objects.get(tabel_number='EMP-905')
        self.assertEqual(employee_1.login, 'ali.valiyev')
        self.assertEqual(employee_2.login, 'ali.valiyev.emp.905')
        self.assertNotEqual(employee_1.login, 'custom_admin')
        self.assertEqual(employee_1.user.profile.role, ROLE_USER)
        self.assertTrue(employee_1.is_active)
        self.assertEqual(employee_1.source_system, 'default')
        self.assertEqual(employee_1.external_id, '')
        self.assertEqual(employee_1.metadata, {})
        generated = {item['tabel_number']: item for item in response.data['employees']}
        self.assertNotEqual(generated['EMP-904']['generated_password'], 'admin123')
        self.assertTrue(employee_1.user.check_password(generated['EMP-904']['generated_password']))

    def test_import_excel_returns_uzbek_message_for_existing_tabel_number(self):
        Employee.objects.create(
            tabel_number='EMP-907',
            first_name='Existing',
            last_name='Worker',
            login='existing.worker',
        )

        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': [
                    {
                        'Табельный номер': 'EMP-907',
                        'Имя': 'New',
                        'Фамилия': 'Worker',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])
        self.assertEqual(
            response.data['errors'][0]['errors']['tabel_number'][0],
            'Бундай табель рақамли ходим аллақачон қўшилган.',
        )

    def test_import_excel_accepts_common_excel_text_date_formats(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': [
                    {
                        'Табельный номер': 'EMP-906',
                        'Имя': 'Dilnoza',
                        'Фамилия': 'Karimova',
                        'Дата рождения': '30.12.02 00:00:00',
                        'Дата приема на работу': '1/15/24',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        employee = Employee.objects.get(tabel_number='EMP-906')
        self.assertEqual(employee.date_of_birth.isoformat(), '2002-12-30')
        self.assertEqual(employee.date_of_employment.isoformat(), '2024-01-15')

    def test_import_excel_attaches_images_by_tabel_number_filename(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': json.dumps([
                    {
                        'Табельный номер': 'EMP-908',
                        'Имя': 'Photo',
                        'Фамилия': 'Worker',
                    },
                    {
                        'Табельный номер': 'EMP-909',
                        'Имя': 'NoPhoto',
                        'Фамилия': 'Worker',
                    },
                ]),
                'images': [
                    build_test_image('EMP-908.jpg'),
                ],
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 201, response.data)
        employee_with_image = Employee.objects.get(tabel_number='EMP-908')
        employee_without_image = Employee.objects.get(tabel_number='EMP-909')
        self.assertTrue(bool(employee_with_image.base_image))
        self.assertFalse(bool(employee_without_image.base_image))
        self.assertEqual(response.data['imported_with_images'], 1)

    def test_import_excel_supports_template_headers_with_robe_and_image(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': json.dumps([
                    {
                        'Табельный номер *': 'EMP-911',
                        'Фамилия *': 'Karimov',
                        'Имя *': 'Aziz',
                        'Отчество *': 'Azamatovich',
                        'Должность *': 'Engineer',
                        'Цех *': '28-цех Рақамли технологиялар',
                        'Отдел *': 'Компьютер техникаси, тармоқни',
                        'Руководитель цеха': 'Boss Name',
                        'Пол': 'М',
                        'Дата рождения': '23.04.1999',
                        'Телефон 1': '+998 93 999 23 04',
                        'Телефон 2': '',
                        'Спецодежда *': '54',
                        'Обувь *': '43',
                        'Куртка *': '58',
                        'Футболка *': '52',
                        'Халат': '56',
                    },
                ]),
                'images': [
                    build_test_image('EMP-911.jpg'),
                ],
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 201, response.data)
        employee = Employee.objects.get(tabel_number='EMP-911')
        self.assertEqual(employee.first_name, 'Aziz')
        self.assertEqual(employee.last_name, 'Karimov')
        self.assertEqual(employee.surname, 'Azamatovich')
        self.assertEqual(employee.position, 'Engineer')
        self.assertEqual(employee.department.name, '28-цех Рақамли технологиялар')
        self.assertEqual(employee.section.name, 'Компьютер техникаси, тармоқни')
        self.assertEqual(employee.department.boss_full_name, 'Boss Name')
        self.assertEqual(employee.gender, 'M')
        self.assertEqual(employee.date_of_birth.isoformat(), '1999-04-23')
        self.assertEqual(employee.phone_number_1, '+998 93 999 23 04')
        self.assertEqual(employee.special_clothing_size, '54')
        self.assertEqual(employee.shoe_size, '43')
        self.assertEqual(employee.jacket_size, '58')
        self.assertEqual(employee.tshirt_size, '52')
        self.assertEqual(employee.clothe_size, '56')
        self.assertTrue(bool(employee.base_image))

    def test_import_excel_allows_empty_optional_personal_fields(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': json.dumps([
                    {
                        'Табельный номер': 'EMP-912',
                        'Фамилия': 'Optional',
                        'Имя': 'Fields',
                        'Отчество': '',
                        'Пол': '',
                        'Дата рождения': '',
                        'Телефон 1': '',
                        'Телефон 2': '',
                        'Халат': '',
                    },
                ]),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 201, response.data)
        employee = Employee.objects.get(tabel_number='EMP-912')
        self.assertEqual(employee.surname, '')
        self.assertEqual(employee.gender, '')
        self.assertIsNone(employee.date_of_birth)
        self.assertEqual(employee.phone_number_1, '')
        self.assertEqual(employee.phone_number_2, '')
        self.assertEqual(employee.clothe_size, '')

    def test_import_excel_ignores_rows_with_only_unsupported_columns(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': json.dumps([
                    {
                        'Табельный номер': 'EMP-913',
                        'Фамилия': 'Valid',
                        'Имя': 'Row',
                    },
                    {
                        '__EMPTY': 'Примечание под таблицей',
                    },
                ]),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Employee.objects.filter(tabel_number='EMP-913').exists())

    def test_import_excel_rejects_duplicate_uploaded_image_names(self):
        response = self.client.post(
            '/api/template/employees/import-excel/',
            {
                'rows': json.dumps([
                    {
                        'Табельный номер': 'EMP-910',
                        'Имя': 'Image',
                        'Фамилия': 'Duplicate',
                    },
                ]),
                'images': [
                    build_test_image('EMP-910.jpg'),
                    build_test_image('EMP-910.png'),
                ],
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])
        self.assertIn('EMP-910', response.data['errors'][0]['message'])
        self.assertFalse(Employee.objects.filter(tabel_number='EMP-910').exists())

    def test_employee_update_syncs_profile_face_image(self):
        user = User.objects.create_user(username='worker_face', password='oldpass123')
        profile = UserProfile.objects.create(user=user, role=ROLE_USER)
        employee = Employee.objects.create(
            first_name='Face',
            last_name='Worker',
            tabel_number='EMP-103',
            login='worker_face',
            user=user,
        )

        serializer = EmployeeSerializer(
            employee,
            data={'base_image': build_test_image('employee-base.jpg')},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        profile.refresh_from_db()
        employee.refresh_from_db()

        self.assertTrue(bool(employee.base_image))
        self.assertTrue(bool(profile.face_image))

    def test_employee_avatar_update_api_returns_success(self):
        user = User.objects.create_user(username='worker_api', password='oldpass123')
        UserProfile.objects.create(user=user, role=ROLE_USER)
        employee = Employee.objects.create(
            first_name='Api',
            last_name='Worker',
            tabel_number='EMP-105',
            login='worker_api',
            user=user,
        )

        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=user)
        response = client.patch(
            f'/api/v1/employees/{employee.slug}/',
            {'base_image': build_test_image('avatar-update.jpg')},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        employee.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertTrue(bool(employee.base_image))
        self.assertTrue(bool(user.profile.face_image))

    def test_employee_avatar_update_api_accepts_long_uploaded_filename(self):
        user = User.objects.create_user(username='worker_api_long_name', password='oldpass123')
        UserProfile.objects.create(user=user, role=ROLE_USER)
        employee = Employee.objects.create(
            first_name='Long',
            last_name='Filename',
            tabel_number='EMP-106A',
            login='worker_api_long_name',
            user=user,
        )

        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=user)
        long_name = 'rasm_' + ('verylongname' * 10) + '.jpg'
        response = client.patch(
            f'/api/v1/employees/{employee.slug}/',
            {'base_image': build_test_image(long_name)},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200, response.data)
        employee.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertTrue(bool(employee.base_image))
        self.assertTrue(bool(user.profile.face_image))
        self.assertLessEqual(len(Path(employee.base_image.name).name), 100)


class EmployeeSelfServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='self_worker', password='secret123')
        UserProfile.objects.create(user=self.user, role=ROLE_USER)
        self.employee = Employee.objects.create(
            first_name='Self',
            last_name='Worker',
            tabel_number='EMP-106',
            login='self_worker',
            user=self.user,
            special_clothing_size='50',
            shoe_size='41',
            jacket_size='52',
            tshirt_size='L',
            position='Operator',
        )

    def test_regular_user_can_update_own_employee_sizes(self):
        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=self.user)

        response = client.patch(
            f'/api/v1/employees/{self.employee.slug}/',
            {
                'special_clothing_size': '54',
                'shoe_size': '42',
                'jacket_size': '56',
                'tshirt_size': 'XL',
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.special_clothing_size, '54')
        self.assertEqual(self.employee.shoe_size, '42')
        self.assertEqual(self.employee.jacket_size, '56')
        self.assertEqual(self.employee.tshirt_size, 'XL')

    def test_regular_user_cannot_update_non_size_fields(self):
        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=self.user)

        response = client.patch(
            f'/api/v1/employees/{self.employee.slug}/',
            {'position': 'Director'},
            format='multipart',
        )

        self.assertEqual(response.status_code, 403)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.position, 'Operator')

    def test_regular_user_detail_page_allows_only_size_editing(self):
        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(self.user)

        response = client.get(f'/employees/{self.employee.slug}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ўлчамларни таҳрирлаш')
        self.assertContains(response, 'Оддий ходим фақат шу бўлимни ўзгартира олади.')
        self.assertContains(response, 'Махсус кийим')
        self.assertContains(response, 'Куртка')
        self.assertContains(response, 'Футболка')


class EmployeeGradeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='grade_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.worker_user = User.objects.create_user(username='grade_worker', password='secret123')
        UserProfile.objects.create(user=self.worker_user, role=ROLE_USER)
        self.employee = Employee.objects.create(
            first_name='Grade',
            last_name='Worker',
            tabel_number='EMP-GRADE-1',
            login='grade_worker',
            user=self.worker_user,
            position='Operator',
        )

    def test_existing_employee_has_no_grade(self):
        self.assertIsNone(self.employee.grade)

    def test_admin_can_set_and_clear_grade(self):
        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=self.admin)

        response = client.patch(f'/api/v1/employees/{self.employee.slug}/', {'grade': '4'}, format='multipart')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['grade'], 4)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.grade, 4)

        # The edit form always submits the field, empty when left blank.
        response = client.patch(f'/api/v1/employees/{self.employee.slug}/', {'grade': ''}, format='multipart')
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.grade)

    def test_grade_is_optional_on_create(self):
        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=self.admin)

        response = client.post(
            '/api/v1/employees/',
            {'first_name': 'No', 'last_name': 'Grade', 'tabel_number': 'EMP-GRADE-2', 'position': 'Fitter', 'grade': ''},
            format='multipart',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(Employee.objects.get(tabel_number='EMP-GRADE-2').grade)

    def test_regular_user_cannot_change_grade(self):
        client = APIClient(HTTP_HOST='192.168.2.72')
        client.force_authenticate(user=self.worker_user)

        response = client.patch(f'/api/v1/employees/{self.employee.slug}/', {'grade': '8'}, format='multipart')

        self.assertEqual(response.status_code, 403)
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.grade)

    def test_edit_page_renders_grade_input_after_position(self):
        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(self.admin)

        response = client.get(reverse('employee-edit-web', kwargs={'slug': self.employee.slug}))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('id="grade"', html)
        self.assertLess(html.index('id="position"'), html.index('id="grade"'))


class SectionCodeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='section_code_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.department = Department.objects.create(name='1-Цех')
        self.client = APIClient(HTTP_HOST='192.168.2.72')
        self.client.force_authenticate(user=self.admin)

    def _create(self, name, code=None, department=None):
        payload = {'name': name, 'department_id': (department or self.department).id}
        if code is not None:
            payload['code'] = code
        return self.client.post('/api/template/sections/', payload, format='json')

    def test_existing_style_section_without_code_still_works(self):
        response = self._create('Без кода')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['code'], '')

    def test_section_can_be_created_with_code(self):
        response = self._create('Смена A', code=' 101 ')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['code'], '101')
        self.assertEqual(Section.objects.get(name='Смена A').code, '101')

    def test_many_sections_may_have_blank_code(self):
        self.assertEqual(self._create('Первый', code='').status_code, 201)
        self.assertEqual(self._create('Второй', code='').status_code, 201)

    def test_same_code_is_allowed_in_another_department(self):
        # «ID отдела» уникален в пределах цеха, а не во всей системе.
        other_department = Department.objects.create(name='2-Цех')
        self.assertEqual(self._create('Смена A', code='101').status_code, 201)

        response = self._create('Смена B', code='101', department=other_department)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Section.objects.filter(code='101').count(), 2)

    def test_duplicate_code_is_rejected_within_the_same_department(self):
        self.assertEqual(self._create('Смена A', code='101').status_code, 201)

        response = self._create('Смена B', code='101')

        self.assertEqual(response.status_code, 400)
        self.assertIn('code', response.data)
        self.assertFalse(Section.objects.filter(name='Смена B').exists())

    def test_editing_section_keeps_its_own_code_and_can_change_it(self):
        section = Section.objects.create(department=self.department, name='Смена A', code='101')

        response = self.client.put(
            f'/api/template/sections/{section.pk}/',
            {'code': '101', 'name': 'Смена A2', 'department_id': self.department.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.put(
            f'/api/template/sections/{section.pk}/',
            {'code': '202', 'name': 'Смена A2', 'department_id': self.department.id},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        section.refresh_from_db()
        self.assertEqual(section.code, '202')

    def test_update_without_code_field_does_not_wipe_existing_code(self):
        section = Section.objects.create(department=self.department, name='Смена A', code='101')

        response = self.client.put(
            f'/api/template/sections/{section.pk}/',
            {'name': 'Смена A', 'department_id': self.department.id},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        section.refresh_from_db()
        self.assertEqual(section.code, '101')

    def test_sections_can_be_searched_by_code(self):
        Section.objects.create(department=self.department, name='Смена A', code='777')
        Section.objects.create(department=self.department, name='Смена B', code='888')

        response = self.client.get('/api/template/sections/', {'search': '777'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['name'] for item in response.data['data']], ['Смена A'])

    def test_public_api_creation_also_rejects_duplicate_code(self):
        Section.objects.create(department=self.department, name='Смена A', code='101')

        response = self.client.post(
            '/api/v1/sections/',
            {'name': 'Смена B', 'code': '101', 'department_id': self.department.id},
            format='json',
        )

        self.assertEqual(response.status_code, 400)


class EmployeeWebRoutingTests(TestCase):
    def test_employees_alias_route_serves_dashboard_for_admin(self):
        user = User.objects.create_user(username='routing_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('employees-web'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard.html')

    def test_employee_delete_page_redirects_to_employees_alias_after_success(self):
        user = User.objects.create_user(username='delete_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        employee = Employee.objects.create(
            first_name='Delete',
            last_name='Worker',
            tabel_number='EMP-DELETE-1',
            login='delete.worker',
        )

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('employee-delete-web', kwargs={'slug': employee.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"window.location.href = '{reverse('employees-web')}';")

    def test_dashboard_template_does_not_render_slug_text_in_employee_name_block(self):
        user = User.objects.create_user(username='dashboard_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "${employee.slug || ''}")

    def test_profile_edit_template_does_not_access_missing_login_field_directly(self):
        user = User.objects.create_user(username='profile_user', password='secret123', first_name='Ali', last_name='Valiyev')
        UserProfile.objects.create(user=user, role=ROLE_USER)

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('profile-edit-web'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "const loginField = document.getElementById('login');")
        self.assertNotContains(response, "document.getElementById('login').value.trim()")
        self.assertNotContains(response, "const password = passwordField.value;")

    def test_employee_edit_template_clears_password_fields_on_load(self):
        user = User.objects.create_user(username='employee_edit_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        employee = Employee.objects.create(
            first_name='Timur',
            last_name='Bakayev',
            tabel_number='7777',
            login='admin',
        )

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('employee-edit-web', kwargs={'slug': employee.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "const passwordField = document.getElementById('password');")
        self.assertContains(response, "passwordField.value = '';")
        self.assertContains(response, "passwordConfirmField.value = '';")

    def test_employee_create_template_supports_inline_department_and_section_modals(self):
        user = User.objects.create_user(username='employee_create_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('employee-create-web'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="openDepartmentInlineModalButton"')
        self.assertContains(response, 'id="openSectionInlineModalButton"')
        self.assertContains(response, 'id="inlineDepartmentModal"')
        self.assertContains(response, 'id="inlineSectionModal"')
        self.assertContains(response, 'async function saveInlineDepartment(event)')
        self.assertContains(response, 'async function saveInlineSection(event)')
        self.assertNotContains(response, 'id="passport_series"')
        self.assertNotContains(response, 'id="passport_number"')
        self.assertNotContains(response, 'id="jshshir"')

    def test_employee_edit_template_supports_inline_department_and_section_modals(self):
        user = User.objects.create_user(username='employee_inline_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        department = Department.objects.create(name='1-Цех. Технология', boss_full_name='Boss 1')
        section = Section.objects.create(name='Смена A', department=department)
        employee = Employee.objects.create(
            first_name='Inline',
            last_name='Worker',
            tabel_number='EMP-INLINE-1',
            department=department,
            section=section,
        )

        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)

        response = client.get(reverse('employee-edit-web', kwargs={'slug': employee.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="openDepartmentInlineModalButton"')
        self.assertContains(response, 'id="openSectionInlineModalButton"')
        self.assertContains(response, 'id="inlineDepartmentModal"')
        self.assertContains(response, 'id="inlineSectionModal"')
        self.assertContains(response, 'function syncInlineDepartmentSelect(selectedDepartmentId = \'\')')
        self.assertContains(response, 'async function saveInlineSection(event)')
        self.assertNotContains(response, 'id="passport_series"')
        self.assertNotContains(response, 'id="passport_number"')
        self.assertNotContains(response, 'id="jshshir"')

    def test_employee_serializer_does_not_expose_removed_document_fields(self):
        employee = Employee.objects.create(
            first_name='No',
            last_name='Documents',
            tabel_number='EMP-NO-DOCS',
        )

        serialized = EmployeeSerializer(employee).data

        self.assertNotIn('passport_series', serialized)
        self.assertNotIn('passport_number', serialized)
        self.assertNotIn('jshshir', serialized)


class EmployeeTemplateDeleteTests(TestCase):
    def test_delete_endpoint_hard_deletes_employee_linked_user_and_images(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                admin = User.objects.create_user(username='employee_delete_admin', password='secret123')
                UserProfile.objects.create(user=admin, role=ROLE_ADMIN)

                linked_user = User.objects.create_user(username='employee_delete_user', password='secret123')
                linked_profile = UserProfile.objects.create(
                    user=linked_user,
                    role=ROLE_USER,
                    face_image=build_test_image('profile-face.jpg'),
                )
                employee = Employee.objects.create(
                    first_name='Hard',
                    last_name='Delete',
                    tabel_number='EMP-HARD-DELETE',
                    login='employee_delete_user',
                    password_hash=linked_user.password,
                    user=linked_user,
                    base_image=build_test_image('employee-base.jpg'),
                )

                base_image_path = employee.base_image.path
                face_image_path = linked_profile.face_image.path

                client = APIClient(HTTP_HOST='192.168.2.72')
                client.force_authenticate(user=admin)

                response = client.delete(f'/api/template/employees/{employee.pk}/')

                self.assertEqual(response.status_code, 204)
                self.assertFalse(Employee.objects.filter(pk=employee.pk).exists())
                self.assertFalse(User.objects.filter(pk=linked_user.pk).exists())
                self.assertFalse(UserProfile.objects.filter(pk=linked_profile.pk).exists())

            @override_settings(EMPLOYEE_SERVICE_API_KEYS=['dev-employee-service-key'])
            def test_employee_base_image_update_api_creates_audit_log(self):
                employee = Employee.objects.create(
                    first_name='Audit',
                    last_name='Target',
                    tabel_number='EMP-107',
                    base_image=build_test_image('old-base.jpg'),
                )

                client = APIClient(HTTP_HOST='192.168.2.72', HTTP_X_EMPLOYEE_SERVICE_KEY='dev-employee-service-key')
                response = client.patch(
                    f'/api/v1/employees/{employee.slug}/base-image/',
                    {'base_image': build_test_image('new-base.jpg')},
                    format='multipart',
                    HTTP_X_ACTOR_USER_ID='42',
                    HTTP_X_ACTOR_USERNAME='warehouse_user',
                    HTTP_X_ACTOR_ROLE='warehouse_staff',
                )

                self.assertEqual(response.status_code, 200)
                employee.refresh_from_db()
                log = EmployeeBaseImageChangeLog.objects.get(employee=employee)
                self.assertEqual(log.changed_by_username, 'warehouse_user')
                self.assertEqual(log.changed_by_user_id, '42')
                self.assertEqual(log.changed_by_role, 'warehouse_staff')
                self.assertIn('old-base', log.old_image)
                self.assertIn('new-base', log.new_image)
                self.assertFalse(os.path.exists(base_image_path))
                self.assertFalse(os.path.exists(face_image_path))


class LoginFaceVerifyTests(TestCase):
    def test_successful_face_verify_completes_pending_login(self):
        user = User.objects.create_user(username='face_admin', password='secret123')
        UserProfile.objects.create(
            user=user,
            role=ROLE_ADMIN,
        )
        Employee.objects.create(
            first_name='Face',
            last_name='Admin',
            tabel_number='EMP-104',
            login='face_admin',
            user=user,
            base_image=build_test_image('employee-base.jpg'),
        )

        client = Client(HTTP_HOST='192.168.2.72')
        session = client.session
        session['pending_face_auth_user_id'] = user.id
        session['pending_face_auth_backend'] = 'django.contrib.auth.backends.ModelBackend'
        session['pending_face_auth_next'] = '/employees/'
        session.save()

        with patch('employees.views.decode_image_to_pil', return_value=Image.new('RGB', (32, 32), color='white')):
            with patch('employees.views.calculate_face_similarity', return_value=99.5):
                response = client.post(
                    '/api/v1/auth/login-face-verify/',
                    data={'captured_images': ['data:image/jpeg;base64,ZmFrZQ==']},
                    content_type='application/json',
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['verified'])
        self.assertEqual(response.json()['redirect_url'], '/employees/')
        self.assertEqual(int(client.session['_auth_user_id']), user.id)
        self.assertNotIn('pending_face_auth_user_id', client.session)

    def test_face_verify_supports_optional_head_pose_liveness(self):
        user = User.objects.create_user(username='face_pose_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        Employee.objects.create(
            first_name='Face',
            last_name='Pose',
            tabel_number='EMP-106',
            login='face_pose_admin',
            user=user,
            base_image=build_test_image('employee-base.jpg'),
        )

        client = Client(HTTP_HOST='192.168.2.72')
        session = client.session
        session['pending_face_auth_user_id'] = user.id
        session['pending_face_auth_backend'] = 'django.contrib.auth.backends.ModelBackend'
        session['pending_face_auth_next'] = '/employees/'
        session.save()

        with patch('employees.views.decode_image_to_pil', return_value=Image.new('RGB', (32, 32), color='white')):
            with patch('employees.views.calculate_face_similarity', return_value=99.5):
                with patch('employees.views.estimate_head_pose_from_frames', return_value={
                    'valid_frames': 3,
                    'yaw_range': 18.5,
                    'max_right_yaw': 22.0,
                    'max_left_yaw': -4.0,
                    'max_up_pitch': -1.0,
                }):
                    response = client.post(
                        '/api/v1/auth/login-face-verify/',
                        data={
                            'captured_images': [
                                'data:image/jpeg;base64,ZmFrZQ==',
                                'data:image/jpeg;base64,ZmFrZQ==',
                                'data:image/jpeg;base64,ZmFrZQ==',
                            ],
                            'required_direction': 'right',
                        },
                        content_type='application/json',
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['verified'])
        self.assertTrue(payload['head_pose_passed'])
        self.assertEqual(payload['required_direction'], 'right')
        self.assertEqual(payload['redirect_url'], '/employees/')

    def test_face_verify_rejects_when_requested_head_pose_not_detected(self):
        user = User.objects.create_user(username='face_pose_fail', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        Employee.objects.create(
            first_name='Face',
            last_name='Fail',
            tabel_number='EMP-107',
            login='face_pose_fail',
            user=user,
            base_image=build_test_image('employee-base.jpg'),
        )

        client = Client(HTTP_HOST='192.168.2.72')
        session = client.session
        session['pending_face_auth_user_id'] = user.id
        session['pending_face_auth_backend'] = 'django.contrib.auth.backends.ModelBackend'
        session['pending_face_auth_next'] = '/employees/'
        session.save()

        with patch('employees.views.decode_image_to_pil', return_value=Image.new('RGB', (32, 32), color='white')):
            with patch('employees.views.calculate_face_similarity', return_value=99.5):
                with patch('employees.views.estimate_head_pose_from_frames', return_value={
                    'valid_frames': 3,
                    'yaw_range': 4.5,
                    'max_right_yaw': 8.0,
                    'max_left_yaw': -3.0,
                    'max_up_pitch': -1.0,
                }):
                    response = client.post(
                        '/api/v1/auth/login-face-verify/',
                        data={
                            'captured_images': [
                                'data:image/jpeg;base64,ZmFrZQ==',
                                'data:image/jpeg;base64,ZmFrZQ==',
                                'data:image/jpeg;base64,ZmFrZQ==',
                            ],
                            'required_direction': 'right',
                        },
                        content_type='application/json',
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['verified'])
        self.assertFalse(payload['head_pose_passed'])
        self.assertEqual(payload['required_direction'], 'right')
        self.assertNotIn('redirect_url', payload)
        self.assertNotIn('_auth_user_id', client.session)

    def test_bnpzid_face_verify_allows_regular_user_when_client_requires_face_id(self):
        user = User.objects.create_user(username='face_user', password='secret123')
        profile = UserProfile.objects.create(
            user=user,
            role=ROLE_USER,
        )
        Employee.objects.create(
            first_name='Face',
            last_name='User',
            tabel_number='EMP-105',
            login='face_user',
            user=user,
            base_image=build_test_image('employee-base.jpg'),
        )

        client = Client(HTTP_HOST='192.168.2.72')
        session = client.session
        session['pending_face_auth_user_id'] = user.id
        session['pending_face_auth_backend'] = 'django.contrib.auth.backends.ModelBackend'
        session['pending_face_auth_next'] = ''
        session['pending_bnpzid_request'] = {
            'client_id': 'tb-project',
            'redirect_uri': 'https://192.168.2.72:5175/auth/signin',
            'state': 'abc123',
        }
        session.save()

        with patch('employees.views.decode_image_to_pil', return_value=Image.new('RGB', (32, 32), color='white')):
            with patch('employees.views.calculate_face_similarity', return_value=99.5):
                response = client.post(
                    '/api/v1/auth/login-face-verify/',
                    data={'captured_images': ['data:image/jpeg;base64,ZmFrZQ==']},
                    content_type='application/json',
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['verified'])
        self.assertIn('bnpzid_code=', response.json()['redirect_url'])
        self.assertIn('state=abc123', response.json()['redirect_url'])
        self.assertEqual(int(client.session['_auth_user_id']), user.id)
        self.assertNotIn('pending_face_auth_user_id', client.session)
        self.assertNotIn('pending_bnpzid_request', client.session)


class EmployeeCertificateIssueTests(TestCase):
    def setUp(self):
        pki_dir_context = tempfile.TemporaryDirectory()
        self.addCleanup(pki_dir_context.cleanup)
        override = override_settings(PKI_DIR=Path(pki_dir_context.name))
        override.enable()
        self.addCleanup(override.disable)

        self.client = APIClient()
        self.admin_user = User.objects.create_user(username='pki_admin', password='secret123')
        UserProfile.objects.create(user=self.admin_user, role=ROLE_ADMIN)

        self.hr_user = User.objects.create_user(username='pki_hr', password='secret123')
        UserProfile.objects.create(user=self.hr_user, role=ROLE_HR)

        self.worker_user = User.objects.create_user(username='pki_worker', password='secret123')
        UserProfile.objects.create(user=self.worker_user, role=ROLE_USER)
        self.employee = Employee.objects.create(
            first_name='Key',
            last_name='Holder',
            tabel_number='EMP-PKI-1',
            login='pki_worker',
            user=self.worker_user,
        )

    def test_admin_can_issue_certificate(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(f'/api/template/employees/{self.employee.pk}/certificate/', HTTP_HOST='192.168.2.72')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/x-pkcs12')
        self.assertTrue(response['X-Pfx-Password'])
        self.assertIn(f'{self.employee.tabel_number}.pfx', response['Content-Disposition'])
        self.assertEqual(EmployeeCertificate.objects.filter(employee=self.employee, revoked=False).count(), 1)

    def test_issuing_new_certificate_revokes_previous_one(self):
        self.client.force_authenticate(user=self.admin_user)

        first_response = self.client.post(f'/api/template/employees/{self.employee.pk}/certificate/', HTTP_HOST='192.168.2.72')
        second_response = self.client.post(f'/api/template/employees/{self.employee.pk}/certificate/', HTTP_HOST='192.168.2.72')

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        certificates = list(EmployeeCertificate.objects.filter(employee=self.employee).order_by('id'))
        self.assertEqual(len(certificates), 2)
        self.assertTrue(certificates[0].revoked)
        self.assertFalse(certificates[1].revoked)

    def test_hr_cannot_issue_certificate(self):
        self.client.force_authenticate(user=self.hr_user)

        response = self.client.post(f'/api/template/employees/{self.employee.pk}/certificate/', HTTP_HOST='192.168.2.72')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmployeeCertificate.objects.filter(employee=self.employee).exists())

    def test_certificate_cannot_be_issued_without_linked_login(self):
        employee_without_login = Employee.objects.create(
            first_name='No',
            last_name='Login',
            tabel_number='EMP-PKI-2',
        )
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(f'/api/template/employees/{employee_without_login.pk}/certificate/', HTTP_HOST='192.168.2.72')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])


class ClientCertificateAuthenticationMiddlewareTests(TestCase):
    def setUp(self):
        pki_dir_context = tempfile.TemporaryDirectory()
        self.addCleanup(pki_dir_context.cleanup)
        override = override_settings(PKI_DIR=Path(pki_dir_context.name))
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='cert_worker', password='secret123')
        UserProfile.objects.create(user=self.user, role=ROLE_USER)
        self.employee = Employee.objects.create(
            first_name='Cert',
            last_name='Worker',
            tabel_number='EMP-PKI-3',
            login='cert_worker',
            user=self.user,
        )
        issued = generate_employee_pfx(self.employee)
        self.certificate = EmployeeCertificate.objects.create(
            employee=self.employee,
            serial_number=issued['serial_number'],
            fingerprint_sha256=issued['fingerprint_sha256'],
            expires_at=issued['expires_at'],
        )

    def test_verified_client_certificate_logs_user_in(self):
        client = Client(HTTP_HOST='192.168.2.72')

        client.get(
            '/',
            HTTP_X_SSL_CLIENT_VERIFY='SUCCESS',
            HTTP_X_SSL_CLIENT_SERIAL=self.certificate.serial_number,
        )

        self.assertEqual(int(client.session['_auth_user_id']), self.user.id)

    def test_revoked_certificate_does_not_log_user_in(self):
        self.certificate.revoked = True
        self.certificate.save(update_fields=['revoked'])
        client = Client(HTTP_HOST='192.168.2.72')

        client.get(
            '/',
            HTTP_X_SSL_CLIENT_VERIFY='SUCCESS',
            HTTP_X_SSL_CLIENT_SERIAL=self.certificate.serial_number,
        )

        self.assertNotIn('_auth_user_id', client.session)

    def test_unverified_handshake_does_not_log_user_in(self):
        client = Client(HTTP_HOST='192.168.2.72')

        client.get(
            '/',
            HTTP_X_SSL_CLIENT_VERIFY='NONE',
            HTTP_X_SSL_CLIENT_SERIAL=self.certificate.serial_number,
        )

        self.assertNotIn('_auth_user_id', client.session)