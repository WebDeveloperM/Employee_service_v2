"""Табелчи номидан ишлаш API си (/api/v1/timekeeper/<табелчи табели>/...): бошқа тизим сервис калити билан, табелчи номидан,
унинг цехи ходимларини ўқийди ва янгилайди.

Ҳужжат: API_TIMEKEEPER.md (охирги синф ҳужжат кодга мос эканини текширади).
"""
import json
import re
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from . import timekeeper_api, timekeepers
from .models import (
    Department, Employee, EmployeeCategory, EmployeeReplacement, PositionCategory, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section, Timekeeper,
    WorkSchedule,
)
from .test_timekeeper_assignments import SERVICE_KEY, key_client
from .test_timekeepers import TimekeeperTestBase, make_employee, make_user

BASE = '/api/v1/timekeeper'


@override_settings(EMPLOYEE_SERVICE_API_KEYS=[SERVICE_KEY])
class ApiTestBase(TimekeeperTestBase):
    def setUp(self):
        super().setUp()
        self.assign(self.dept_a, self.ali)          # Али (101) — 1-цех табелчиси, ES ҳисоби бор («ali_login»)
        self.api = key_client()
        self.dept_a_section = Section.objects.create(department=self.dept_a, name='Юридик бўлим', code='1')
        self.dept_b_section = Section.objects.create(department=self.dept_b, name='Бошқа цех бўлими', code='1')

    def url(self, path='', tabel='101'):
        return f'{BASE}/{tabel}/{path}'


