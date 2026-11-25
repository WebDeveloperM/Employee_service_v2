"""Табелчи API си (/api/v1/timekeeper/): бошқа тизим табелчи номидан ўз цехи ходимларини ўқийди ва янгилайди.

Ҳужжат: API_TIMEKEEPER.md (охирги тест ҳужжат кодга мос эканини текширади).
"""
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.conf import settings as django_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from . import timekeeper_api
from .models import (
    Department, Employee, EmployeeCategory, EmployeeReplacement, PositionCategory, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section,
    Timekeeper, TimekeeperApiToken, WorkSchedule,
)
from .test_timekeepers import PASSWORD, TimekeeperTestBase, make_employee, make_user

BASE = '/api/v1/timekeeper'
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'timekeeper-api-tests'}}


class ApiTestBase(TimekeeperTestBase):
    def setUp(self):
        super().setUp()
        self.make_timekeeper()          # Али (101) — 1-цех табелчиси, логини «ali_login»
        self.api = APIClient()
        self.dept_a_section = Section.objects.create(department=self.dept_a, name='Юридик бўлим', code='1')
        self.dept_b_section = Section.objects.create(department=self.dept_b, name='Бошқа цех бўлими', code='1')

    def fetch_token(self, login='ali_login', password=PASSWORD):
        response = APIClient().post(f'{BASE}/token/', {'login': login, 'password': password}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['token']

    def authed(self, token=None):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token or self.fetch_token()}')
        return client


