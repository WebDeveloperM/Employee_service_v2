"""Хавфсизлик аудити тузатишлари: ҳар бир ёпилган тешик учун тест (қайта очилмасин)."""
import os
import subprocess
import sys
from io import BytesIO, StringIO
from unittest.mock import patch

import openpyxl
from PIL import Image
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section, UserProfile
from .security import ScopedThrottleExceptServiceKey
from .staff_export import build_staff_workbook, staff_export_groups


SERVICE_KEY = 'svc-key-for-tests-0123456789abcdefghijklmnop'
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'security-tests'}}
STRONG_PASSWORD = 'Kuchli-parol-2026'


def photo(name='face.jpg'):
    buffer = BytesIO()
    Image.new('RGB', (32, 32), color='white').save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


def make_account(username, role, *, with_photo=True, face_id_login_enabled=True):
    user = User.objects.create_user(username=username, password=STRONG_PASSWORD)
    UserProfile.objects.create(user=user, role=role, face_id_login_enabled=face_id_login_enabled)
    employee = Employee.objects.create(
        first_name='Ism', last_name=username, tabel_number=f'T-{username}', login=username, user=user,
        base_image=photo(f'{username}.jpg') if with_photo else None,
    )
    return user, employee


def api_as(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def service_client():
    client = APIClient()
    client.credentials(HTTP_X_EMPLOYEE_SERVICE_KEY=SERVICE_KEY)
    return client


class SecurityTestCase(TestCase):
    def setUp(self):
        self.admin, self.admin_employee = make_account('sec_admin', ROLE_ADMIN)
        self.hr, self.hr_employee = make_account('sec_hr', ROLE_HR)
        self.plain, self.plain_employee = make_account('sec_plain', ROLE_USER)
        self.other, self.other_employee = make_account('sec_other', ROLE_USER)
        self.department = Department.objects.create(name='1-Цех')
        self.section = Section.objects.create(department=self.department, name='Бўлим')


class PlainUserCannotManageDataTests(SecurityTestCase):
    """Олдин ҳар қандай кирган ходим цех, бўлим ва ходимларни яратиши, ўзгартириши ва ўчириши мумкин эди."""

    def test_plain_user_still_reads_but_cannot_change_departments_and_sections(self):
        api = api_as(self.plain)
        for read_url in ('/api/template/departments/', '/api/template/sections/', '/api/v1/departments/', '/api/v1/sections/'):
            with self.subTest(read=read_url):
                self.assertEqual(api.get(read_url).status_code, 200)

        attempts = [
            ('post', '/api/template/departments/', {'name': 'Бузғунчи цех'}),
            ('patch', f'/api/template/departments/{self.department.pk}/', {'name': 'Ўзгарган'}),
            ('delete', f'/api/template/departments/{self.department.pk}/', None),
            ('post', '/api/template/sections/', {'name': 'Янги', 'department_id': self.department.pk}),
            ('delete', f'/api/template/sections/{self.section.pk}/', None),
            ('post', '/api/v1/departments/', {'name': 'Бузғунчи цех 2'}),
            ('delete', f'/api/v1/departments/{self.department.pk}/', None),
            ('patch', f'/api/v1/sections/{self.section.pk}/', {'name': 'Ўзгарган'}),
        ]
        for method, url, data in attempts:
            with self.subTest(method=method, url=url):
                response = getattr(api, method)(url, data, format='json') if data else getattr(api, method)(url)
                self.assertEqual(response.status_code, 403, response.content)

        self.assertTrue(Department.objects.filter(pk=self.department.pk, name='1-Цех').exists())
        self.assertTrue(Section.objects.filter(pk=self.section.pk, name='Бўлим').exists())
        self.assertFalse(Department.objects.filter(name__startswith='Бузғунчи').exists())

    def test_hr_can_still_manage_departments(self):
        response = api_as(self.hr).post('/api/template/departments/', {'name': '2-Цех'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)

    def test_plain_user_cannot_use_the_employee_management_api(self):
        api = api_as(self.plain)
        other = self.other_employee
        self.assertEqual(api.get('/api/template/employees/').status_code, 403)
        self.assertEqual(api.post('/api/template/employees/', {'first_name': 'A', 'last_name': 'B', 'tabel_number': 'X1'}, format='json').status_code, 403)
        self.assertEqual(api.patch(f'/api/template/employees/{other.pk}/', {'first_name': 'Бузилган'}, format='json').status_code, 403)
        self.assertEqual(api.delete(f'/api/template/employees/{self.admin_employee.pk}/').status_code, 403)
        self.assertEqual(api.post('/api/v1/employees/', {'first_name': 'A', 'last_name': 'B', 'tabel_number': 'X2'}, format='json').status_code, 403)
        self.assertEqual(api.post('/api/v1/employees/upsert/', {'tabel_number': other.tabel_number, 'first_name': 'Бузилган'}, format='json').status_code, 403)
        self.assertEqual(api.post('/api/template/employees/import-excel/', {'rows': []}, format='json').status_code, 403)

        other.refresh_from_db()
        self.assertEqual(other.first_name, 'Ism')
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())   # админ ходими ўчмади, админ ҳам

    def test_plain_user_sees_only_himself_in_the_v1_employee_list(self):
        body = api_as(self.plain).get('/api/v1/employees/?no_pagination=true').json()
        self.assertEqual([item['tabel_number'] for item in body], [self.plain_employee.tabel_number])

        profileless = User.objects.create_user(username='no_profile', password=STRONG_PASSWORD)
        self.assertEqual(api_as(profileless).get('/api/v1/employees/?no_pagination=true').json(), [])
        self.assertEqual(api_as(profileless).get(f'/api/v1/employees/{self.other_employee.slug}/').status_code, 403)

    def test_plain_user_keeps_self_service_on_his_own_card_only(self):
        api = api_as(self.plain)
        own = f'/api/v1/employees/{self.plain_employee.slug}/'
        self.assertEqual(api.patch(own, {'shoe_size': '42'}, format='json').status_code, 200)
        self.assertEqual(api.patch(own, {'position': 'Директор'}, format='json').status_code, 403)
        self.assertEqual(api.patch(f'/api/v1/employees/{self.other_employee.slug}/', {'shoe_size': '40'}, format='json').status_code, 403)

    def test_plain_user_cannot_probe_other_faces_or_flip_face_id_checkout(self):
        api = api_as(self.plain)
        self.assertEqual(api.post(f'/api/v1/employees/{self.admin_employee.slug}/face-verify/', {'captured_image': 'x'}, format='json').status_code, 403)
        self.assertEqual(api.patch(f'/api/v1/employees/{self.other_employee.slug}/face-id-exemption/', {'requires_face_id_checkout': False}, format='json').status_code, 403)
        self.assertEqual(api.get('/api/v1/employees/base-image-change-logs/').status_code, 403)

    def test_management_pages_send_a_plain_user_back_to_his_card(self):
        client = Client()
        client.force_login(self.plain)
        own_card = f'/employees/{self.plain_employee.slug}/'
        for url in ('/departments/', '/sections/', '/employees/create/', f'/employees/{self.other_employee.slug}/delete/'):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], own_card)
        self.assertEqual(client.get(own_card).status_code, 200)


