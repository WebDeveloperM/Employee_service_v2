"""Ўринбосар: ходимнинг «Ҳолати» «Фаол»дан бошқа қийматга ўзгартирилганда унинг ўрнига ишлайдиган ходим (шу цехдан) тайинланади."""
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from rest_framework.test import APIClient

from . import template_views
from .models import Department, Employee, EmployeeReplacement, ROLE_ADMIN, ROLE_TIMEKEEPER, ROLE_USER, Timekeeper, UserProfile

CANDIDATES = '/api/template/employees/{}/replacement-candidates/'


def make_employee(tabel, department, **extra):
    return Employee.objects.create(
        first_name='Ишчи', last_name=f'Ходим{tabel}', tabel_number=str(tabel), department=department, position='Фаррош', **extra,
    )


def make_user(username, role, employee=None):
    user = User.objects.create_user(username=username, password='Zhr29Kqp71')
    UserProfile.objects.create(user=user, role=role)
    if employee is not None:
        employee.user = user
        employee.login = username
        employee.save(update_fields=['user', 'login'])
    return user


def api_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class ReplacementTestBase(TestCase):
    def setUp(self):
        self.dept_a = Department.objects.create(name='1-Цех Биринчи')
        self.dept_b = Department.objects.create(name='2-Цех Иккинчи')
        self.admin = make_user('rep_admin', ROLE_ADMIN)
        self.api = api_for(self.admin)
        self.absent = make_employee(101, self.dept_a)
        self.sub = make_employee(102, self.dept_a)
        self.other_sub = make_employee(103, self.dept_a)
        self.stranger = make_employee(201, self.dept_b)

    def patch(self, employee=None, client=None, **fields):
        return (client or self.api).patch(f'/api/employees/{(employee or self.absent).slug}/', fields, format='multipart')

    def go_on_leave(self, substitute=None, status='vacation', **extra):
        fields = {'is_active': 'true', 'work_status': status, **extra}
        if substitute is not None:
            fields['replacement_employee_id'] = substitute.pk
        return self.patch(**fields)

    def record(self):
        return EmployeeReplacement.objects.filter(employee=self.absent).first()


class AssignReplacementTests(ReplacementTestBase):
    def test_status_and_substitute_are_saved_together(self):
        response = self.go_on_leave(self.sub)

        self.assertEqual(response.status_code, 200, response.content)
        self.absent.refresh_from_db()
        self.assertEqual(self.absent.work_status, 'vacation')
        record = self.record()
        self.assertEqual((record.substitute, record.assigned_by), (self.sub, self.admin))

    def test_every_absence_status_accepts_a_substitute(self):
        for status in ('vacation', 'business_trip', 'sick_leave', 'childcare_leave', 'over_staff'):
            with self.subTest(status=status):
                self.assertEqual(self.go_on_leave(self.sub, status).status_code, 200)
                self.assertEqual(self.record().substitute, self.sub)

    def test_an_inactive_employee_can_have_a_substitute_too(self):
        response = self.patch(is_active='false', replacement_employee_id=self.sub.pk)

        self.assertEqual(response.status_code, 200, response.content)
        self.absent.refresh_from_db()
        self.assertEqual((self.absent.is_active, self.absent.work_status), (False, None))
        self.assertEqual(self.record().substitute, self.sub)

    def test_the_substitute_is_optional(self):
        self.assertEqual(self.go_on_leave().status_code, 200)
        self.assertIsNone(self.record())

    def test_choosing_another_substitute_replaces_the_record(self):
        self.go_on_leave(self.sub)

        self.go_on_leave(self.other_sub)

        self.assertEqual(EmployeeReplacement.objects.filter(employee=self.absent).count(), 1)
        self.assertEqual(self.record().substitute, self.other_sub)

    def test_one_employee_can_replace_several_absent_colleagues(self):
        second = make_employee(104, self.dept_a)
        self.go_on_leave(self.sub)

        response = self.patch(employee=second, is_active='true', work_status='sick_leave', replacement_employee_id=self.sub.pk)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(EmployeeReplacement.objects.filter(substitute=self.sub).count(), 2)

    def test_a_substitute_who_works_over_the_staff_is_accepted(self):
        self.sub.work_status = 'over_staff'
        self.sub.save(update_fields=['work_status'])

        self.assertEqual(self.go_on_leave(self.sub).status_code, 200)