class TokenIssueTests(ApiTestBase):
    def test_a_working_timekeeper_gets_a_token(self):
        response = self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': PASSWORD}, format='json')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['token'].startswith('tk_'))
        self.assertEqual(data['token_type'], 'Token')
        self.assertAlmostEqual(data['expires_in'], 24 * 3600, delta=5)
        self.assertEqual(data['timekeeper']['tabel_number'], '101')
        self.assertEqual(data['timekeeper']['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})

    def test_the_token_itself_is_never_stored_only_its_hash(self):
        token = self.fetch_token()

        row = TimekeeperApiToken.objects.get()
        self.assertEqual(row.key_hash, timekeeper_api.hash_token(token))
        self.assertNotIn(token, [row.key_hash, row.auth_stamp])
        self.assertEqual(len(row.key_hash), 64)

    def test_wrong_password_and_unknown_login_look_exactly_the_same(self):
        wrong = self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': 'nope'}, format='json')
        unknown = self.api.post(f'{BASE}/token/', {'login': 'nobody', 'password': 'nope'}, format='json')

        self.assertEqual((wrong.status_code, unknown.status_code), (401, 401))
        self.assertEqual(wrong.json(), unknown.json())
        self.assertEqual(wrong.json()['code'], 'invalid_credentials')

    def test_only_working_timekeepers_get_tokens(self):
        make_user('plain_login', ROLE_USER, employee=self.bobur)
        for login in ('tk_admin', 'tk_hr', 'plain_login'):
            with self.subTest(login=login):
                response = self.api.post(f'{BASE}/token/', {'login': login, 'password': PASSWORD}, format='json')
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['code'], 'not_a_timekeeper')
        self.assertFalse(TimekeeperApiToken.objects.exists())

    def test_a_removed_timekeeper_gets_no_new_token(self):
        Timekeeper.objects.all().delete()

        response = self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': PASSWORD}, format='json')

        self.assertEqual((response.status_code, response.json()['code']), (403, 'not_a_timekeeper'))

    def test_a_disabled_account_is_refused(self):
        User.objects.filter(username='ali_login').update(is_active=False)

        response = self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': PASSWORD}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_missing_fields_are_a_400_with_field_messages(self):
        response = self.api.post(f'{BASE}/token/', {'login': 'ali_login'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.json())

    def test_a_junk_authorization_header_does_not_break_the_login_call(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token junk')

        self.assertEqual(client.post(f'{BASE}/token/', {'login': 'ali_login', 'password': PASSWORD}, format='json').status_code, 200)

    @override_settings(CACHES=LOCMEM_CACHE)
    def test_repeated_failures_lock_the_login_even_for_the_right_password(self):
        cache.clear()
        for _ in range(django_settings.LOGIN_FAILURE_LIMIT):
            self.assertEqual(self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': 'bad'}, format='json').status_code, 401)

        locked = self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': PASSWORD}, format='json')

        self.assertEqual((locked.status_code, locked.json()['code']), (429, 'login_locked'))
        self.assertIn('Retry-After', locked)
        cache.clear()

    @override_settings(CACHES=LOCMEM_CACHE)
    def test_the_token_endpoint_is_rate_limited_per_client(self):
        cache.clear()
        rates = {**ScopedRateThrottle.THROTTLE_RATES, 'timekeeper_token': '2/min'}
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', rates):
            codes = [self.api.post(f'{BASE}/token/', {'login': 'ali_login', 'password': PASSWORD}, format='json').status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        cache.clear()

    def test_a_user_keeps_at_most_the_configured_number_of_tokens(self):
        with override_settings(TIMEKEEPER_API_MAX_TOKENS=3):
            tokens = [self.fetch_token() for _ in range(5)]

        self.assertEqual(TimekeeperApiToken.objects.count(), 3)
        self.assertEqual(self.authed(tokens[-1]).get(f'{BASE}/me/').status_code, 200)
        self.assertEqual(self.authed(tokens[0]).get(f'{BASE}/me/').status_code, 401)


class TokenUseTests(ApiTestBase):
    def test_no_token_is_a_401_with_a_challenge_header(self):
        response = self.api.get(f'{BASE}/employees/')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Token')
        self.assertEqual(response.json()['code'], 'not_authenticated')

    def test_bad_and_malformed_tokens_are_refused_with_a_code(self):
        for header, code in (('Token tk_wrong', 'token_invalid'), ('Token', 'bad_header'), ('Token a b', 'bad_header')):
            with self.subTest(header=header):
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=header)
                response = client.get(f'{BASE}/me/')
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['code'], code)

    def test_an_expired_token_says_so(self):
        client = self.authed()
        TimekeeperApiToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        response = client.get(f'{BASE}/me/')

        self.assertEqual((response.status_code, response.json()['code']), (401, 'token_expired'))

    def test_changing_the_password_kills_every_token(self):
        client = self.authed()
        self.assertEqual(client.get(f'{BASE}/me/').status_code, 200)
        user = User.objects.get(username='ali_login')
        user.set_password('Yangi-parol-2026')
        user.save()

        response = client.get(f'{BASE}/me/')

        self.assertEqual((response.status_code, response.json()['code']), (401, 'password_changed'))

    def test_a_disabled_account_stops_working_at_once(self):
        client = self.authed()
        User.objects.filter(username='ali_login').update(is_active=False)

        self.assertEqual(client.get(f'{BASE}/me/').json()['code'], 'account_disabled')

    def test_logout_revokes_the_token(self):
        token = self.fetch_token()
        client = self.authed(token)

        self.assertEqual(client.post(f'{BASE}/logout/').status_code, 204)

        self.assertEqual(client.get(f'{BASE}/me/').status_code, 401)
        self.assertFalse(TimekeeperApiToken.objects.exists())

    def test_a_token_stops_working_the_moment_the_timekeeper_is_removed(self):
        client = self.authed()
        self.assertEqual(client.get(f'{BASE}/employees/').status_code, 200)
        Timekeeper.objects.all().delete()

        response = client.get(f'{BASE}/employees/')

        self.assertEqual((response.status_code, response.json()['code']), (403, 'not_a_timekeeper'))

    def test_a_timekeeper_who_is_no_longer_active_loses_access(self):
        client = self.authed()
        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])

        self.assertEqual(client.get(f'{BASE}/employees/').status_code, 403)

    def test_last_used_is_recorded(self):
        client = self.authed()
        self.assertIsNone(TimekeeperApiToken.objects.get().last_used_at)

        client.get(f'{BASE}/me/')

        self.assertIsNotNone(TimekeeperApiToken.objects.get().last_used_at)

    @override_settings(EMPLOYEE_SERVICE_API_KEYS=['svc-key-for-tests-0123456789abcdefghijklmnop'])
    def test_the_service_key_and_a_web_session_do_not_work_here(self):
        service = APIClient()
        service.credentials(HTTP_X_EMPLOYEE_SERVICE_KEY='svc-key-for-tests-0123456789abcdefghijklmnop')
        bearer = APIClient()
        bearer.credentials(HTTP_AUTHORIZATION='Bearer svc-key-for-tests-0123456789abcdefghijklmnop')
        session = APIClient()
        session.force_login(User.objects.get(username='ali_login'))
        for name, client in (('service key', service), ('bearer service key', bearer), ('web session', session)):
            with self.subTest(client=name):
                self.assertEqual(client.get(f'{BASE}/employees/').status_code, 401)

    def test_a_timekeeper_token_opens_nothing_outside_this_api(self):
        client = self.authed()

        for url in ('/api/v1/employees/', '/api/template/employees/'):
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (401, 403))

    def test_me_describes_the_timekeeper_and_the_token(self):
        data = self.authed().get(f'{BASE}/me/').json()

        self.assertEqual(data['timekeeper']['login'], 'ali_login')
        self.assertEqual(data['timekeeper']['department']['name'], self.dept_a.name)
        self.assertIn('expires_at', data['token'])