class RoleEscalationTests(SecurityTestCase):
    """Олдин оддий ходим ўзини администратор қила олар эди, HR эса администратор ҳисобини бошқарар эди."""

    def test_plain_user_cannot_make_himself_admin_but_can_edit_his_profile(self):
        api = api_as(self.plain)
        url = f'/api/template/users/{self.plain.profile.pk}/'

        self.assertEqual(api.patch(url, {'role': ROLE_ADMIN}, format='json').status_code, 403)
        self.assertEqual(api.patch(url, {'is_active': False}, format='json').status_code, 403)
        ok = api.patch(url, {'first_name': 'Янги', 'password': 'Yangi-parol-77'}, format='json')

        self.assertEqual(ok.status_code, 200, ok.content)
        self.plain.refresh_from_db()
        self.assertEqual((self.plain.profile.role, self.plain.first_name), (ROLE_USER, 'Янги'))
        self.assertTrue(self.plain.check_password('Yangi-parol-77'))

    def test_hr_cannot_create_admins_or_promote_anyone(self):
        api = api_as(self.hr)
        created = api.post('/api/template/users/', {'username': 'new_boss', 'password': STRONG_PASSWORD, 'role': ROLE_ADMIN}, format='json')
        promoted = api.patch(f'/api/template/users/{self.other.profile.pk}/', {'role': ROLE_HR}, format='json')
        self_promoted = api.patch(f'/api/template/users/{self.hr.profile.pk}/', {'role': ROLE_ADMIN}, format='json')
        employee_admin = api.post('/api/v1/employees/', {
            'first_name': 'A', 'last_name': 'B', 'tabel_number': 'NEW-1', 'login': 'emp_boss',
            'password': STRONG_PASSWORD, 'password_confirm': STRONG_PASSWORD, 'role': ROLE_ADMIN,
        }, format='json')

        self.assertEqual([created.status_code, promoted.status_code, self_promoted.status_code, employee_admin.status_code], [403] * 4)
        self.assertFalse(User.objects.filter(username__in=['new_boss', 'emp_boss']).exists())
        self.assertEqual(UserProfile.objects.filter(role=ROLE_ADMIN).count(), 1)

    def test_hr_still_creates_plain_users(self):
        response = api_as(self.hr).post('/api/template/users/', {'username': 'new_plain', 'password': STRONG_PASSWORD, 'role': ROLE_USER}, format='json')
        self.assertEqual(response.status_code, 201, response.content)

    def test_hr_cannot_take_over_an_admin_account_through_the_employee_card(self):
        api = api_as(self.hr)
        url = f'/api/template/employees/{self.admin_employee.pk}/'
        for payload in (
            {'password': 'Hijack-parol-1', 'password_confirm': 'Hijack-parol-1'},
            {'login': 'hijacked'},
            {'is_active': False},
        ):
            with self.subTest(payload=list(payload)):
                self.assertEqual(api.patch(url, payload, format='json').status_code, 403)
        self.assertEqual(api.delete(url).status_code, 403)
        self.assertEqual(api.delete(f'/api/template/users/{self.admin.profile.pk}/').status_code, 403)

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(STRONG_PASSWORD))
        self.assertEqual((self.admin.username, self.admin.is_active), ('sec_admin', True))
        # Бошқа майдонларни HR ўзгартира олади
        self.assertEqual(api.patch(url, {'position': 'Бош муҳандис'}, format='json').status_code, 200)

    def test_admin_still_manages_everything(self):
        api = api_as(self.admin)
        self.assertEqual(api.patch(f'/api/template/users/{self.hr.profile.pk}/', {'role': ROLE_USER}, format='json').status_code, 200)
        self.assertEqual(api.patch(f'/api/template/employees/{self.hr_employee.pk}/', {'password': 'Admin-qoydi-9', 'password_confirm': 'Admin-qoydi-9'}, format='json').status_code, 200)
        self.hr.refresh_from_db()
        self.assertTrue(self.hr.check_password('Admin-qoydi-9'))