class KeepAndClearReplacementTests(ReplacementTestBase):
    def setUp(self):
        super().setUp()
        self.go_on_leave(self.sub)

    def test_requests_without_the_field_leave_the_substitute_alone(self):
        self.patch(first_name='Янги')
        self.go_on_leave(status='sick_leave')   # ҳолат ўзгарди, ўринбосар юборилмади

        self.assertEqual(self.record().substitute, self.sub)

    def test_an_empty_value_removes_the_substitute_but_keeps_the_status(self):
        for empty in ('', None):
            with self.subTest(empty=empty):
                self.go_on_leave(self.sub)
                fields = {'is_active': 'true', 'work_status': 'vacation'}
                response = self.api.patch(f'/api/employees/{self.absent.slug}/', {**fields, 'replacement_employee_id': empty}, format='json' if empty is None else 'multipart')

                self.assertEqual(response.status_code, 200, response.content)
                self.assertIsNone(self.record())
                self.absent.refresh_from_db()
                self.assertEqual(self.absent.work_status, 'vacation')

    def test_coming_back_to_work_removes_the_substitute(self):
        response = self.patch(is_active='true', work_status='')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(self.record())

    def test_a_substitute_sent_together_with_the_working_status_is_ignored(self):
        response = self.patch(is_active='true', work_status='', replacement_employee_id=self.other_sub.pk)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(self.record())

    def test_deleting_the_substitute_removes_the_record(self):
        self.sub.delete()

        self.assertIsNone(self.record())
        self.assertTrue(Employee.objects.filter(pk=self.absent.pk).exists())

    def test_an_employee_cannot_be_his_own_substitute_in_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeReplacement.objects.create(employee=self.other_sub, substitute=self.other_sub)


class ReplacementRulesTests(ReplacementTestBase):
    def assert_refused(self, response, text=None):
        self.assertEqual(response.status_code, 400, response.content)
        message = ' '.join(response.json()['replacement_employee_id'])
        if text:
            self.assertIn(text, message)
        self.absent.refresh_from_db()
        self.assertIsNone(self.absent.work_status)   # хато бўлса, «Ҳолати» ҳам ўзгармайди
        self.assertIsNone(self.record())

    def test_the_substitute_must_be_from_the_same_department(self):
        self.assert_refused(self.go_on_leave(self.stranger), 'бир цехдан')

    def test_an_employee_cannot_replace_himself(self):
        self.assert_refused(self.go_on_leave(self.absent), 'ўзининг ўринбосари')

    def test_an_absent_inactive_or_deleted_employee_cannot_be_a_substitute(self):
        on_leave = make_employee(105, self.dept_a, work_status='vacation')
        inactive = make_employee(106, self.dept_a, is_active=False)
        deleted = make_employee(107, self.dept_a, is_deleted=True)

        for candidate in (on_leave, inactive):
            with self.subTest(candidate=candidate.tabel_number):
                self.assert_refused(self.go_on_leave(candidate), 'ҳозир ишда')
        self.assert_refused(self.go_on_leave(deleted), 'топилмади')

    def test_an_unknown_employee_is_refused(self):
        self.assert_refused(self.patch(is_active='true', work_status='vacation', replacement_employee_id=999999), 'топилмади')

    def test_a_garbage_id_is_refused(self):
        response = self.patch(is_active='true', work_status='vacation', replacement_employee_id='abc')

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.record())

    def test_moving_to_another_department_needs_a_substitute_from_that_department(self):
        self.assert_refused(self.patch(is_active='true', work_status='vacation', department_id=self.dept_b.pk, replacement_employee_id=self.sub.pk), 'бир цехдан')

        ok = self.patch(is_active='true', work_status='vacation', department_id=self.dept_b.pk, replacement_employee_id=self.stranger.pk)

        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(self.record().substitute, self.stranger)

    def test_an_employee_without_a_department_cannot_get_a_substitute(self):
        loose = Employee.objects.create(first_name='A', last_name='B', tabel_number='NODEPT')

        response = self.patch(employee=loose, is_active='true', work_status='vacation', replacement_employee_id=self.sub.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('цехини танланг', ' '.join(response.json()['replacement_employee_id']))

    def test_it_cannot_be_given_while_creating_an_employee(self):
        response = self.api.post('/api/employees/', {
            'first_name': 'Янги', 'last_name': 'Ходим', 'tabel_number': '777', 'department_id': self.dept_a.pk,
            'replacement_employee_id': self.sub.pk,
        }, format='multipart')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Employee.objects.filter(tabel_number='777').exists())

    def test_a_plain_employee_cannot_use_it_on_his_own_card(self):
        make_user('plain_rep', ROLE_USER, employee=self.sub)

        response = self.patch(employee=self.sub, client=api_for(User.objects.get(username='plain_rep')), replacement_employee_id=self.other_sub.pk)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmployeeReplacement.objects.exists())


