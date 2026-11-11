from rest_framework.test import APIClient

from .models import Employee, Section
from .test_staff import POSITIONS_URL, StaffTestCase


LOOKUP_URL = '/api/template/staff/employee-lookup/'


class StaffPositionEmployeesTests(StaffTestCase):
    def employees_url(self, position):
        return f'{POSITIONS_URL}{position.pk}/employees/'

    def test_list_returns_active_employees_of_the_same_unit_and_position(self):
        position = self.make_position('Слесарь', staff_count=5)
        inside = self.make_employee('  слесарь ', department=self.department, login='slesar1')
        self.make_employee('Слесарь', department=self.department, is_active=False)
        self.make_employee('Слесарь', department=self.department, is_deleted=True)
        self.make_employee('Слесарь', department=self.other_department)
        self.make_employee('Слесарь', department=self.department, section=self.section)
        self.make_employee('Токарь', department=self.department)

        response = self.client.get(self.employees_url(position))

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([item['id'] for item in body['data']], [inside.pk])
        self.assertEqual(body['data'][0]['tabel_number'], inside.tabel_number)
        self.assertEqual(body['data'][0]['login'], 'slesar1')
        self.assertEqual(body['data'][0]['position'], 'слесарь')
        self.assertEqual(body['data'][0]['department'], '1 цех')
        self.assertEqual(body['position'], {
            'id': position.pk, 'name': 'Слесарь', 'unit_name': '1 цех', 'staff_count': 5, 'occupied': 1, 'vacant': 4,
        })

    def test_list_matches_the_occupied_count_of_the_positions_list(self):
        position = self.make_position('Юрист', staff_count=3, section=self.section)
        self.make_employee('Юрист', department=self.department, section=self.section)
        self.make_employee('Юрист', department=self.department, section=self.section)

        employees = self.client.get(self.employees_url(position)).json()
        listing = self.client.get(f'{POSITIONS_URL}?section_id={self.section.pk}').json()['data'][0]

        self.assertEqual(len(employees['data']), listing['occupied'])
        self.assertEqual(employees['position']['vacant'], listing['vacant'])

    def test_assign_moves_employee_to_the_position_and_changes_nothing_else(self):
        position = self.make_position('Слесарь', staff_count=2)
        old_section = Section.objects.create(department=self.other_department, name='Eski bo`lim')
        employee = self.make_employee('Токарь', department=self.other_department, section=old_section, login='old-login')
        before = Employee.objects.filter(pk=employee.pk).values().get()

        response = self.client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual(employee.department_id, self.department.pk)
        self.assertIsNone(employee.section_id)
        self.assertEqual(employee.position, 'Слесарь')
        after = Employee.objects.filter(pk=employee.pk).values().get()
        changed = {key for key in before if before[key] != after[key]}
        self.assertEqual(changed, {'department_id', 'section_id', 'position', 'updated_at'})

        body = response.json()
        self.assertEqual([item['id'] for item in body['data']], [employee.pk])
        self.assertEqual(body['position']['occupied'], 1)
        self.assertEqual(body['position']['vacant'], 1)

    def test_assign_to_a_section_position_sets_section_and_its_department(self):
        position = self.make_position('Юрист', staff_count=1, section=self.section)
        employee = self.make_employee('', department=None, section=None)

        response = self.client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.department_id, employee.section_id, employee.position), (self.department.pk, self.section.pk, 'Юрист'))

    def test_assigned_employee_shows_up_in_the_positions_list_counts(self):
        position = self.make_position('Слесарь', staff_count=2)
        employee = self.make_employee('', department=None)

        self.client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json')
        row = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').json()['data'][0]

        self.assertEqual((row['occupied'], row['vacant']), (1, 1))

    def test_assign_is_rejected_when_there_is_no_vacancy(self):
        position = self.make_position('Слесарь', staff_count=1)
        self.make_employee('Слесарь', department=self.department)
        newcomer = self.make_employee('Токарь', department=self.other_department)

        response = self.client.post(self.employees_url(position), {'employee_id': newcomer.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        newcomer.refresh_from_db()
        self.assertEqual((newcomer.department_id, newcomer.position), (self.other_department.pk, 'Токарь'))

    def test_assign_is_rejected_for_employee_already_in_the_position(self):
        position = self.make_position('Слесарь', staff_count=3)
        employee = self.make_employee('Слесарь', department=self.department)

        response = self.client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)

    def test_assign_rejects_missing_inactive_and_deleted_employees(self):
        position = self.make_position('Слесарь', staff_count=3)
        inactive = self.make_employee('', is_active=False)
        deleted = self.make_employee('', is_deleted=True)
        url = self.employees_url(position)

        self.assertEqual(self.client.post(url, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'employee_id': 'abc'}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'employee_id': 999999}, format='json').status_code, 404)
        self.assertEqual(self.client.post(url, {'employee_id': inactive.pk}, format='json').status_code, 404)
        self.assertEqual(self.client.post(url, {'employee_id': deleted.pk}, format='json').status_code, 404)

    def test_unknown_position_is_404(self):
        self.assertEqual(self.client.get(f'{POSITIONS_URL}999999/employees/').status_code, 404)

    def test_regular_user_cannot_list_assign_or_search(self):
        position = self.make_position('Слесарь', staff_count=3)
        employee = self.make_employee('', department=None)
        client = APIClient()
        client.force_authenticate(user=self.regular)

        self.assertEqual(client.get(self.employees_url(position)).status_code, 403)
        self.assertEqual(client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json').status_code, 403)
        self.assertEqual(client.get(f'{LOOKUP_URL}?tabel_number=T').status_code, 403)
        employee.refresh_from_db()
        self.assertEqual(employee.position, '')


class StaffEmployeeLookupTests(StaffTestCase):
    def lookup(self, query):
        response = self.client.get(f'{LOOKUP_URL}?{query}')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']

    def test_empty_term_returns_nothing(self):
        self.make_employee('Слесарь', department=self.department)

        self.assertEqual(self.lookup('tabel_number='), [])
        self.assertEqual(self.lookup('tabel_number=%20%20'), [])

    def test_exact_match_comes_first_then_partial_and_limit_is_ten(self):
        for number in ('1234', '12', '120', '312', '99'):
            Employee.objects.create(first_name='A', last_name=f'L{number}', tabel_number=number)
        for index in range(15):
            Employee.objects.create(first_name='B', last_name=f'M{index}', tabel_number=f'Q-{chr(65 + index)}')

        exact_first = [item['tabel_number'] for item in self.lookup('tabel_number=12')]
        capped = self.lookup('tabel_number=Q-')

        self.assertEqual(exact_first, ['12', '120', '1234', '312'])
        self.assertEqual(len(capped), 10)

    def test_deleted_and_inactive_employees_are_not_found(self):
        Employee.objects.create(first_name='A', last_name='Active', tabel_number='500')
        Employee.objects.create(first_name='A', last_name='Inactive', tabel_number='501', is_active=False)
        Employee.objects.create(first_name='A', last_name='Deleted', tabel_number='502', is_deleted=True)

        self.assertEqual([item['tabel_number'] for item in self.lookup('tabel_number=50')], ['500'])

    def test_result_describes_current_assignment_and_flags_employees_already_in_position(self):
        position = self.make_position('Слесарь', staff_count=3)
        here = self.make_employee('Слесарь', department=self.department)
        elsewhere = self.make_employee('Юрист', department=self.department, section=self.section)

        results = {item['id']: item for item in self.lookup(f'tabel_number=T-&position_id={position.pk}')}

        self.assertTrue(results[here.pk]['in_position'])
        self.assertFalse(results[elsewhere.pk]['in_position'])
        self.assertEqual(
            (results[elsewhere.pk]['department'], results[elsewhere.pk]['section'], results[elsewhere.pk]['position']),
            ('1 цех', 'Юридик бўлим', 'Юрист'),
        )
        self.assertIn('full_name', results[here.pk])
        self.assertIn('photo', results[here.pk])

    def test_unknown_position_id_is_404(self):
        self.assertEqual(self.client.get(f'{LOOKUP_URL}?tabel_number=1&position_id=999999').status_code, 404)
