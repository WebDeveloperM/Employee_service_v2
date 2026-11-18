"""Бўлим штатсиз («Шогиртлар»): ходимлар рўйхатга қўшилади, лекин штат ва бўш иш ўринларига кирмайди."""
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from .dashboard_stats import staff_chart
from .models import Employee, Section
from .staff import collect_staff_stats, is_no_staff_section_name
from .test_staff import POSITIONS_URL, TREE_URL, StaffTestCase


LOOKUP_URL = '/api/template/staff/employee-lookup/'


class NoStaffSectionNameTests(SimpleTestCase):
    def test_apprentice_names_are_recognised_regardless_of_case_and_spaces(self):
        for name in ('Шогиртлар', 'шогиртлар', '  ШОГИРТЛАР ', 'Шогирдлар', 'Shogirtlar', 'Шогиртлар   бўлими'):
            with self.subTest(name=name):
                self.assertTrue(is_no_staff_section_name(name))

    def test_other_sections_have_staff(self):
        for name in ('Юридик бўлим', 'Маркетинг ва бозор', 'Устоз ва шогиртлар мактаби', '', None):
            with self.subTest(name=name):
                self.assertFalse(is_no_staff_section_name(name))


class ApprenticeTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.apprentices = Section.objects.create(department=self.department, name='Шогиртлар', code='17')

    def employees_url(self, section=None):
        return f'/api/template/staff/sections/{(section or self.apprentices).pk}/employees/'

    def member_url(self, employee, section=None):
        return f'{self.employees_url(section)}{employee.pk}/'


class ApprenticeUnitTests(ApprenticeTestCase):
    def test_tree_marks_only_the_apprentice_section(self):
        nodes = {}
        for department in self.client.get(TREE_URL).json()['data']:
            for section in department['children']:
                nodes[section['name']] = section

        self.assertTrue(nodes['Шогиртлар']['no_staff'])
        self.assertFalse(nodes['Юридик бўлим']['no_staff'])

    def test_positions_list_reports_no_staff_unit_with_zero_totals(self):
        body = self.client.get(POSITIONS_URL, {'section_id': self.apprentices.pk}).json()

        self.assertTrue(body['unit']['no_staff'])
        self.assertEqual((body['summary']['own_staff'], body['summary']['own_vacant']), (0, 0))
        regular = self.client.get(POSITIONS_URL, {'section_id': self.section.pk}).json()
        self.assertFalse(regular['unit']['no_staff'])

    def test_a_position_cannot_be_added_to_the_apprentice_section(self):
        response = self.client.post(POSITIONS_URL, {'section_id': self.apprentices.pk, 'name': 'Шогирд', 'staff_count': 1}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.apprentices.staff_positions.count(), 0)

    def test_positions_can_still_be_added_to_other_sections(self):
        response = self.client.post(POSITIONS_URL, {'section_id': self.section.pk, 'name': 'Юрист', 'staff_count': 1}, format='json')

        self.assertEqual(response.status_code, 201, response.content)

    def test_old_positions_of_the_apprentice_section_do_not_count_in_any_total(self):
        self.make_position('Шогирд', staff_count=3, section=self.apprentices)
        self.make_position('Слесарь', staff_count=2)
        self.make_employee('Слесарь', department=self.department)

        stats = collect_staff_stats()
        tree = {item['name']: item for item in self.client.get(TREE_URL).json()['data']}
        dashboard = {row['label']: row['value'] for row in staff_chart()['rows']}

        self.assertNotIn(('section', self.apprentices.pk), stats.own)
        self.assertEqual((tree['1 цех']['staff_total'], tree['1 цех']['vacant_total']), (2, 1))
        self.assertEqual((dashboard['Штат бирлиги'], dashboard['Бўш иш ўринлари']), (2, 1))