class ReplacementCandidatesTests(ReplacementTestBase):
    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [item['id'] for item in response.json()['data']]

    def test_lists_only_working_employees_of_the_same_department(self):
        make_employee(104, self.dept_a, work_status='sick_leave')
        make_employee(105, self.dept_a, is_active=False)
        make_employee(106, self.dept_a, is_deleted=True)
        over_staff = make_employee(107, self.dept_a, work_status='over_staff')

        response = self.api.get(CANDIDATES.format(self.absent.pk))

        self.assertEqual(sorted(self.ids(response)), sorted([self.sub.pk, self.other_sub.pk, over_staff.pk]))   # ўзи, бошқа цех ва йўқлар киритилмайди
        body = response.json()
        self.assertEqual(body['department'], {'id': self.dept_a.pk, 'name': self.dept_a.name})
        self.assertFalse(body['truncated'])
        item = next(entry for entry in body['data'] if entry['id'] == self.sub.pk)
        self.assertEqual((item['tabel_number'], item['full_name'], item['position']), ('102', str(self.sub), 'Фаррош'))
        self.assertEqual(set(item), {'id', 'full_name', 'tabel_number', 'position', 'section_name', 'base_image_url'})   # шахсий маълумот йўқ

    def test_reports_the_current_substitute(self):
        self.assertIsNone(self.api.get(CANDIDATES.format(self.absent.pk)).json()['current'])
        self.go_on_leave(self.sub)

        self.assertEqual(self.api.get(CANDIDATES.format(self.absent.pk)).json()['current'], self.sub.pk)

    def test_admin_can_look_at_the_department_the_employee_is_being_moved_to(self):
        response = self.api.get(CANDIDATES.format(self.absent.pk), {'department_id': self.dept_b.pk})

        self.assertEqual(self.ids(response), [self.stranger.pk])
        self.assertEqual(response.json()['department']['id'], self.dept_b.pk)

    def test_unknown_employee_and_missing_department(self):
        self.assertEqual(self.api.get(CANDIDATES.format(999999)).status_code, 404)
        loose = Employee.objects.create(first_name='A', last_name='B', tabel_number='NODEPT')

        body = self.api.get(CANDIDATES.format(loose.pk)).json()

        self.assertEqual((body['department'], body['data']), (None, []))

    def test_the_list_is_cut_when_the_department_is_huge(self):
        with mock.patch.object(template_views, 'CANDIDATE_LIMIT', 1):
            body = self.api.get(CANDIDATES.format(self.absent.pk)).json()

        self.assertEqual(len(body['data']), 1)
        self.assertTrue(body['truncated'])

    def test_plain_users_and_anonymous_have_no_access(self):
        make_user('plain_cand', ROLE_USER, employee=self.sub)

        self.assertEqual(api_for(User.objects.get(username='plain_cand')).get(CANDIDATES.format(self.absent.pk)).status_code, 403)
        self.assertIn(APIClient().get(CANDIDATES.format(self.absent.pk)).status_code, (401, 403))


