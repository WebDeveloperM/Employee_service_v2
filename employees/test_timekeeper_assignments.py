"""Табелчи тайинлаш API си (/api/v1/timekeeper-assignments/): табелчини бошқа тизим тайинлайди, ES да тайинлаш саҳифаси йўқ.

Фақат сервис калити. Ҳужжат: API_TIMEKEEPER.md.
"""
import re

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from . import timekeepers
from .models import Department, Employee, ROLE_ADMIN, ROLE_HR, ROLE_TIMEKEEPER, ROLE_USER, Timekeeper, UserProfile
from .test_timekeepers import PASSWORD, TimekeeperTestBase, client_for, make_employee, make_user

SERVICE_KEY = 'svc-key-for-tests-0123456789abcdefghijklmnop'
BASE = '/api/v1/timekeeper-assignments'


def key_client(key=SERVICE_KEY):
    client = APIClient()
    client.credentials(HTTP_X_EMPLOYEE_SERVICE_KEY=key)
    return client


@override_settings(EMPLOYEE_SERVICE_API_KEYS=[SERVICE_KEY])
class AssignmentApiBase(TimekeeperTestBase):
    def setUp(self):
        super().setUp()
        self.api = key_client()

    def put(self, department, tabel, **extra):
        return self.api.put(f'{BASE}/{department.id}/', {'tabel_number': tabel, **extra}, format='json')

    def role_of(self, username):
        return User.objects.get(username=username).profile.role