class AccessTests(ApiTestBase):
    def all_calls(self, tabel='101'):
        return (
            ('get', self.url('', tabel)), ('get', self.url('reference/', tabel)), ('get', self.url('employees/', tabel)),
            ('get', self.url('employees/102/', tabel)), ('patch', self.url('employees/102/', tabel)),
        )

    def test_without_a_key_everything_is_401_even_for_an_unknown_timekeeper(self):
        for tabel in ('101', '999999'):
            for method, url in self.all_calls(tabel):
                with self.subTest(method=method, url=url):
                    response = getattr(APIClient(), method)(url, {'position': 'x'}, format='json')
                    self.assertEqual(response.status_code, 401)       # калит текшируви биринчи: табелчи борлиги ошкор бўлмайди
                    self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="employee-service"')
                    self.assertEqual(response.json()['code'], 'not_authenticated')

    def test_a_wrong_key_and_a_web_session_are_refused(self):
        session = APIClient()
        session.force_login(User.objects.get(username='ali_login'))
        admin = APIClient()
        admin.force_login(self.admin)
        clients = {'wrong key': key_client('wrong-key'), 'timekeeper session': session, 'admin session': admin}
        for name, client in clients.items():
            for method, url in self.all_calls():
                with self.subTest(client=name, method=method, url=url):
                    self.assertEqual(getattr(client, method)(url, {'position': 'x'}, format='json').status_code, 401)
        self.assertEqual(Employee.objects.get(pk=self.bobur.pk).position, 'Фаррош')

    def test_the_key_also_works_as_a_bearer_token(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {SERVICE_KEY}')

        self.assertEqual(client.get(self.url('employees/')).status_code, 200)

    def test_only_a_working_timekeeper_can_be_acted_for(self):
        """Тайинланмаган, ўчирилган, фаол эмас ва тайинлови олиб ташланган табелчи номидан иш қилиб бўлмайди."""
        refused = {
            'unknown tabel': '999999',
            'employee who is not a timekeeper': '102',
            'plain employee of another department': '201',
        }
        for name, tabel in refused.items():
            with self.subTest(case=name):
                response = self.api.get(self.url('employees/', tabel))
                self.assertEqual((response.status_code, response.json()['code']), (403, 'not_a_timekeeper'))

        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])
        self.assertEqual(self.api.get(self.url('employees/')).json()['code'], 'not_a_timekeeper')
        self.ali.is_active = True
        self.ali.is_deleted = True
        self.ali.save(update_fields=['is_active', 'is_deleted'])
        self.assertEqual(self.api.get(self.url('employees/')).status_code, 403)
        self.ali.is_deleted = False
        self.ali.save(update_fields=['is_deleted'])
        self.assertEqual(self.api.get(self.url('employees/')).status_code, 200)
        timekeepers.remove_timekeeper(self.dept_a)
        self.assertEqual(self.api.get(self.url('employees/')).status_code, 403)

    def test_odd_timekeeper_numbers_are_never_a_500(self):
        for tabel in ('%20', 'A' * 400, "x'%20OR%201=1", '٣١٤', 'ё'):
            with self.subTest(tabel=tabel):
                self.assertEqual(self.api.get(self.url('employees/', tabel)).status_code, 403)
        # «/» бор қиймат — маршрутга мос келмайди (404): ҳеч қачон 500 эмас, ҳеч қачон бошқа табелчига «ўтиб» кетмайди.
        for tabel in ('..%2f..', '101/employees/../..', '101%2f'):
            with self.subTest(tabel=tabel):
                self.assertIn(self.api.get(self.url('employees/', tabel)).status_code, (403, 404))

    def test_a_timekeeper_without_an_es_account_works(self):
        """ES ҳисоби йўқ табелчи (тайинлов API орқали, ҳисоб очилмаган) бошқа тизим орқали тўлиқ ишлайди."""
        self.assign(self.dept_b, self.sardor)
        self.assertIsNone(Employee.objects.get(pk=self.sardor.pk).user_id)
        make_employee(202, self.dept_b, first_name='Иккинчи')

        listing = self.api.get(self.url('employees/', '201')).json()
        updated = self.api.patch(self.url('employees/202/', '201'), {'position': 'Электрик'}, format='json')

        self.assertEqual(sorted(item['tabel_number'] for item in listing['results']), ['201', '202'])
        self.assertEqual(updated.status_code, 200)

    def test_every_timekeeper_sees_and_changes_only_his_own_department(self):
        self.assign(self.dept_b, self.sardor)

        ali_sees = {item['tabel_number'] for item in self.api.get(self.url('employees/', '101')).json()['results']}
        sardor_sees = {item['tabel_number'] for item in self.api.get(self.url('employees/', '201')).json()['results']}

        self.assertEqual((ali_sees, sardor_sees), ({'101', '102'}, {'201'}))
        self.assertEqual(self.api.get(self.url('employees/201/', '101')).status_code, 404)
        self.assertEqual(self.api.get(self.url('employees/101/', '201')).status_code, 404)
        self.assertEqual(self.api.patch(self.url('employees/201/', '101'), {'position': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.api.patch(self.url('employees/101/', '201'), {'position': 'x'}, format='json').status_code, 404)

    def test_a_replaced_timekeeper_loses_access_at_once_and_the_new_one_gets_it(self):
        self.assertEqual(self.api.get(self.url('employees/')).status_code, 200)

        self.assign(self.dept_a, self.bobur)       # Бобур Алининг ўрнига

        self.assertEqual(self.api.get(self.url('employees/', '101')).status_code, 403)
        self.assertEqual(self.api.get(self.url('employees/', '102')).status_code, 200)

    def test_info_shows_who_and_where(self):
        data = self.api.get(self.url()).json()

        self.assertEqual(data['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual((data['timekeeper']['tabel_number'], data['timekeeper']['is_active']), ('101', True))

    def test_no_throttle(self):
        for _ in range(40):
            self.assertEqual(self.api.get(self.url()).status_code, 200)

    def test_the_old_token_endpoints_do_not_exist(self):
        """Илгари табелчи ES логини билан токен оларди. Энди йўқ: «token», «logout», «me» — оддий (мавжуд бўлмаган) табелчи рақами."""
        for path in ('token/', 'logout/', 'me/'):
            with self.subTest(path=path):
                response = self.api.post(f'{BASE}/{path}', {'login': 'x', 'password': 'y'}, format='json')
                self.assertEqual(response.status_code, 403)      # not_a_timekeeper
                self.assertEqual(response.json()['code'], 'not_a_timekeeper')


class ReferenceTests(ApiTestBase):
    def test_reference_lists_only_the_own_departments_sections_and_all_the_dictionaries(self):
        PositionCategory.objects.get_or_create(name='Тест лавозим тоифаси')
        EmployeeCategory.objects.get_or_create(name='Тест ходим тоифаси')
        WorkSchedule.objects.get_or_create(name='Тест иш тартиби')
        child = Section.objects.create(department=self.dept_a, name='Ички участка', code='1', parent=self.dept_a_section)

        data = self.api.get(self.url('reference/')).json()

        self.assertEqual(data['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual({item['id'] for item in data['sections']}, {self.dept_a_section.id, child.id})
        self.assertEqual([item['path'] for item in data['sections']], ['Юридик бўлим', 'Юридик бўлим / Ички участка'])
        self.assertEqual([item['id'] for item in data['position_categories']], list(PositionCategory.objects.values_list('id', flat=True)))
        self.assertEqual([item['id'] for item in data['employee_categories']], list(EmployeeCategory.objects.values_list('id', flat=True)))
        self.assertEqual([item['id'] for item in data['work_schedules']], list(WorkSchedule.objects.values_list('id', flat=True)))
        self.assertIn('Тест иш тартиби', [item['name'] for item in data['work_schedules']])
        self.assertEqual([item['value'] for item in data['statuses']], ['working', 'vacation', 'business_trip', 'sick_leave', 'childcare_leave', 'over_staff', 'inactive'])
        self.assertEqual([item['value'] for item in data['genders']], ['M', 'F'])


class EmployeeReadTests(ApiTestBase):
    def tabels(self, response):
        return [item['tabel_number'] for item in response.json()['results']]

    def tabels_of(self, payload):
        return [item['tabel_number'] for item in payload['results']]

    def test_the_list_holds_only_the_own_department_without_deleted_employees(self):
        make_employee(103, self.dept_a, is_deleted=True)
        make_employee(104, self.dept_a)

        response = self.api.get(self.url('employees/'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 3)
        self.assertEqual(sorted(self.tabels(response)), ['101', '102', '104'])
        self.assertNotIn('201', self.tabels(response))      # бошқа цех ходими

    def test_the_list_cannot_be_widened_by_filters(self):
        for query in (f'?department_id={self.dept_b.id}', '?search=Сардор', '?tabel_number=201', '?page_size=200'):
            with self.subTest(query=query):
                self.assertNotIn('201', self.tabels(self.api.get(self.url('employees/') + query)))

    def test_pagination(self):
        for number in range(110, 125):
            make_employee(number, self.dept_a)

        first = self.api.get(self.url('employees/') + '?page_size=10').json()
        second = self.api.get(first['next']).json()

        self.assertEqual((first['count'], len(first['results']), len(second['results'])), (17, 10, 7))
        self.assertEqual(len({*self.tabels_of(first), *self.tabels_of(second)}), 17)
        self.assertEqual(self.api.get(self.url('employees/') + '?page_size=1000').json()['count'], 17)

    def test_search_tabel_status_and_updated_since_filters(self):
        self.bobur.work_status = 'vacation'
        self.bobur.save(update_fields=['work_status'])
        listing = self.url('employees/')

        self.assertEqual(self.tabels(self.api.get(f'{listing}?search=Бобур')), ['102'])
        self.assertEqual(self.tabels(self.api.get(f'{listing}?tabel_number=101')), ['101'])
        self.assertEqual(self.tabels(self.api.get(f'{listing}?status=vacation')), ['102'])
        self.assertEqual(self.tabels(self.api.get(f'{listing}?status=working')), ['101'])
        self.assertEqual(self.tabels(self.api.get(f'{listing}?status=inactive')), [])
        future = (timezone.now() + timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
        self.assertEqual(self.tabels(self.api.get(listing, {'updated_since': future})), [])
        past = (timezone.now() - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
        self.assertEqual(len(self.tabels(self.api.get(listing, {'updated_since': past}))), 2)

    def test_bad_filters_are_a_400_with_the_field_name(self):
        self.assertIn('status', self.api.get(self.url('employees/') + '?status=sleeping').json())
        self.assertIn('updated_since', self.api.get(self.url('employees/') + '?updated_since=yesterday').json())

    def test_the_number_of_queries_does_not_grow_with_the_page(self):
        def queries():
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.api.get(self.url('employees/') + '?page_size=100').status_code, 200)
            return len(context.captured_queries)

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

        data = self.api.get(self.url('employees/102/')).json()

        self.assertEqual(data['tabel_number'], '102')
        self.assertEqual(data['department'], {'id': self.dept_a.id, 'name': self.dept_a.name})
        self.assertEqual(data['section'], {'id': self.dept_a_section.id, 'code': '1', 'name': 'Юридик бўлим', 'path': 'Юридик бўлим'})
        self.assertEqual((data['status'], data['status_label'], data['is_active']), ('working', 'Фаол', True))
        self.assertIsNone(data['replacement'])
        self.assertEqual(data['phone_number_1'], '+998901112233')

    def test_nothing_private_or_internal_is_in_the_response(self):
        data = self.api.get(self.url('employees/101/')).json()    # Алида ҳисоб бор

        for name in ('login', 'password', 'password_hash', 'metadata', 'source_system', 'external_id', 'role', 'requires_face_id_checkout', 'is_deleted'):
            self.assertNotIn(name, data)

    def test_other_departments_deleted_and_unknown_employees_are_all_just_not_found(self):
        make_employee(105, self.dept_a, is_deleted=True)
        for tabel in ('201', '105', '99999'):
            with self.subTest(tabel=tabel):
                response = self.api.get(self.url(f'employees/{tabel}/'))
                self.assertEqual((response.status_code, response.json()['code']), (404, 'not_found'))


class EmployeeUpdateTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.target = self.url('employees/102/')

    def patch(self, data, url=None, **kwargs):
        return self.api.patch(url or self.target, data, format='json', **kwargs)

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
        response = self.api.patch(self.target, {'position': 'Электрик', 'phone_number_1': '+998900000000'}, format='multipart')

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

    def test_the_substitute_is_not_tied_to_any_es_account(self):
        """API орқали тайинланган ўринбосарда «ким тайинлаган» (ES ҳисоби) бўш: табелчининг ES ҳисоби бўлса ҳам
        (ES ҳисоби билан ишламайди), бўлмаса ҳам. Ким қилгани — хавфсизлик журналида (табелчининг табель рақами)."""
        with self.assertLogs('employees.security', level='INFO') as logs:
            self.patch({'status': 'vacation', 'replacement_tabel_number': '101'})
        self.assertIsNone(EmployeeReplacement.objects.get().assigned_by)
        self.assertTrue(any("timekeeper='101'" in line for line in logs.output), logs.output)

        self.assign(self.dept_b, self.sardor)                                    # ҳисоби йўқ табелчи
        second = make_employee(202, self.dept_b)
        make_employee(203, self.dept_b)
        self.patch({'status': 'vacation', 'replacement_tabel_number': '203'}, url=self.url('employees/202/', '201'))
        self.assertIsNone(EmployeeReplacement.objects.get(employee=second).assigned_by)

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
        self.assertFalse(User.objects.filter(username='hacker').exists())

    def test_every_refused_field_has_an_explanation_not_just_the_generic_one(self):
        for name in ('tabel_number', 'department_id', 'is_active', 'work_status', 'replacement_employee_id', 'base_image', 'login', 'password', 'role'):
            with self.subTest(field=name):
                message = self.patch({name: 'x'}).json()[name][0]
                self.assertNotEqual(message, timekeeper_api.UNKNOWN_FIELD_MESSAGE)

    def test_empty_and_non_object_bodies_are_refused(self):
        empty = self.patch({})
        self.assertEqual((empty.status_code, empty.json()['code']), (400, 'empty_update'))
        array = self.api.patch(self.target, ['position'], format='json')
        self.assertEqual((array.status_code, array.json()['code']), (400, 'parse_error'))

    def test_an_employee_of_another_department_cannot_be_touched_or_even_seen(self):
        before = Employee.objects.get(pk=self.sardor.pk).first_name

        response = self.patch({'first_name': 'Бузилди'}, url=self.url('employees/201/'))

        self.assertEqual((response.status_code, response.json()['code']), (404, 'not_found'))
        self.assertEqual(Employee.objects.get(pk=self.sardor.pk).first_name, before)

    def test_a_section_of_another_department_is_refused(self):
        response = self.patch({'section_id': self.dept_b_section.id})

        self.assertEqual(response.status_code, 400)
        self.assertIn('section_id', response.json())
        self.assertIsNone(Employee.objects.get(pk=self.bobur.pk).section_id)

    def test_too_long_values_are_a_400_not_a_database_error(self):
        too_long = {'surname': 'х' * 256, 'phone_number_1': '9' * 21, 'phone_number_2': '9' * 21, 'clothe_size': 'х' * 256,
                    'first_name': 'х' * 256, 'last_name': 'х' * 256, 'shoe_size': 'х' * 256, 'special_clothing_size': 'х' * 256}
        for field, value in too_long.items():
            with self.subTest(field=field):
                response = self.patch({field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        self.assertEqual(self.patch({'phone_number_1': '9' * 20, 'surname': 'х' * 255}).status_code, 200)   # чегарада — ўтади

    def test_invalid_values_come_back_as_field_errors(self):
        for field, value in (('date_of_birth', '31-31-2020'), ('grade', '123*'), ('gender', 'X'), ('position_category_id', 99999)):
            with self.subTest(field=field):
                response = self.patch({field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())

    def test_an_administrators_or_hr_employee_is_protected_even_though_the_service_key_is_trusted(self):
        """Сервис калити «ишончли ёзувчи» (ходим ҳолатини ўзи синхронлайди), аммо табелчи номидан иш қилганда
        администратор/HR ҳисобидаги ходимга тегиб бўлмайди — веб-саҳифадаги «Цех ходимлари» билан бир хил."""
        admin_employee = make_employee(103, self.dept_a, first_name='Админ')
        hr_employee = make_employee(104, self.dept_a, first_name='Ҳр')
        make_user('admin_employee', ROLE_ADMIN, employee=admin_employee)
        make_user('hr_employee', ROLE_HR, employee=hr_employee)
        superuser = make_employee(105, self.dept_a, first_name='Супер')
        superuser.user = User.objects.create_superuser(username='emp_super', password='Zhr29Kqp71')
        superuser.save(update_fields=['user'])

        for tabel in ('103', '104', '105'):
            for payload in ({'position': 'Бошқа'}, {'status': 'inactive'}, {'phone_number_1': '+998900000000'}):
                with self.subTest(tabel=tabel, payload=payload):
                    response = self.patch(payload, url=self.url(f'employees/{tabel}/'))
                    self.assertEqual((response.status_code, response.json()['code']), (403, 'permission_denied'))
        self.assertEqual(Employee.objects.get(pk=admin_employee.pk).position, 'Фаррош')
        self.assertTrue(User.objects.get(username='admin_employee').is_active)
        self.assertTrue(self.api.get(self.url('employees/103/')).json()['is_protected'])

    def test_only_get_and_patch_exist(self):
        self.assertEqual(self.api.put(self.target, {'position': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.api.delete(self.target).status_code, 405)
        self.assertEqual(self.api.post(self.url('employees/'), {'tabel_number': '1'}, format='json').status_code, 405)

    def test_updates_are_logged_with_field_names_but_not_their_values(self):
        with self.assertLogs('employees.security', level='INFO') as logs:
            self.patch({'phone_number_1': '+998905554433', 'position': 'Бош муҳандис'})

        line = next(entry for entry in logs.output if 'timekeeper api update' in entry)
        self.assertIn("timekeeper='101'", line)
        self.assertIn("employee='102'", line)
        self.assertIn('phone_number_1', line)
        self.assertNotIn('998905554433', line)
        self.assertNotIn('Бош муҳандис', line)
        self.assertNotIn(SERVICE_KEY, ''.join(logs.output))


class DocumentationMatchesTheCodeTests(ApiTestBase):
    """API_TIMEKEEPER.md кодга мос: ҳар бир манзил, параметр, ёзиладиган майдон, ҳолат ва хато коди ҳужжатда бор."""

    @classmethod
    def docs(cls):
        return (django_settings.BASE_DIR / 'API_TIMEKEEPER.md').read_text(encoding='utf-8')

    def test_every_route_is_documented(self):
        docs = self.docs()
        for route in ('/api/v1/timekeeper-assignments/', '/api/v1/timekeeper-assignments/{department_id}/',
                      '/api/v1/timekeeper/{timekeeper_tabel}/', '/api/v1/timekeeper/{timekeeper_tabel}/reference/',
                      '/api/v1/timekeeper/{timekeeper_tabel}/employees/', '/api/v1/timekeeper/{timekeeper_tabel}/employees/{tabel_number}/'):
            self.assertIn(route, docs)

    def test_every_query_parameter_is_documented(self):
        docs = self.docs()
        for name in ('assigned', 'tabel_number', 'search', 'status', 'updated_since', 'page', 'page_size'):
            self.assertIn(f'`{name}`', docs, name)

    def test_every_writable_field_and_every_refused_field_is_documented(self):
        docs = self.docs()
        for name in sorted(timekeeper_api.WRITABLE_FIELDS | set(timekeeper_api.REJECTED_HINTS)):
            self.assertIn(f'`{name}`', docs, name)

    def test_every_status_is_documented(self):
        docs = self.docs()
        for value in timekeeper_api.STATUS_LABELS:
            self.assertIn(f'`{value}`', docs, value)

    def test_every_error_code_is_documented(self):
        docs = self.docs()
        source = (django_settings.BASE_DIR / 'employees' / 'timekeeper_api.py').read_text(encoding='utf-8')
        codes = set(re.findall(r"code='([a-z_]+)'", source)) | set(re.findall(r"'code': '([a-z_]+)'", source))
        codes |= set(timekeepers.ASSIGNMENT_ERROR_CODES)
        codes |= {'not_authenticated', 'authentication_failed', 'permission_denied', 'not_found', 'method_not_allowed', 'parse_error'}
        self.assertGreaterEqual(len(codes), 12)
        for code in sorted(codes):
            self.assertIn(f'`{code}`', docs, code)

    def test_the_documented_timekeeper_object_is_what_the_api_returns(self):
        """3.2-бўлимдаги табелчи объекти — API жавобининг ўзи; олиб ташланганлар фақат «0. O'zgarishlar» да тилга олинади."""
        docs = self.docs()
        section = docs[docs.index('### 3.2'):docs.index('### 3.3')]
        documented = json.loads(section[section.index('```json') + len('```json'):section.index('```', section.index('```json') + 1)])
        real = self.api.get(self.url('')).json()['timekeeper']

        self.assertEqual(sorted(documented), sorted(real))
        current = docs[docs.index('## 1. Bir qarashda'):]
        for removed in ('has_es_account', 'privileged_account'):
            self.assertNotIn(removed, current)
            self.assertIn(f'`{removed}`', docs[:docs.index('## 1. Bir qarashda')])

    def test_the_documented_error_examples_are_what_the_api_really_says(self):
        """API_TIMEKEEPER.md: шу матнлар ҳужжатда мисол сифатида келтирилган."""
        docs = self.docs()
        target = self.url('employees/102/')
        cross = self.api.patch(target, {'section_id': self.dept_b_section.id}, format='json').json()
        self.assertEqual(cross, {'section_id': ['Бўлим танланган цехга тегишли эмас.']})
        self.assertIn(cross['section_id'][0], docs)
        self.ali.work_status = 'vacation'
        self.ali.save(update_fields=['work_status'])
        busy = self.api.patch(target, {'status': 'vacation', 'replacement_tabel_number': '101'}, format='json').json()
        self.assertIn(busy['replacement_tabel_number'][0], docs)
        for message in (timekeeper_api.REJECTED_HINTS['tabel_number'], timekeeper_api.REJECTED_HINTS['login']):
            self.assertIn(message, docs)
        grade = self.api.patch(target, {'grade': '123*'}, format='json').json()['grade'][0]
        self.assertIn(grade, docs)
        taken = key_client().put('/api/v1/timekeeper-assignments/%d/' % self.dept_b.id, {'tabel_number': '101'}, format='json').json()
        self.assertEqual(taken['code'], 'already_timekeeper_elsewhere')
        self.assertIn(taken['detail'], docs)