class TimekeeperReplacementTests(ReplacementTestBase):
    def setUp(self):
        super().setUp()
        self.keeper_employee = make_employee(110, self.dept_a)
        self.keeper_user = make_user('rep_keeper', ROLE_TIMEKEEPER, employee=self.keeper_employee)
        Timekeeper.objects.create(department=self.dept_a, employee=self.keeper_employee)
        self.keeper = api_for(self.keeper_user)

    def test_a_timekeeper_assigns_a_substitute_inside_his_department(self):
        response = self.patch(client=self.keeper, is_active='true', work_status='vacation', replacement_employee_id=self.sub.pk)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.record().substitute, self.sub)
        self.assertEqual(self.record().assigned_by, self.keeper_user)

    def test_a_timekeeper_cannot_pick_a_substitute_from_another_department(self):
        response = self.patch(client=self.keeper, is_active='true', work_status='vacation', replacement_employee_id=self.stranger.pk)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIsNone(self.record())

    def test_a_timekeeper_gets_the_candidates_of_his_own_department_only(self):
        response = self.keeper.get(CANDIDATES.format(self.absent.pk), {'department_id': self.dept_b.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['department']['id'], self.dept_a.pk)   # ?department_id инобатга олинмайди
        self.assertNotIn(self.stranger.pk, [item['id'] for item in response.json()['data']])
        self.assertNotIn(self.absent.pk, [item['id'] for item in response.json()['data']])

    def test_a_timekeeper_cannot_touch_another_department(self):
        self.assertEqual(self.keeper.get(CANDIDATES.format(self.stranger.pk)).status_code, 404)

        response = self.patch(employee=self.stranger, client=self.keeper, is_active='true', work_status='vacation')

        self.assertEqual(response.status_code, 403)


class ReplacementPageTests(ReplacementTestBase):
    def setUp(self):
        super().setUp()
        self.web = Client(HTTP_HOST='localhost')
        self.web.force_login(self.admin)

    def edit_html(self, employee=None, client=None):
        return (client or self.web).get(f'/employees/{(employee or self.absent).slug}/edit/').content.decode()

    def test_edit_page_opens_the_modal_when_the_status_changes(self):
        html = self.edit_html()

        for expected in ('id="replacementModal"', 'id="replacementSearch"', 'id="replacementList"', 'id="replacementConfirmButton"',
                         'id="replacementSkipButton"', 'id="replacementSummary"', 'onchange="onEmployeeStatusChange()"',
                         'function onEmployeeStatusChange()', 'function confirmReplacement()', 'function skipReplacement()',
                         '/replacement-candidates/?department_id=', "formData.append('replacement_employee_id', replacementValue);"):
            self.assertIn(expected, html)
        self.assertIn("if (document.getElementById('employee_status').value !== 'working') {\n            openReplacementModal();", html)   # «Фаол»дан бошқа ҳар қандай ҳолатда
        self.assertIn('onReplacementDepartmentChange()', html)   # цех ўзгарса, танлов бекор бўлади

    def test_summary_is_hidden_without_a_substitute_and_shows_the_name_with_one(self):
        before = self.edit_html()
        self.assertIn('id="replacementSummary" class="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 hidden"', before)
        self.assertIn("id: '',", before)

        self.go_on_leave(self.sub)
        after = self.edit_html()

        self.assertIn('id="replacementSummary" class="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"', after)
        self.assertIn(f'{self.sub} · 102', after)
        self.assertIn(f"id: '{self.sub.pk}',", after)

    def test_view_page_shows_the_substitute_read_only(self):
        self.go_on_leave(self.sub)

        html = self.web.get(f'/employees/{self.absent.slug}/').content.decode()

        self.assertIn(f'{self.sub} · 102', html)
        self.assertIn('Ўрнига ишлайди', html)
        self.assertNotIn('id="replacementModal"', html)
        self.assertNotIn('onclick="openReplacementModal()"', html)

    def test_a_plain_employee_editing_only_sizes_gets_no_modal(self):
        make_user('plain_page', ROLE_USER, employee=self.sub)
        client = Client(HTTP_HOST='localhost')
        client.force_login(User.objects.get(username='plain_page'))

        html = client.get(f'/employees/{self.sub.slug}/').content.decode()

        self.assertNotIn('id="replacementModal"', html)

    def test_a_timekeeper_gets_the_modal_on_the_card_of_his_department(self):
        keeper_employee = make_employee(110, self.dept_a)
        Timekeeper.objects.create(department=self.dept_a, employee=keeper_employee)
        client = Client(HTTP_HOST='localhost')
        client.force_login(make_user('rep_keeper_page', ROLE_TIMEKEEPER, employee=keeper_employee))

        html = self.edit_html(client=client)

        self.assertIn('id="replacementModal"', html)