class AccessTests(AssignmentApiBase):
    URLS = (('get', BASE + '/'), ('get', BASE + '/{dept}/'), ('put', BASE + '/{dept}/'), ('delete', BASE + '/{dept}/'))

    def test_without_a_key_everything_is_401_with_a_challenge(self):
        for method, url in self.URLS:
            with self.subTest(method=method, url=url):
                response = getattr(APIClient(), method)(url.format(dept=self.dept_a.id), {'tabel_number': '101'}, format='json')
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="employee-service"')
                self.assertEqual(response.json()['code'], 'not_authenticated')
        self.assertFalse(Timekeeper.objects.exists())

    def test_a_wrong_key_is_401(self):
        for method, url in self.URLS:
            with self.subTest(method=method):
                response = getattr(key_client('wrong-key'), method)(url.format(dept=self.dept_a.id), {'tabel_number': '101'}, format='json')
                self.assertEqual((response.status_code, response.json()['code']), (401, 'authentication_failed'))
        self.assertFalse(Timekeeper.objects.exists())

    def test_the_key_also_works_as_a_bearer_token(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {SERVICE_KEY}')

        self.assertEqual(client.get(BASE + '/').status_code, 200)

    def test_web_sessions_do_not_work_not_even_for_an_administrator(self):
        """Тайинлаш ES да қилинмайди: админ ҳам, HR ҳам, табелчи ҳам веб-сессия билан бу API дан фойдалана олмайди."""
        self.assign(self.dept_a, self.ali)
        for who in (self.admin, self.hr, User.objects.get(username='ali_login')):
            client = APIClient()
            client.force_authenticate(user=who)
            for method, url in self.URLS:
                with self.subTest(user=who.username, method=method):
                    response = getattr(client, method)(url.format(dept=self.dept_a.id), {'tabel_number': '102'}, format='json')
                    self.assertIn(response.status_code, (401, 403))
        self.assertEqual(Timekeeper.objects.get().employee, self.ali)

    def test_there_is_no_throttle_for_the_key(self):
        for _ in range(40):
            self.assertEqual(self.api.get(BASE + '/').status_code, 200)


class ListAndDetailTests(AssignmentApiBase):
    def test_list_has_every_department_in_the_staff_tree_order_with_the_timekeeper(self):
        leadership = Department.objects.create(name='Раҳбарият', sort_order=500)
        self.assign(self.dept_a, self.ali)

        data = self.api.get(BASE + '/').json()

        self.assertEqual(data['count'], 3)
        names = [item['department']['name'] for item in data['results']]
        self.assertEqual(names[0], 'Раҳбарият')
        self.assertEqual(names[1:], ['1-Цех Биринчи', '2-Цех Иккинчи'])
        by_name = {item['department']['name']: item for item in data['results']}
        first = by_name['1-Цех Биринчи']
        self.assertEqual(first['employee_count'], 2)
        self.assertEqual(
            {key: first['timekeeper'][key] for key in ('employee_id', 'tabel_number', 'full_name', 'is_active', 'has_es_account')},
            {'employee_id': self.ali.id, 'tabel_number': '101', 'full_name': str(self.ali), 'is_active': True, 'has_es_account': True},
        )
        self.assertRegex(first['timekeeper']['assigned_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+05:00$')
        self.assertIsNone(by_name['2-Цех Иккинчи']['timekeeper'])
        self.assertIsNone(by_name[leadership.name]['timekeeper'])

    def test_filters(self):
        self.assign(self.dept_a, self.ali)

        assigned = self.api.get(BASE + '/?assigned=true').json()
        free = self.api.get(BASE + '/?assigned=false').json()
        mine = self.api.get(BASE + '/?tabel_number=101').json()
        nobody = self.api.get(BASE + '/?tabel_number=999').json()

        self.assertEqual([item['department']['id'] for item in assigned['results']], [self.dept_a.id])
        self.assertEqual([item['department']['id'] for item in free['results']], [self.dept_b.id])
        self.assertEqual([item['department']['id'] for item in mine['results']], [self.dept_a.id])
        self.assertEqual((nobody['count'], nobody['results']), (0, []))
        bad = self.api.get(BASE + '/?assigned=maybe')
        self.assertEqual(bad.status_code, 400)
        self.assertIn('assigned', bad.json())

    def test_the_list_does_not_run_a_query_per_department(self):
        def queries():
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.api.get(BASE + '/').status_code, 200)
            return len(context.captured_queries)

        few = queries()
        for number in range(10, 30):
            Department.objects.create(name=f'{number}-Цех Тест', sort_order=number)
        self.assertEqual(queries(), few)

    def test_detail(self):
        self.assign(self.dept_a, self.ali)

        taken = self.api.get(f'{BASE}/{self.dept_a.id}/').json()
        free = self.api.get(f'{BASE}/{self.dept_b.id}/').json()

        self.assertEqual(taken['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual(taken['timekeeper']['tabel_number'], '101')
        self.assertEqual(free, {'department': {'id': self.dept_b.id, 'name': self.dept_b.name}, 'timekeeper': None})
        missing = self.api.get(f'{BASE}/99999/')
        self.assertEqual((missing.status_code, missing.json()['code']), (404, 'department_not_found'))

    def test_a_timekeeper_who_stopped_working_is_listed_as_not_active(self):
        self.assign(self.dept_a, self.ali)
        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])

        timekeeper = self.api.get(f'{BASE}/{self.dept_a.id}/').json()['timekeeper']

        self.assertFalse(timekeeper['is_active'])      # бошқа тизим шуни кўриб, янги табелчи тайинлаши мумкин


class AssignTests(AssignmentApiBase):
    def test_assign_an_employee_with_an_account_sets_the_web_role(self):
        response = self.put(self.dept_a, '101')

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual((data['timekeeper']['tabel_number'], data['timekeeper']['has_es_account']), ('101', True))
        self.assertEqual((data['replaced'], data['changed']), (None, True))
        self.assertEqual(Timekeeper.objects.get(department=self.dept_a).employee, self.ali)
        self.assertEqual(self.role_of('ali_login'), ROLE_TIMEKEEPER)

    def test_assign_an_employee_without_an_account_does_not_create_one(self):
        users_before = User.objects.count()
        profiles_before = UserProfile.objects.count()

        response = self.put(self.dept_a, '102')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['timekeeper']['has_es_account'])
        self.assertEqual(Timekeeper.objects.get().employee, self.bobur)
        self.assertEqual((User.objects.count(), UserProfile.objects.count()), (users_before, profiles_before))   # ҳисоб очилмайди
        self.bobur.refresh_from_db()
        self.assertEqual((self.bobur.user_id, self.bobur.login, self.bobur.password_hash), (None, '', ''))

    def test_an_employee_with_a_disabled_account_can_still_be_assigned(self):
        User.objects.filter(username='ali_login').update(is_active=False)

        self.assertEqual(self.put(self.dept_a, '101').status_code, 200)

    def test_a_timekeeper_with_an_account_can_use_the_es_web_pages(self):
        self.put(self.dept_a, '101')

        client = client_for(User.objects.get(username='ali_login'))

        self.assertEqual(client.get('/department-employees/').status_code, 200)
        self.assertEqual(client.get('/api/template/employees/').json()['count'], 2)

    def test_assigning_the_same_employee_again_changes_nothing(self):
        self.put(self.dept_a, '101')

        response = self.put(self.dept_a, '101')

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()['changed'], response.json()['replaced']), (False, None))
        self.assertEqual(Timekeeper.objects.count(), 1)

    def test_replacing_the_timekeeper_reports_the_old_one_and_returns_him_to_a_plain_user(self):
        make_user('bobur_login', ROLE_USER, employee=self.bobur)
        self.put(self.dept_a, '101')

        response = self.put(self.dept_a, '102')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['replaced'], {'tabel_number': '101', 'full_name': str(self.ali)})
        self.assertTrue(data['changed'])
        self.assertEqual(Timekeeper.objects.get(department=self.dept_a).employee, self.bobur)
        self.assertEqual((self.role_of('ali_login'), self.role_of('bobur_login')), (ROLE_USER, ROLE_TIMEKEEPER))

    def test_the_tabel_number_is_trimmed_and_may_be_a_number(self):
        self.assertEqual(self.put(self.dept_a, ' 101 ').status_code, 200)
        self.assertEqual(self.api.put(f'{BASE}/{self.dept_a.id}/', {'tabel_number': 102}, format='json').status_code, 200)
        self.assertEqual(Timekeeper.objects.get().employee, self.bobur)

    def test_a_form_encoded_request_works_too(self):
        response = self.api.put(f'{BASE}/{self.dept_a.id}/', {'tabel_number': '101'}, format='multipart')

        self.assertEqual(response.status_code, 200)

    def test_malformed_bodies_are_a_400_with_the_field(self):
        for body in ({}, {'tabel_number': ''}, {'tabel_number': '   '}, {'wrong': 'x'}, None):
            with self.subTest(body=body):
                response = self.api.put(f'{BASE}/{self.dept_a.id}/', body, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('tabel_number', response.json())
        array = self.api.put(f'{BASE}/{self.dept_a.id}/', ['101'], format='json')
        self.assertEqual(array.status_code, 400)
        self.assertFalse(Timekeeper.objects.exists())

    def test_unknown_department_and_unknown_or_deleted_employee_are_404_with_their_own_codes(self):
        unknown_department = self.api.put(f'{BASE}/99999/', {'tabel_number': '101'}, format='json')
        self.assertEqual((unknown_department.status_code, unknown_department.json()['code']), (404, 'department_not_found'))
        unknown_employee = self.put(self.dept_a, 'NOPE-1')
        self.assertEqual((unknown_employee.status_code, unknown_employee.json()['code']), (404, 'employee_not_found'))
        self.ali.is_deleted = True
        self.ali.save(update_fields=['is_deleted'])
        deleted = self.put(self.dept_a, '101')
        self.assertEqual((deleted.status_code, deleted.json()['code']), (404, 'employee_not_found'))
        self.assertFalse(Timekeeper.objects.exists())


class AssignRuleTests(AssignmentApiBase):
    """Бизнес-қоидалар: 409 ва барқарор «code». Хато бўлса ҳеч нарса (рол ҳам) ўзгармайди."""

    def assertConflict(self, response, code):
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], code)
        self.assertIn(code, timekeepers.ASSIGNMENT_ERROR_CODES)

    def test_an_employee_of_another_department_is_refused(self):
        self.assertConflict(self.put(self.dept_a, '201'), 'employee_not_in_department')
        self.assertFalse(Timekeeper.objects.exists())

    def test_an_employee_without_a_department_is_refused(self):
        make_employee(300, None, first_name='Цехсиз')

        self.assertConflict(self.put(self.dept_a, '300'), 'employee_not_in_department')

    def test_leadership_employees_and_the_leadership_department_are_refused(self):
        leadership = Department.objects.create(name='Раҳбарият', sort_order=42)
        boss = make_employee(900, leadership, first_name='Раҳбар')
        make_user('boss_login', ROLE_USER, employee=boss)

        self.assertConflict(self.put(self.dept_a, '900'), 'leadership_department')
        self.assertConflict(self.put(leadership, '900'), 'leadership_department')
        self.assertConflict(self.put(leadership, '101'), 'leadership_department')
        self.assertFalse(Timekeeper.objects.exists())
        self.assertEqual(self.role_of('boss_login'), ROLE_USER)

    def test_inactive_employees_are_refused(self):
        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])

        self.assertConflict(self.put(self.dept_a, '101'), 'employee_inactive')
        self.assertFalse(Timekeeper.objects.exists())

    def test_admin_hr_and_superuser_accounts_are_refused_and_keep_their_role(self):
        admin_employee = make_employee(103, self.dept_a, first_name='Админ')
        hr_employee = make_employee(104, self.dept_a, first_name='Ҳр')
        super_employee = make_employee(105, self.dept_a, first_name='Супер')
        admin_user = make_user('emp_admin', ROLE_ADMIN, employee=admin_employee)
        hr_user = make_user('emp_hr', ROLE_HR, employee=hr_employee)
        super_employee.user = User.objects.create_superuser(username='emp_super', password=PASSWORD)
        super_employee.save(update_fields=['user'])

        for tabel in ('103', '104', '105'):
            with self.subTest(tabel=tabel):
                self.assertConflict(self.put(self.dept_a, tabel), 'privileged_account')

        admin_user.profile.refresh_from_db()
        hr_user.profile.refresh_from_db()
        self.assertEqual((admin_user.profile.role, hr_user.profile.role), (ROLE_ADMIN, ROLE_HR))
        self.assertFalse(Timekeeper.objects.exists())

    def test_an_employee_cannot_be_timekeeper_of_two_departments_and_the_error_names_the_other_one(self):
        self.put(self.dept_a, '101')

        response = self.put(self.dept_b, '101')

        self.assertConflict(response, 'already_timekeeper_elsewhere')
        self.assertEqual(response.json()['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual(Timekeeper.objects.count(), 1)

    def test_moving_a_timekeeper_to_another_department_needs_removal_first(self):
        self.put(self.dept_a, '101')
        self.assertEqual(self.api.delete(f'{BASE}/{self.dept_a.id}/').status_code, 204)
        self.ali.department = self.dept_b
        self.ali.save(update_fields=['department'])

        self.assertEqual(self.put(self.dept_b, '101').status_code, 200)

    def test_a_failed_replacement_keeps_the_current_timekeeper_and_his_role(self):
        self.put(self.dept_a, '101')

        self.assertConflict(self.put(self.dept_a, '201'), 'employee_not_in_department')

        self.assertEqual(Timekeeper.objects.get(department=self.dept_a).employee, self.ali)
        self.assertEqual(self.role_of('ali_login'), ROLE_TIMEKEEPER)


class RemoveAndSyncTests(AssignmentApiBase):
    def test_remove_takes_the_role_back_but_keeps_the_account(self):
        self.put(self.dept_a, '101')

        response = self.api.delete(f'{BASE}/{self.dept_a.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        self.assertFalse(Timekeeper.objects.exists())
        self.assertEqual(self.role_of('ali_login'), ROLE_USER)

    def test_remove_is_idempotent_and_an_unknown_department_is_404(self):
        self.assertEqual(self.api.delete(f'{BASE}/{self.dept_a.id}/').status_code, 204)
        self.put(self.dept_a, '102')
        self.assertEqual(self.api.delete(f'{BASE}/{self.dept_a.id}/').status_code, 204)
        self.assertEqual(self.api.delete(f'{BASE}/{self.dept_a.id}/').status_code, 204)
        missing = self.api.delete(f'{BASE}/99999/')
        self.assertEqual((missing.status_code, missing.json()['code']), (404, 'department_not_found'))

    def test_an_es_administrator_changing_the_role_removes_the_assignment_and_the_list_shows_it(self):
        """ES да админ ҳисоб ролини ўзгартирса, тайинлов ўчади: бошқа тизим рўйхатни солиштириб (reconcile) буни кўради."""
        self.put(self.dept_a, '101')
        profile = User.objects.get(username='ali_login').profile

        response = self.admin_client.patch(f'/api/template/users/{profile.id}/', data='{"role": "user"}', content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.api.get(f'{BASE}/{self.dept_a.id}/').json()['timekeeper'])

    def test_deleting_the_employee_in_es_removes_the_assignment(self):
        self.put(self.dept_a, '101')

        self.assertEqual(self.admin_client.delete(f'/api/template/employees/{self.ali.id}/').status_code, 204)

        self.assertIsNone(self.api.get(f'{BASE}/{self.dept_a.id}/').json()['timekeeper'])


class AssignmentIsNotInTheEsWebAnymoreTests(AssignmentApiBase):
    def test_the_old_internal_routes_are_gone(self):
        client = client_for(self.admin)
        for method, url in (('get', '/timekeepers/'), ('get', '/api/template/timekeepers/'), ('get', '/api/template/timekeepers/candidates/'),
                            ('put', f'/api/template/timekeepers/{self.dept_a.id}/'), ('delete', f'/api/template/timekeepers/{self.dept_a.id}/')):
            with self.subTest(url=url, method=method):
                self.assertEqual(getattr(client, method)(url).status_code, 404)

    def test_no_account_is_ever_created_by_the_assignment_code(self):
        source = (django_settings.BASE_DIR / 'employees' / 'timekeepers.py').read_text(encoding='utf-8')

        self.assertNotIn('create_account', source)
        self.assertNotIn('create_login_user', source)
        self.assertIsNone(re.search(r'set_password|password=', source))