class ReferenceTests(ApiTestBase):
    def test_reference_lists_only_the_own_departments_sections_and_all_the_dictionaries(self):
        PositionCategory.objects.get_or_create(name='Тест лавозим тоифаси')
        EmployeeCategory.objects.get_or_create(name='Тест ходим тоифаси')
        WorkSchedule.objects.get_or_create(name='Тест иш тартиби')
        child = Section.objects.create(department=self.dept_a, name='Ички участка', code='1', parent=self.dept_a_section)

        data = self.authed().get(f'{BASE}/reference/').json()

        self.assertEqual({item['id'] for item in data['sections']}, {self.dept_a_section.id, child.id})
        self.assertEqual([item['path'] for item in data['sections']], ['Юридик бўлим', 'Юридик бўлим / Ички участка'])
        self.assertEqual([item['id'] for item in data['position_categories']], list(PositionCategory.objects.values_list('id', flat=True)))
        self.assertEqual([item['id'] for item in data['employee_categories']], list(EmployeeCategory.objects.values_list('id', flat=True)))
        self.assertEqual([item['id'] for item in data['work_schedules']], list(WorkSchedule.objects.values_list('id', flat=True)))
        self.assertIn('Тест иш тартиби', [item['name'] for item in data['work_schedules']])
        self.assertEqual([item['value'] for item in data['statuses']], ['working', 'vacation', 'business_trip', 'sick_leave', 'childcare_leave', 'over_staff', 'inactive'])
        self.assertEqual([item['value'] for item in data['genders']], ['M', 'F'])


class EmployeeReadTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.client = self.authed()

    def tabels(self, response):
        return [item['tabel_number'] for item in response.json()['results']]

    def test_the_list_holds_only_the_own_department_without_deleted_employees(self):
        make_employee(103, self.dept_a, is_deleted=True)
        make_employee(104, self.dept_a)

        response = self.client.get(f'{BASE}/employees/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 3)
        self.assertEqual(sorted(self.tabels(response)), ['101', '102', '104'])
        self.assertNotIn('201', self.tabels(response))      # бошқа цех ходими

    def test_the_list_cannot_be_widened_by_filters(self):
        for query in (f'?department_id={self.dept_b.id}', '?search=Сардор', '?tabel_number=201', '?page_size=200'):
            with self.subTest(query=query):
                self.assertNotIn('201', self.tabels(self.client.get(f'{BASE}/employees/{query}')))

    def test_pagination(self):
        for number in range(110, 125):
            make_employee(number, self.dept_a)

        first = self.client.get(f'{BASE}/employees/?page_size=10').json()
        second = self.client.get(first['next']).json()

        self.assertEqual((first['count'], len(first['results']), len(second['results'])), (17, 10, 7))
        self.assertEqual(len({*self.tabels_of(first), *self.tabels_of(second)}), 17)
        self.assertEqual(self.client.get(f'{BASE}/employees/?page_size=1000').json()['count'], 17)

    def tabels_of(self, payload):
        return [item['tabel_number'] for item in payload['results']]

    def test_search_tabel_status_and_updated_since_filters(self):
        self.bobur.work_status = 'vacation'
        self.bobur.save(update_fields=['work_status'])

        self.assertEqual(self.tabels(self.client.get(f'{BASE}/employees/?search=Бобур')), ['102'])
        self.assertEqual(self.tabels(self.client.get(f'{BASE}/employees/?tabel_number=101')), ['101'])
        self.assertEqual(self.tabels(self.client.get(f'{BASE}/employees/?status=vacation')), ['102'])
        self.assertEqual(self.tabels(self.client.get(f'{BASE}/employees/?status=working')), ['101'])
        self.assertEqual(self.tabels(self.client.get(f'{BASE}/employees/?status=inactive')), [])
        future = (timezone.now() + timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
        self.assertEqual(self.tabels(self.client.get(f'{BASE}/employees/', {'updated_since': future})), [])
        past = (timezone.now() - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
        self.assertEqual(len(self.tabels(self.client.get(f'{BASE}/employees/', {'updated_since': past}))), 2)

    def test_bad_filters_are_a_400_with_the_field_name(self):
        self.assertIn('status', self.client.get(f'{BASE}/employees/?status=sleeping').json())
        self.assertIn('updated_since', self.client.get(f'{BASE}/employees/?updated_since=yesterday').json())

    def test_the_number_of_queries_does_not_grow_with_the_page(self):
        def queries():
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.client.get(f'{BASE}/employees/?page_size=100').status_code, 200)
            return len(context.captured_queries)

        self.client.get(f'{BASE}/me/')      # исиш: токеннинг «охирги ишлатилган» вақти биринчи сўровда ёзилади
        for number in range(110, 113):
            make_employee(number, self.dept_a, section=self.dept_a_section)
        few = queries()
        for number in range(120, 140):
            make_employee(number, self.dept_a, section=self.dept_a_section)
        self.assertEqual(queries(), few)

    def test_an_employee_is_read_by_tabel_number_in_a_compact_shape(self):
        self.bobur.section = self.dept_a_section
        self.bobur.grade = '7'
        self.bobur.save()

        data = self.client.get(f'{BASE}/employees/102/').json()

        self.assertEqual(data['tabel_number'], '102')
        self.assertEqual(data['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual(data['section'], {'id': self.dept_a_section.id, 'code': '1', 'name': 'Юридик бўлим', 'path': 'Юридик бўлим'})
        self.assertEqual((data['status'], data['status_label'], data['is_active']), ('working', 'Фаол', True))
        self.assertIsNone(data['replacement'])
        self.assertEqual(data['phone_number_1'], '+998901112233')

    def test_nothing_private_or_internal_is_in_the_response(self):
        data = self.client.get(f'{BASE}/employees/101/').json()    # Али ҳисоби бор

        for name in ('login', 'password', 'password_hash', 'metadata', 'source_system', 'external_id', 'role', 'requires_face_id_checkout', 'is_deleted'):
            self.assertNotIn(name, data)

    def test_other_departments_deleted_and_unknown_employees_are_all_just_not_found(self):
        make_employee(105, self.dept_a, is_deleted=True)
        for tabel in ('201', '105', '99999'):
            with self.subTest(tabel=tabel):
                response = self.client.get(f'{BASE}/employees/{tabel}/')
                self.assertEqual((response.status_code, response.json()['code']), (404, 'not_found'))


class EmployeeUpdateTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.client = self.authed()
        self.url = f'{BASE}/employees/102/'

    def patch(self, data, url=None, **kwargs):
        return self.client.patch(url or self.url, data, format='json', **kwargs)

    def test_only_the_sent_fields_change_and_the_response_shows_the_result(self):
        before = Employee.objects.get(pk=self.bobur.pk)

        response = self.patch({
            'first_name': 'Бахтиёр', 'phone_number_1': '+998 90 555 44 33', 'position': 'Бош муҳандис', 'grade': '04*',
            'date_of_birth': '30.12.2002', 'date_of_employment': '2020-05-01', 'date_of_change_position': '15/01/2024',
            'section_id': self.dept_a_section.id, 'gender': 'M', 'shoe_size': '42',
        })

        self.assertEqual(response.status_code, 200, response.content)
        employee = Employee.objects.get(pk=self.bobur.pk)
        self.assertEqual((employee.first_name, employee.position, employee.grade, employee.shoe_size), ('Бахтиёр', 'Бош муҳандис', '4*', '42'))
        self.assertEqual(employee.date_of_birth.isoformat(), '2002-12-30')
        self.assertEqual(employee.date_of_employment.isoformat(), '2020-05-01')
        self.assertEqual(employee.date_of_change_position.isoformat(), '2024-01-15')
        self.assertEqual(employee.section_id, self.dept_a_section.id)
        self.assertEqual((employee.last_name, employee.tabel_number, employee.department_id), (before.last_name, '102', self.dept_a.id))
        data = response.json()
        self.assertEqual((data['first_name'], data['grade'], data['date_of_birth'], data['section']['name']), ('Бахтиёр', '4*', '30/12/2002', 'Юридик бўлим'))

    def test_a_field_can_be_cleared(self):
        self.bobur.section = self.dept_a_section
        self.bobur.date_of_birth = '2000-01-01'
        self.bobur.grade = '5'
        self.bobur.save()

        response = self.patch({'section_id': None, 'date_of_birth': None, 'grade': '', 'phone_number_2': ''})

        self.assertEqual(response.status_code, 200, response.content)
        employee = Employee.objects.get(pk=self.bobur.pk)
        self.assertEqual((employee.section_id, employee.date_of_birth, employee.grade), (None, None, None))

    def test_a_form_encoded_request_works_too(self):
        response = self.client.patch(self.url, {'position': 'Электрик', 'phone_number_1': '+998900000000'}, format='multipart')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Employee.objects.get(pk=self.bobur.pk).position, 'Электрик')

    def test_vacation_with_a_substitute_and_back_to_work(self):
        response = self.patch({'status': 'vacation', 'replacement_tabel_number': '101'})

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual((data['status'], data['status_label']), ('vacation', 'Меҳнат таътилида'))
        self.assertEqual(data['replacement'], {'id': self.ali.id, 'tabel_number': '101', 'full_name': str(self.ali)})
        self.assertEqual(EmployeeReplacement.objects.get().substitute_id, self.ali.id)

        back = self.patch({'status': 'working'}).json()

        self.assertEqual((back['status'], back['replacement']), ('working', None))
        self.assertFalse(EmployeeReplacement.objects.exists())

    def test_the_substitute_can_be_removed_without_changing_the_status(self):
        self.patch({'status': 'sick_leave', 'replacement_tabel_number': '101'})

        data = self.patch({'replacement_tabel_number': None}).json()

        self.assertEqual((data['status'], data['replacement']), ('sick_leave', None))

    def test_inactive_status_deactivates_the_employee(self):
        data = self.patch({'status': 'inactive'}).json()

        self.assertEqual((data['status'], data['is_active']), ('inactive', False))
        employee = Employee.objects.get(pk=self.bobur.pk)
        self.assertEqual((employee.is_active, employee.work_status), (False, None))

    def test_deactivating_an_employee_with_an_account_also_disables_the_account(self):
        user = make_user('bobur_login', ROLE_USER, employee=self.bobur)

        self.patch({'status': 'inactive'})

        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_the_documented_error_examples_are_what_the_api_really_says(self):
        """API_TIMEKEEPER.md 7.4: шу матнлар ҳужжатда мисол сифатида келтирилган."""
        docs = DocumentationMatchesTheCodeTests.docs()
        cross = self.patch({'section_id': self.dept_b_section.id}).json()
        self.assertEqual(cross, {'section_id': ['Бўлим танланган цехга тегишли эмас.']})
        self.assertIn(cross['section_id'][0], docs)
        away = self.patch({'status': 'vacation', 'replacement_tabel_number': '201'}).json()
        self.assertIn('replacement_tabel_number', away)
        self.ali.work_status = 'vacation'
        self.ali.save(update_fields=['work_status'])
        busy = self.patch({'status': 'vacation', 'replacement_tabel_number': '101'}).json()
        self.assertIn(busy['replacement_tabel_number'][0], docs)
        for message in (timekeeper_api.REJECTED_HINTS['tabel_number'], timekeeper_api.REJECTED_HINTS['login']):
            self.assertIn(message, docs)
        grade = self.patch({'grade': '123*'}).json()['grade'][0]
        self.assertIn(grade, docs)

    def test_substitute_rules_are_enforced(self):
        # бошқа цех ходими
        other = self.patch({'status': 'vacation', 'replacement_tabel_number': '201'})
        self.assertEqual(other.status_code, 400)
        self.assertIn('replacement_tabel_number', other.json())
        # ходимнинг ўзи
        own = self.patch({'status': 'vacation', 'replacement_tabel_number': '102'})
        self.assertEqual(own.status_code, 400)
        self.assertIn('replacement_tabel_number', own.json())
        # ўзи таътилда бўлган ходим ўринбосар бўла олмайди
        self.ali.work_status = 'vacation'
        self.ali.save(update_fields=['work_status'])
        away = self.patch({'status': 'vacation', 'replacement_tabel_number': '101'})
        self.assertEqual(away.status_code, 400)
        self.assertFalse(EmployeeReplacement.objects.exists())
        self.assertEqual(Employee.objects.get(pk=self.bobur.pk).work_status, None)    # хато бўлса, ҳеч нарса ўзгармайди

    def test_an_unknown_status_is_refused(self):
        response = self.patch({'status': 'sleeping'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.json())

    def test_everything_that_is_not_allowed_is_refused_with_a_reason_and_nothing_is_saved(self):
        forbidden = {
            'tabel_number': '999', 'department_id': self.dept_b.id, 'department': self.dept_b.id, 'login': 'hacker', 'password': 'Aa1234567',
            'password_confirm': 'Aa1234567', 'role': 'admin', 'base_image': 'x', 'is_active': False, 'work_status': 'vacation',
            'replacement_employee_id': 1, 'source_system': 'x', 'external_id': '1', 'metadata': {}, 'is_deleted': True,
            'requires_face_id_checkout': False, 'slug': 'x', 'whatever': 1,
        }
        before = Employee.objects.filter(pk=self.bobur.pk).values().get()

        for name, value in forbidden.items():
            with self.subTest(field=name):
                response = self.patch({name: value, 'position': 'Ўзгармаслиги керак'})
                self.assertEqual(response.status_code, 400, name)
                self.assertIn(name, response.json())
        after = Employee.objects.filter(pk=self.bobur.pk).values().get()
        self.assertEqual(after, before)

    def test_every_refused_field_has_an_explanation_not_just_the_generic_one(self):
        for name in ('tabel_number', 'department_id', 'is_active', 'work_status', 'replacement_employee_id', 'base_image', 'login', 'password', 'role'):
            with self.subTest(field=name):
                message = self.patch({name: 'x'}).json()[name][0]
                self.assertNotEqual(message, timekeeper_api.UNKNOWN_FIELD_MESSAGE)

    def test_empty_and_non_object_bodies_are_refused(self):
        empty = self.patch({})
        self.assertEqual((empty.status_code, empty.json()['code']), (400, 'empty_update'))
        array = self.client.patch(self.url, ['position'], format='json')
        self.assertEqual((array.status_code, array.json()['code']), (400, 'parse_error'))

    def test_an_employee_of_another_department_cannot_be_touched_or_even_seen(self):
        before = Employee.objects.get(pk=self.sardor.pk).first_name

        response = self.patch({'first_name': 'Бузилди'}, url=f'{BASE}/employees/201/')

        self.assertEqual((response.status_code, response.json()['code']), (404, 'not_found'))
        self.assertEqual(Employee.objects.get(pk=self.sardor.pk).first_name, before)

    def test_a_section_of_another_department_is_refused(self):
        response = self.patch({'section_id': self.dept_b_section.id})

        self.assertEqual(response.status_code, 400)
        self.assertIn('section_id', response.json())
        self.assertIsNone(Employee.objects.get(pk=self.bobur.pk).section_id)

    def test_invalid_values_come_back_as_field_errors(self):
        for field, value in (('date_of_birth', '31-31-2020'), ('grade', '123*'), ('gender', 'X'), ('position_category_id', 99999)):
            with self.subTest(field=field):
                response = self.patch({field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())

    def test_an_administrators_or_hr_employee_is_protected(self):
        protected = make_employee(103, self.dept_a, first_name='Админ')
        make_user('admin_employee', ROLE_ADMIN, employee=protected)

        response = self.patch({'position': 'Бошқа'}, url=f'{BASE}/employees/103/')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Employee.objects.get(pk=protected.pk).position, 'Фаррош')

    def test_only_get_and_patch_exist(self):
        self.assertEqual(self.client.put(self.url, {'position': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)
        self.assertEqual(self.client.post(f'{BASE}/employees/', {'tabel_number': '1'}, format='json').status_code, 405)

    def test_updates_are_logged_with_field_names_but_not_their_values(self):
        with self.assertLogs('employees.security', level='INFO') as logs:
            self.patch({'phone_number_1': '+998905554433', 'position': 'Бош муҳандис'})

        line = next(entry for entry in logs.output if 'timekeeper api update' in entry)
        self.assertIn("'ali_login'", line)
        self.assertIn("'102'", line)
        self.assertIn('phone_number_1', line)
        self.assertNotIn('998905554433', line)
        self.assertNotIn('Бош муҳандис', line)


class DocumentationMatchesTheCodeTests(ApiTestBase):
    """API_TIMEKEEPER.md кодга мос: ҳар бир манзил, ёзиладиган майдон, ҳолат ва хато коди ҳужжатда бор."""

    @classmethod
    def docs(cls):
        return (django_settings.BASE_DIR / 'API_TIMEKEEPER.md').read_text(encoding='utf-8')

    def test_every_route_is_documented(self):
        docs = self.docs()
        for route in ('/api/v1/timekeeper/token/', '/api/v1/timekeeper/logout/', '/api/v1/timekeeper/me/', '/api/v1/timekeeper/reference/',
                      '/api/v1/timekeeper/employees/', '/api/v1/timekeeper/employees/{tabel_number}/'):
            self.assertIn(route, docs)

    def test_every_writable_field_and_every_refused_field_is_documented(self):
        docs = self.docs()
        for name in sorted(timekeeper_api.WRITABLE_FIELDS | set(timekeeper_api.REJECTED_HINTS)):
            self.assertIn(f'`{name}`', docs, name)

    def test_every_status_and_error_code_is_documented(self):
        docs = self.docs()
        for value in timekeeper_api.STATUS_LABELS:
            self.assertIn(f'`{value}`', docs, value)
        source = (django_settings.BASE_DIR / 'employees' / 'timekeeper_api.py').read_text(encoding='utf-8')
        codes = set(re.findall(r"code='([a-z_]+)'", source)) | set(re.findall(r"'code': '([a-z_]+)'", source))
        self.assertTrue(codes)
        for code in sorted(codes):
            self.assertIn(f'`{code}`', docs, code)
