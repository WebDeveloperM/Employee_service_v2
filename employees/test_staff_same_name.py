"""Бир цех/бўлимда бир хил номли штат лавозимлари: банд жойлар қаторлар ўртасида такрор саналмай тақсимланади."""
from decimal import Decimal

from django.test import SimpleTestCase

from .models import Employee
from .staff import collect_staff_stats, employees_of_position, split_occupancy
from .test_staff import POSITIONS_URL, StaffTestCase


LOOKUP_URL = '/api/template/staff/employee-lookup/'


class SplitOccupancyTests(SimpleTestCase):
    def test_a_single_position_takes_everything(self):
        self.assertEqual(split_occupancy([1], 0), [0])
        self.assertEqual(split_occupancy([1], 3), [3])   # штатдан ортиғи ҳам шу қаторда
        self.assertEqual(split_occupancy([Decimal('0.5')], 2), [2])

    def test_places_fill_in_order_and_the_rest_goes_to_the_last_row(self):
        self.assertEqual(split_occupancy([1, 1], 0), [0, 0])
        self.assertEqual(split_occupancy([1, 1], 1), [1, 0])
        self.assertEqual(split_occupancy([1, 1], 2), [1, 1])
        self.assertEqual(split_occupancy([1, 1], 3), [1, 2])
        self.assertEqual(split_occupancy([2, 1], 2), [2, 0])
        self.assertEqual(split_occupancy([1, 1, 1], 2), [1, 1, 0])

    def test_fractional_staff_counts_are_rounded_up_like_the_assign_check(self):
        self.assertEqual(split_occupancy([Decimal('0.5'), Decimal('1')], 1), [1, 0])
        self.assertEqual(split_occupancy([Decimal('1.5'), Decimal('1')], 2), [2, 0])
        self.assertEqual(split_occupancy([Decimal('1.5'), Decimal('1')], 3), [2, 1])


class SameNamePositionTests(StaffTestCase):
    def rows(self, **params):
        query = params or {'department_id': self.department.pk}
        return self.client.get(POSITIONS_URL, query).json()

    def employees_url(self, position):
        return f'{POSITIONS_URL}{position.pk}/employees/'

    def test_second_row_gets_the_vacancy_instead_of_double_counting_the_employee(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('слесарь ', staff_count=1)
        self.make_employee('Слесарь', department=self.department)

        body = self.rows()

        self.assertEqual([(row['id'], row['occupied'], row['vacant']) for row in body['data']], [(first.pk, 1, 0), (second.pk, 0, 1)])
        self.assertEqual((body['summary']['own_staff'], body['summary']['own_vacant']), (2, 1))

    def test_employees_above_the_staff_end_up_on_the_last_row(self):
        self.make_position('Слесарь', staff_count=1)
        self.make_position('Слесарь', staff_count=1)
        for _ in range(3):
            self.make_employee('Слесарь', department=self.department)

        body = self.rows()

        self.assertEqual([(row['occupied'], row['vacant']) for row in body['data']], [(1, 0), (2, 0)])
        self.assertEqual(body['summary']['own_vacant'], 0)

    def test_fractional_rows_share_employees_too(self):
        self.make_position('Слесарь', staff_count=Decimal('0.5'))
        self.make_position('Слесарь', staff_count=1)
        self.make_employee('Слесарь', department=self.department)

        body = self.rows()

        self.assertEqual([(row['occupied'], row['vacant']) for row in body['data']], [(1, 0), (0, 1)])
        self.assertEqual(body['summary']['own_vacant'], 1)

    def test_rows_of_different_units_or_names_do_not_share(self):
        self.make_position('Слесарь', staff_count=1)
        in_section = self.make_position('Слесарь', staff_count=1, section=self.section)
        self.make_position('Токарь', staff_count=1)
        self.make_employee('Слесарь', department=self.department)

        stats = collect_staff_stats()

        self.assertEqual(stats.occupied_for(in_section), 0)
        self.assertEqual(self.rows(section_id=self.section.pk)['data'][0]['vacant'], 1)

    def test_employee_lists_are_split_like_the_counts_and_do_not_overlap(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        one = self.make_employee('Слесарь', department=self.department)
        two = self.make_employee('Слесарь', department=self.department)

        first_body = self.client.get(self.employees_url(first)).json()
        second_body = self.client.get(self.employees_url(second)).json()

        self.assertEqual([item['id'] for item in first_body['data']], [one.pk])
        self.assertEqual([item['id'] for item in second_body['data']], [two.pk])
        for body, row in ((first_body, self.rows()['data'][0]), (second_body, self.rows()['data'][1])):
            self.assertEqual(len(body['data']), row['occupied'])
            self.assertEqual(body['position']['vacant'], row['vacant'])
        self.assertEqual([item.pk for item in employees_of_position(first)], [one.pk])

    def test_unit_positions_list_shows_the_vacancy_of_each_row(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        self.make_employee('Слесарь', department=self.department)

        options = self.client.get(self.employees_url(first)).json()['unit_positions']

        self.assertEqual({item['id']: item['vacant'] for item in options}, {first.pk: 0, second.pk: 1})

    def test_assign_to_the_full_row_is_rejected_but_the_row_with_room_accepts(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        self.make_employee('Слесарь', department=self.department)
        newcomer = self.make_employee('Токарь', department=self.other_department)

        rejected = self.client.post(self.employees_url(first), {'employee_id': newcomer.pk}, format='json')
        accepted = self.client.post(self.employees_url(second), {'employee_id': newcomer.pk}, format='json')

        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertEqual(accepted.status_code, 201, accepted.content)
        self.assertEqual([(row['occupied'], row['vacant']) for row in self.rows()['data']], [(1, 0), (1, 0)])
        self.assertEqual(accepted.json()['position']['occupied'], 1)

    def test_assign_is_rejected_for_an_employee_who_is_already_in_another_same_name_row(self):
        self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)

        response = self.client.post(self.employees_url(second), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)

    def test_transfer_between_same_name_rows_is_rejected_and_changes_nothing(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)
        before = Employee.objects.filter(pk=employee.pk).values().get()

        response = self.client.patch(
            f'{POSITIONS_URL}{first.pk}/employees/{employee.pk}/', {'position_id': second.pk}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Employee.objects.filter(pk=employee.pk).values().get(), before)

    def test_lookup_flags_an_employee_from_any_same_name_row(self):
        self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)

        data = self.client.get(LOOKUP_URL, {'tabel_number': 'T-', 'position_id': second.pk}).json()['data']

        self.assertTrue({item['id']: item for item in data}[employee.pk]['in_position'])

    def test_removing_an_employee_frees_the_place_and_the_rest_move_up(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        one = self.make_employee('Слесарь', department=self.department)
        two = self.make_employee('Слесарь', department=self.department)

        response = self.client.delete(f'{POSITIONS_URL}{first.pk}/employees/{one.pk}/')

        self.assertEqual(response.status_code, 200, response.content)
        one.refresh_from_db()
        self.assertEqual(one.position, '')
        self.assertEqual([(row['id'], row['occupied'], row['vacant']) for row in self.rows()['data']], [(first.pk, 1, 0), (second.pk, 0, 1)])
        self.assertEqual([item.pk for item in employees_of_position(first)], [two.pk])