class PasswordPolicyTests(SecurityTestCase):
    def test_login_without_password_no_longer_gets_changeme123(self):
        response = api_as(self.hr).post('/api/v1/employees/', {
            'first_name': 'Ali', 'last_name': 'Valiyev', 'tabel_number': 'NP-1', 'login': 'nopass_user',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(username='nopass_user')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password('changeme123'))

    def test_weak_passwords_are_rejected_with_uzbek_messages(self):
        api = api_as(self.admin)
        for weak in ('abc12', 'password1', '12345678', 'abcdefgh'):
            with self.subTest(password=weak):
                response = api.post('/api/template/users/', {'username': f'weak_{len(weak)}_{weak[:3]}', 'password': weak}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('password', response.json().get('errors', response.json()))

    def test_login_characters_are_validated(self):
        response = api_as(self.admin).post('/api/template/users/', {'username': 'bad"<login>', 'password': STRONG_PASSWORD}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_audit_command_finds_and_locks_old_default_passwords(self):
        victim = User.objects.create_user(username='legacy_default', password='changeme123')
        Employee.objects.create(first_name='L', last_name='D', tabel_number='LD-1', login='legacy_default', user=victim, password_hash=victim.password)
        out = StringIO()

        call_command('audit_default_passwords', '--lock', stdout=out)

        victim.refresh_from_db()
        self.assertIn('legacy_default', out.getvalue())
        self.assertFalse(victim.has_usable_password())
        self.assertEqual(Employee.objects.get(user=victim).password_hash, '')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(STRONG_PASSWORD))   # бошқаларга тегилмайди


@override_settings(EMPLOYEE_SERVICE_API_KEYS=[SERVICE_KEY])
class ServiceKeyKeepsTbIntegrationWorkingTests(SecurityTestCase):
    """TB (ppe_product) сервис калити билан /api/v1/ да ўқийди ҳам, ёзади ҳам (base/employee_service_client.py).
    Аудитда калит «фақат ўқиш» қилинган эди — TB да синхронлаш ва ходим фотолари тўхтаб қолди."""

    def test_reads_work(self):
        api = service_client()
        self.assertEqual(api.get('/api/v1/employees/', {'source_system': 'tb-project', 'no_pagination': 'true'}).status_code, 200)
        self.assertEqual(api.get(f'/api/v1/employees/{self.other_employee.slug}/').status_code, 200)
        self.assertEqual(api.get('/api/v1/departments/').status_code, 200)
        self.assertEqual(api.get('/api/v1/sections/').status_code, 200)

    def test_upsert_with_a_photo_like_tb_sync(self):
        api = service_client()
        payload = {
            'source_system': 'tb-project', 'external_id': '501', 'first_name': 'Али', 'last_name': 'Валиев',
            'tabel_number': 'TB-501', 'is_active': 'true', 'is_deleted': 'false', 'department_name': 'TB цехи',
            'metadata': '{"origin": "tb-project"}', 'base_image': photo('tb501.jpg'),
        }
        created = api.post('/api/v1/employees/upsert/', payload, format='multipart')
        self.assertEqual(created.status_code, 201, created.content)
        employee = Employee.objects.get(source_system='tb-project', external_id='501')
        self.assertTrue(employee.base_image)
        self.assertTrue(created.json()['base_image_url'])

        updated = api.post('/api/v1/employees/upsert/', {'source_system': 'tb-project', 'external_id': '501', 'tabel_number': 'TB-501', 'first_name': 'Алишер'}, format='multipart')
        self.assertEqual(updated.status_code, 200, updated.content)
        employee.refresh_from_db()
        self.assertEqual(employee.first_name, 'Алишер')

    def test_department_and_section_crud_like_tb(self):
        api = service_client()
        department = api.post('/api/v1/departments/', {'name': 'TB янги цех'}, format='json')
        self.assertEqual(department.status_code, 201, department.content)
        department_id = department.json()['id']
        self.assertEqual(api.put(f'/api/v1/departments/{department_id}/', {'name': 'TB цех (ўзгарди)'}, format='json').status_code, 200)
        section = api.post('/api/v1/sections/', {'name': 'TB бўлими', 'department_id': department_id}, format='json')
        self.assertEqual(section.status_code, 201, section.content)
        section_id = section.json()['id']
        self.assertEqual(api.put(f'/api/v1/sections/{section_id}/', {'name': 'TB бўлими 2', 'department_id': department_id}, format='json').status_code, 200)
        self.assertEqual(api.delete(f'/api/v1/sections/{section_id}/').status_code, 204)
        self.assertEqual(api.delete(f'/api/v1/departments/{department_id}/').status_code, 204)

    def test_employee_patch_and_soft_delete_like_tb(self):
        api = service_client()
        response = api.patch(f'/api/v1/employees/{self.other_employee.slug}/', {'position': 'Слесарь', 'clothe_size': '52'}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        # TB ходимнинг ҳолатини ўзи синхронлайди — администраторга боғланган карточкада ҳам.
        deactivated = api.patch(f'/api/v1/employees/{self.admin_employee.slug}/', {'is_active': 'false'}, format='multipart')
        self.assertEqual(deactivated.status_code, 200, deactivated.content)
        self.assertEqual(api.patch(f'/api/v1/employees/{self.other_employee.slug}/', {'is_deleted': 'true'}, format='multipart').status_code, 200)

    def test_tb_employee_info_form_changes_sizes(self):
        # TB «Данные сотрудника» (EditEmployeeInfo.tsx) -> TB backend -> PATCH /api/v1/employees/{slug}/ (сервис калити).
        api = service_client()
        sizes = {'special_clothing_size': '52-54/182-188', 'shoe_size': '43', 'jacket_size': '56', 'tshirt_size': 'XL'}
        form = {'department_id': self.department.pk, 'section_id': self.section.pk, 'position': 'Слесарь',
                'phone_number_1': '+998901234567', **sizes}
        for employee in (self.other_employee, self.admin_employee, self.hr_employee):
            with self.subTest(employee=employee.login):
                response = api.patch(f'/api/v1/employees/{employee.slug}/', form, format='multipart')
                self.assertEqual(response.status_code, 200, response.content)
                employee.refresh_from_db()
                self.assertEqual({key: getattr(employee, key) for key in sizes}, sizes)
                self.assertEqual(employee.section_id, self.section.pk)

    def test_tb_excel_import_changes_sizes_through_upsert(self):
        api = service_client()
        response = api.post('/api/v1/employees/upsert/', {
            'source_system': 'tb-project', 'tabel_number': self.other_employee.tabel_number, 'first_name': 'Ism', 'last_name': 'sec_other',
            'clothe_size': '50', 'shoe_size': '42', 'department_name': '1-Цех', 'section_name': 'Бўлим', 'is_active': 'true', 'is_deleted': 'false',
        }, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        self.other_employee.refresh_from_db()
        self.assertEqual((self.other_employee.clothe_size, self.other_employee.shoe_size), ('50', '42'))

    def test_integration_endpoints_keep_working(self):
        api = service_client()
        response = api.patch(f'/api/v1/employees/{self.other_employee.slug}/face-id-exemption/', {'requires_face_id_checkout': False}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(api.get('/api/v1/employees/base-image-change-logs/').status_code, 200)
        changed = api.patch(f'/api/v1/employees/{self.other_employee.slug}/base-image/', {'base_image': photo('new.jpg')}, format='multipart',
                            HTTP_X_ACTOR_USERNAME='tb_admin', HTTP_X_ACTOR_ROLE='admin')
        self.assertEqual(changed.status_code, 200, changed.content)

    def test_wrong_key_is_still_refused(self):
        api = APIClient()
        api.credentials(HTTP_X_EMPLOYEE_SERVICE_KEY='wrong-key-0123456789abcdefghijklmnop')
        self.assertIn(api.post('/api/v1/employees/upsert/', {'tabel_number': 'X-1', 'first_name': 'A', 'last_name': 'B'}).status_code, (401, 403))
        self.assertFalse(Employee.objects.filter(tabel_number='X-1').exists())


class BnpzIdFaceIdBypassTests(TestCase):
    @patch('employees.web_views.resolve_bnpzid_client_access', return_value={'allowed': True, 'requires_face_id': False, 'error': ''})
    def test_admin_with_face_id_must_pass_it_even_if_the_client_does_not_ask(self, _access):
        user, _ = make_account('bnpz_admin', ROLE_ADMIN)

        response = Client().post('/bnpzid/authorize/', {
            'username': 'bnpz_admin', 'password': STRONG_PASSWORD,
            'client_id': 'tb-project', 'redirect_uri': 'http://localhost:5175/auth/signin', 'state': 's',
        })

        # Олдин: 302 ва bnpzid_code — Face ID сўралмасдан. Энди: Face ID босқичи.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Face ID тасдиғи')
        self.assertNotIn('_auth_user_id', response.wsgi_request.session)


@override_settings(CACHES=LOCMEM_CACHE)
class BnpzIdCodeIsSingleUseTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_code_can_be_exchanged_only_once(self):
        from .bnpzid import issue_bnpzid_code

        user, _ = make_account('bnpz_once', ROLE_USER)
        client_secret = settings.BNPZID_CLIENTS['tb-project']['client_secret']
        code = issue_bnpzid_code(user=user, profile=user.profile, client_id='tb-project', redirect_uri='http://localhost:5175/auth/signin')
        payload = {'client_id': 'tb-project', 'client_secret': client_secret, 'code': code, 'redirect_uri': 'http://localhost:5175/auth/signin'}

        first = APIClient().post('/api/v1/auth/bnpzid/exchange/', payload, format='json')
        replay = APIClient().post('/api/v1/auth/bnpzid/exchange/', payload, format='json')
        wrong_secret = APIClient().post('/api/v1/auth/bnpzid/exchange/', {**payload, 'client_secret': 'x'}, format='json')

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['username'], 'bnpz_once')
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(wrong_secret.status_code, 403)


@override_settings(CACHES=LOCMEM_CACHE)
class LoginBruteForceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, _ = make_account('bf_user', ROLE_USER)

    def login(self, password, ip='10.0.0.5'):
        return Client(HTTP_X_REAL_IP=ip).post('/login/', {'username': 'bf_user', 'password': password})

    def test_after_five_wrong_passwords_even_the_right_one_is_refused_for_a_while(self):
        for _ in range(5):
            self.assertContains(self.login('wrong-password'), 'Логин ёки парол нотўғри.')

        locked = self.login(STRONG_PASSWORD)

        self.assertEqual(locked.status_code, 200)
        self.assertContains(locked, 'Жуда кўп нотўғри уриниш')
        self.assertNotIn('_auth_user_id', locked.wsgi_request.session)
        # Бошқа манзилдан эгаси кира олади (бир манзилдан ҳужум бутун ҳисобни блокламайди)
        self.assertEqual(self.login(STRONG_PASSWORD, ip='10.0.0.99').status_code, 302)

    def test_a_successful_login_resets_the_counter(self):
        for _ in range(4):
            self.login('wrong-password')
        self.assertEqual(self.login(STRONG_PASSWORD).status_code, 302)
        for _ in range(4):
            self.login('wrong-password')
        self.assertEqual(self.login(STRONG_PASSWORD).status_code, 302)


class FaceLoginAttemptLimitTests(TestCase):
    def pending_client(self, **session_values):
        user, _ = make_account('face_limit', ROLE_ADMIN)
        client = Client()
        session = client.session
        session['pending_face_auth_user_id'] = user.id
        session['pending_face_auth_backend'] = 'django.contrib.auth.backends.ModelBackend'
        session['pending_face_auth_next'] = '/'
        session.update(session_values)
        session.save()
        return client

    def verify(self, client, similarity):
        with patch('employees.views.decode_image_to_pil', return_value=Image.new('RGB', (32, 32))):
            with patch('employees.views.calculate_face_similarity', return_value=similarity):
                return client.post('/api/v1/auth/login-face-verify/', data={'captured_images': ['data:image/jpeg;base64,eA==']}, content_type='application/json')

    def test_five_failed_attempts_end_the_face_step(self):
        client = self.pending_client()
        for _ in range(5):
            self.assertFalse(self.verify(client, 10.0).json()['verified'])

        stopped = self.verify(client, 99.0)   # тўғри юз ҳам энди қабул қилинмайди

        self.assertEqual(stopped.status_code, 429)
        self.assertNotIn('pending_face_auth_user_id', client.session)
        self.assertNotIn('_auth_user_id', client.session)

    def test_the_face_step_expires(self):
        client = self.pending_client(pending_face_auth_started_at=1, pending_face_auth_attempts=0)
        self.assertEqual(self.verify(client, 99.0).status_code, 429)

    def test_a_good_face_within_the_limit_logs_in(self):
        client = self.pending_client()
        self.verify(client, 10.0)
        response = self.verify(client, 99.0)
        self.assertTrue(response.json()['verified'])
        self.assertIn('_auth_user_id', client.session)


@override_settings(CACHES=LOCMEM_CACHE, EMPLOYEE_SERVICE_API_KEYS=[SERVICE_KEY])
class FaceThrottleTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_face_detection_is_rate_limited_for_users_but_not_for_the_service_key(self):
        rates = {'face': '3/min', 'bnpzid': '60/min'}
        with patch.object(ScopedThrottleExceptServiceKey, 'THROTTLE_RATES', rates):
            api = api_as(self.plain)
            codes = [api.post('/api/v1/face/detect-boxes/', {}, format='json').status_code for _ in range(4)]
            service = [service_client().post('/api/v1/face/detect-boxes/', {}, format='json').status_code for _ in range(4)]

        self.assertEqual(codes, [400, 400, 400, 429])
        self.assertNotIn(429, service)


@override_settings(EMPLOYEE_SERVICE_API_KEYS=[SERVICE_KEY])
class MediaIsOpenByDefaultForTbTests(SecurityTestCase):
    """TB фотони <img> да тўғридан-тўғри https://<host>:5000/media/... дан, media-proxy эса калитсиз олади."""

    def url(self, employee):
        return '/' + employee.base_image.url.lstrip('/')

    def test_default_setting_is_open(self):
        from config import settings as project_settings
        self.assertFalse(project_settings.MEDIA_REQUIRE_AUTH)

    @override_settings(MEDIA_REQUIRE_AUTH=False)
    def test_tb_media_proxy_without_headers_gets_the_photo(self):
        response = Client().get(self.url(self.other_employee))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertTrue(b''.join(response.streaming_content))

    @override_settings(MEDIA_REQUIRE_AUTH=False)
    def test_open_media_still_refuses_path_traversal(self):
        for path in ('/media/../config/settings.py', '/media/%2e%2e/config/settings.py', '/media//etc/passwd'):
            with self.subTest(path=path):
                self.assertEqual(Client().get(path).status_code, 404)


@override_settings(EMPLOYEE_SERVICE_API_KEYS=[SERVICE_KEY], MEDIA_REQUIRE_AUTH=True)
class ProtectedMediaTests(SecurityTestCase):
    """MEDIA_REQUIRE_AUTH=true (TB калит юборишга ўтгандан кейин): эталон фотолар фақат ваколатлиларга."""

    def url(self, employee):
        return '/' + employee.base_image.url.lstrip('/')

    def test_anonymous_gets_nothing(self):
        self.assertEqual(Client().get(self.url(self.admin_employee)).status_code, 404)

    def test_plain_user_sees_only_his_own_photo(self):
        client = Client()
        client.force_login(self.plain)
        own = client.get(self.url(self.plain_employee))
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own['Content-Type'], 'image/jpeg')
        self.assertEqual(own['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(client.get(self.url(self.admin_employee)).status_code, 404)

    def test_hr_and_the_service_key_see_all_photos(self):
        client = Client()
        client.force_login(self.hr)
        self.assertEqual(client.get(self.url(self.admin_employee)).status_code, 200)
        self.assertEqual(Client(HTTP_X_EMPLOYEE_SERVICE_KEY=SERVICE_KEY).get(self.url(self.other_employee)).status_code, 200)

    def test_path_traversal_is_refused(self):
        client = Client()
        client.force_login(self.admin)
        for path in ('/media/../config/settings.py', '/media/%2e%2e/config/settings.py', '/media//etc/passwd'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)



class AdminLoginGoesThroughTheMainLoginTests(TestCase):
    def test_admin_login_redirects_to_the_main_login(self):
        response = Client().get('/admin/login/?next=/admin/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/login/?next='), response['Location'])

    def test_logged_in_non_staff_user_is_not_bounced_in_a_loop(self):
        user, _ = make_account('loop_user', ROLE_USER)
        client = Client()
        client.force_login(user)
        response = client.get('/admin/login/')
        self.assertEqual((response.status_code, response['Location']), (302, '/'))


class XssEscapingTemplatesTests(TestCase):
    """Номлар (цех, бўлим, ходим, лавозим, логин) HTML га экранланиб қўйилади."""

    def read(self, name):
        return (settings.BASE_DIR / 'employees' / 'templates' / name).read_text(encoding='utf-8')

    def test_raw_names_are_no_longer_inserted_into_html(self):
        departments = self.read('departments.html')
        employees = self.read('employees_list.html')
        users = self.read('users.html')
        access = self.read('user_access.html')

        self.assertIn('${esc(department.name)}', departments)
        self.assertNotIn('>${department.name}<', departments)
        self.assertIn('${escapeHtml(employee.position', employees)
        self.assertNotIn("onclick=\"openImageModal('${", employees)
        self.assertIn('data-username="${escapeHtml(u.username)}"', users)
        self.assertNotIn("'${escapeHtml(u.username)}'", users)
        self.assertIn('id="row-${escapeHtml(emp.login)}"', access)
        for html in (users, access):
            self.assertIn("'\"': '&quot;'", html)   # қўштирноқ ҳам экранланади (атрибут учун)


class ExcelFormulaInjectionTests(SecurityTestCase):
    def test_a_name_that_looks_like_a_formula_is_stored_as_text(self):
        self.plain_employee.last_name = '=HYPERLINK("http://evil","x")'
        self.plain_employee.department = self.department
        self.plain_employee.save()

        sheet = openpyxl.load_workbook(BytesIO(build_staff_workbook(staff_export_groups()))).active
        cells = [cell for row in sheet.iter_rows() for cell in row if isinstance(cell.value, str) and 'HYPERLINK' in cell.value]

        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].data_type, 's')


class ProductionSettingsTests(TestCase):
    """DEBUG ўчирилганда маълум (репозиторийдаги) калитлар билан сервис ишга тушмайди."""

    def load_settings(self, **env):
        base = {key: value for key, value in os.environ.items() if not key.startswith(('EMPLOYEE_SERVICE_', 'SECRET_KEY', 'BNPZID', 'DJANGO_SETTINGS'))}
        base.update({
            'EMPLOYEE_SERVICE_DB_ENGINE': 'django.db.backends.postgresql',
            'EMPLOYEE_SERVICE_DB_NAME': 'unused',
            'EMPLOYEE_SERVICE_DEBUG': 'false',
            'SECRET_KEY': 'k' * 20 + 'Q9xLm2Vb7Rt4Wz8Np3Hs6Jd1Fg5Ky0Ca' + 'z' * 10,
            'EMPLOYEE_SERVICE_API_KEYS': 'Tq8nV3mZ1xR7pL5wK9sD2fH6gJ4bC0aYuE',
            'BNPZID_CLIENTS': '{}',
        })
        base.update(env)
        return subprocess.run(
            [sys.executable, '-c', 'import config.settings as s; print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CORS_ALLOW_ALL_ORIGINS, s.EMPLOYEE_SERVICE_API_KEYS == [], s.BNPZID_CLIENTS)'],
            cwd=settings.BASE_DIR, env=base, capture_output=True, text=True, encoding='utf-8',
        )

    def test_good_production_values_load_with_secure_defaults(self):
        result = self.load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['False', 'True', 'False', 'False', '{}'])

    def test_placeholder_secret_key_is_refused(self):
        for value in ('', 'your-secret-key-here-change-me-0123456789', 'short'):
            with self.subTest(value=value):
                result = self.load_settings(SECRET_KEY=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SECRET_KEY', result.stderr)

    def test_the_published_dev_api_key_and_bnpzid_secret_are_refused(self):
        self.assertIn('EMPLOYEE_SERVICE_API_KEYS', self.load_settings(EMPLOYEE_SERVICE_API_KEYS='dev-employee-service-key').stderr)
        bad_client = '{"tb-project": {"client_secret": "dev-bnpzid-secret"}}'
        self.assertIn('client_secret', self.load_settings(BNPZID_CLIENTS=bad_client).stderr)