class ApprenticeEmployeesTests(ApprenticeTestCase):
    def test_list_shows_every_active_employee_of_the_section_whatever_the_position(self):
        one = self.make_employee('Шогирд слесарь', department=self.department, section=self.apprentices)
        two = self.make_employee('', department=self.department, section=self.apprentices)
        self.make_employee('Юрист', department=self.department, section=self.section)
        self.make_employee('Слесарь', department=self.department)
        self.make_employee('Шогирд', department=self.department, section=self.apprentices, is_deleted=True)
        self.make_employee('Шогирд', department=self.department, section=self.apprentices, is_active=False)

        body = self.client.get(self.employees_url()).json()

        self.assertEqual({item['id'] for item in body['data']}, {one.pk, two.pk})
        self.assertEqual(body['count'], 2)
        self.assertEqual(body['unit']['name'], 'Шогиртлар')
        self.assertTrue(body['unit']['no_staff'])
        self.assertEqual({item['id']: item['position'] for item in body['data']}[one.pk], 'Шогирд слесарь')

    def test_list_of_a_section_with_staff_is_rejected(self):
        self.assertEqual(self.client.get(self.employees_url(self.section)).status_code, 400)
        self.assertEqual(self.client.get('/api/template/staff/sections/999999/employees/').status_code, 404)

    def test_add_moves_the_employee_into_the_section_and_keeps_the_position_text(self):
        employee = self.make_employee('Слесарь', department=self.other_department)

        response = self.client.post(self.employees_url(), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.department_id, employee.section_id, employee.position), (self.department.pk, self.apprentices.pk, 'Слесарь'))
        self.assertEqual([item['id'] for item in response.json()['data']], [employee.pk])
        self.assertEqual(response.json()['count'], 1)

    def test_add_works_without_any_position_or_department(self):
        employee = self.make_employee('')

        response = self.client.post(self.employees_url(), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.department_id, employee.section_id), (self.department.pk, self.apprentices.pk))

    def test_the_same_employee_cannot_be_added_twice(self):
        employee = self.make_employee('Шогирд', department=self.department, section=self.apprentices)

        response = self.client.post(self.employees_url(), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)

    def test_add_is_not_limited_by_any_staff_number(self):
        employees = [self.make_employee('', department=self.department) for _ in range(5)]

        statuses = [self.client.post(self.employees_url(), {'employee_id': item.pk}, format='json').status_code for item in employees]

        self.assertEqual(statuses, [201] * 5)
        self.assertEqual(len(self.client.get(self.employees_url()).json()['data']), 5)

    def test_add_rejects_unknown_deleted_inactive_and_missing_employees(self):
        deleted = self.make_employee('', is_deleted=True)
        inactive = self.make_employee('', is_active=False)

        self.assertEqual(self.client.post(self.employees_url(), {'employee_id': deleted.pk}, format='json').status_code, 404)
        self.assertEqual(self.client.post(self.employees_url(), {'employee_id': inactive.pk}, format='json').status_code, 404)
        self.assertEqual(self.client.post(self.employees_url(), {'employee_id': 999999}, format='json').status_code, 404)
        self.assertEqual(self.client.post(self.employees_url(), {}, format='json').status_code, 400)

    def test_add_to_a_section_with_staff_is_rejected_and_changes_nothing(self):
        employee = self.make_employee('Слесарь', department=self.other_department)

        response = self.client.post(self.employees_url(self.section), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.department_id, employee.section_id), (self.other_department.pk, None))

    def test_moving_an_employee_out_of_a_staffed_position_frees_the_place_there(self):
        self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)
        self.assertEqual(collect_staff_stats().own[('department', self.department.pk)]['vacant'], 0)

        self.client.post(self.employees_url(), {'employee_id': employee.pk}, format='json')

        self.assertEqual(collect_staff_stats().own[('department', self.department.pk)]['vacant'], 1)
        self.assertNotIn(('section', self.apprentices.pk), collect_staff_stats().own)

    def test_apprentices_do_not_take_places_of_a_same_name_position_elsewhere(self):
        self.make_position('Слесарь', staff_count=1)
        self.make_employee('Слесарь', department=self.department, section=self.apprentices)

        self.assertEqual(collect_staff_stats().own[('department', self.department.pk)]['vacant'], 1)

    def test_remove_clears_only_the_section(self):
        employee = self.make_employee('Шогирд слесарь', department=self.department, section=self.apprentices)
        other = self.make_employee('Шогирд', department=self.department, section=self.apprentices)

        response = self.client.delete(self.member_url(employee))

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.department_id, employee.section_id, employee.position, employee.is_active, employee.is_deleted),
                         (self.department.pk, None, 'Шогирд слесарь', True, False))
        self.assertEqual([item['id'] for item in response.json()['data']], [other.pk])

    def test_remove_of_an_employee_who_is_not_in_the_section_is_404(self):
        outsider = self.make_employee('Юрист', department=self.department, section=self.section)

        response = self.client.delete(self.member_url(outsider))

        self.assertEqual(response.status_code, 404, response.content)
        outsider.refresh_from_db()
        self.assertEqual(outsider.section_id, self.section.pk)

    def test_remove_through_a_section_with_staff_is_rejected(self):
        employee = self.make_employee('Юрист', department=self.department, section=self.section)

        self.assertEqual(self.client.delete(self.member_url(employee, self.section)).status_code, 400)
        employee.refresh_from_db()
        self.assertEqual(employee.section_id, self.section.pk)


class ApprenticeAccessAndLookupTests(ApprenticeTestCase):
    def test_only_admin_and_hr_can_use_the_endpoints(self):
        employee = self.make_employee('', department=self.department, section=self.apprentices)
        for user, expected in ((self.hr, 200), (self.regular, 403)):
            with self.subTest(role=user.profile.role):
                client = APIClient()
                client.force_authenticate(user=user)
                self.assertEqual(client.get(self.employees_url()).status_code, expected)
                self.assertEqual(client.post(self.employees_url(), {'employee_id': employee.pk}, format='json').status_code, 400 if expected == 200 else 403)
                self.assertEqual(client.delete(self.member_url(employee)).status_code, expected)

    def test_anonymous_user_is_refused(self):
        client = APIClient()

        self.assertIn(client.get(self.employees_url()).status_code, (401, 403))

    def test_lookup_marks_employees_who_are_already_in_the_section(self):
        inside = self.make_employee('', department=self.department, section=self.apprentices)
        outside = self.make_employee('', department=self.department)

        data = self.client.get(LOOKUP_URL, {'tabel_number': 'T-', 'section_id': self.apprentices.pk}).json()['data']
        flags = {item['id']: item['in_position'] for item in data}

        self.assertEqual((flags[inside.pk], flags[outside.pk]), (True, False))
        self.assertEqual(Employee.objects.get(pk=outside.pk).section_id, None)
