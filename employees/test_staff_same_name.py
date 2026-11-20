"""Бир цех/бўлимда бир хил номли штат лавозимлари: банд жойлар қаторлар ўртасида такрор саналмай тақсимланади."""
import importlib
from decimal import Decimal

from django.test import SimpleTestCase

from .models import Employee, Section
from .staff import collect_staff_stats, distribute_unbound, employees_of_position, split_occupancy
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

    def test_unbound_employees_take_only_the_places_left_by_bound_ones(self):
        self.assertEqual(distribute_unbound([2, 2], [0, 2], 2), [2, 0])
        self.assertEqual(distribute_unbound([2, 2], [2, 0], 1), [0, 1])
        self.assertEqual(distribute_unbound([1, 1], [2, 0], 1), [0, 1])    # штатдан ортиқ бириктирилган — бўш жой манфий эмас
        self.assertEqual(distribute_unbound([1, 1], [0, 1], 3), [1, 2])    # ортиғи — охирги қаторда (аввалгидек)
        self.assertEqual(distribute_unbound([2, 2], [1, 1], 0), [0, 0])

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

    def ids(self, position):
        return [item['id'] for item in self.client.get(self.employees_url(position)).json()['data']]

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

    def test_adding_an_employee_of_another_same_name_row_moves_him_to_this_row(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)
        self.assertEqual(self.ids(first), [employee.pk])

        response = self.client.post(self.employees_url(second), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((self.ids(first), self.ids(second)), ([], [employee.pk]))
        self.assertEqual([(row['occupied'], row['vacant']) for row in self.rows()['data']], [(0, 1), (1, 0)])

    def test_adding_the_same_employee_to_the_same_row_again_is_rejected(self):
        self.make_position('Слесарь', staff_count=2)
        second = self.make_position('Слесарь', staff_count=2)
        employee = self.make_employee('Токарь', department=self.other_department)
        self.client.post(self.employees_url(second), {'employee_id': employee.pk}, format='json')

        response = self.client.post(self.employees_url(second), {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)

    def test_transfer_between_same_name_rows_moves_exactly_that_employee(self):
        first = self.make_position('Слесарь', staff_count=2)
        second = self.make_position('Слесарь', staff_count=2)
        one = self.make_employee('Токарь', department=self.other_department)
        two = self.make_employee('Токарь', department=self.other_department)
        for employee in (one, two):
            self.client.post(self.employees_url(first), {'employee_id': employee.pk}, format='json')

        response = self.client.patch(
            f'{POSITIONS_URL}{first.pk}/employees/{two.pk}/', {'position_id': second.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((self.ids(first), self.ids(second)), ([one.pk], [two.pk]))
        two.refresh_from_db()
        self.assertEqual((two.position, two.staff_position_id, two.department_id), ('Слесарь', second.pk, self.department.pk))

    def test_transfer_to_a_full_same_name_row_is_rejected(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        one = self.make_employee('Токарь', department=self.other_department)
        two = self.make_employee('Токарь', department=self.other_department)
        self.client.post(self.employees_url(first), {'employee_id': one.pk}, format='json')
        self.client.post(self.employees_url(second), {'employee_id': two.pk}, format='json')

        response = self.client.patch(f'{POSITIONS_URL}{first.pk}/employees/{one.pk}/', {'position_id': second.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual((self.ids(first), self.ids(second)), ([one.pk], [two.pk]))

    def test_lookup_flags_only_employees_of_this_row(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)

        flags = {
            row.pk: {item['id']: item for item in self.client.get(LOOKUP_URL, {'tabel_number': 'T-', 'position_id': row.pk}).json()['data']}[employee.pk]['in_position']
            for row in (first, second)
        }

        self.assertEqual(flags, {first.pk: True, second.pk: False})     # иккинчи қаторга кўчириш мумкин

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


class SameNamePositionBindingTests(StaffTestCase):
    """Ходим ўзи қўшилган қаторда қолади (Employee.staff_position): бир хил номли биринчи қаторга «ўтиб кетмайди»."""

    def employees_url(self, position):
        return f'{POSITIONS_URL}{position.pk}/employees/'

    def ids(self, position):
        return [item['id'] for item in self.client.get(self.employees_url(position)).json()['data']]

    def add(self, position, employee):
        response = self.client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def rows(self):
        return [(row['occupied'], row['vacant']) for row in self.client.get(POSITIONS_URL, {'department_id': self.department.pk}).json()['data']]

    def newcomer(self):
        return self.make_employee('Токарь', department=self.other_department)

    def test_employees_added_to_the_second_row_stay_there_even_if_the_first_row_has_room(self):
        # Хато: иккинчи «Электрмонтер»га қўшилган ходимлар биринчисига ўтиб кетарди.
        first = self.make_position('Электрмонтер', staff_count=8)
        second = self.make_position('Электрмонтер', staff_count=8)
        added = [self.newcomer() for _ in range(3)]

        for employee in added:
            body = self.add(second, employee)

        self.assertEqual(sorted(item['id'] for item in body['data']), sorted(item.pk for item in added))
        self.assertEqual((self.ids(first), sorted(self.ids(second))), ([], sorted(item.pk for item in added)))
        self.assertEqual(self.rows(), [(0, 8), (3, 5)])

    def test_each_row_keeps_its_own_employees_whatever_the_order_of_adding(self):
        first = self.make_position('Электрмонтер', staff_count=2)
        second = self.make_position('Электрмонтер', staff_count=2)
        third = self.make_position('Электрмонтер', staff_count=1)
        a, b, c, d = (self.newcomer() for _ in range(4))

        self.add(third, a)
        self.add(second, b)
        self.add(first, c)
        self.add(second, d)

        self.assertEqual((self.ids(first), sorted(self.ids(second)), self.ids(third)), ([c.pk], sorted([b.pk, d.pk]), [a.pk]))
        self.assertEqual(self.rows(), [(1, 1), (2, 0), (1, 0)])

    def test_a_full_row_does_not_accept_more_and_the_others_are_not_touched(self):
        first = self.make_position('Электрмонтер', staff_count=1)
        second = self.make_position('Электрмонтер', staff_count=1)
        self.add(second, self.newcomer())

        response = self.client.post(self.employees_url(second), {'employee_id': self.newcomer().pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.rows(), [(0, 1), (1, 0)])
        self.assertEqual(self.ids(first), [])

    def test_unbound_employees_fill_the_places_left_by_the_bound_ones(self):
        # Лавозими карточкада ёзилган (саҳифа орқали қўшилмаган) ходимлар бўш жойларни тартиб билан эгаллайди.
        first = self.make_position('Электрмонтер', staff_count=2)
        second = self.make_position('Электрмонтер', staff_count=2)
        bound = self.newcomer()
        self.add(first, bound)
        typed = [self.make_employee('электрмонтер ', department=self.department) for _ in range(2)]

        self.assertEqual(self.rows(), [(2, 0), (1, 1)])
        self.assertIn(bound.pk, self.ids(first))
        listed = self.ids(first) + self.ids(second)
        self.assertEqual(sorted(listed), sorted([bound.pk] + [employee.pk for employee in typed]))   # ҳеч ким такрорланмайди ва йўқолмайди
        self.assertEqual(collect_staff_stats().occupied_for(second), 1)

    def test_a_binding_stops_counting_once_the_card_moves_the_employee_elsewhere(self):
        first = self.make_position('Электрмонтер', staff_count=1)
        second = self.make_position('Электрмонтер', staff_count=1)
        employee = self.newcomer()
        self.add(second, employee)

        Employee.objects.filter(pk=employee.pk).update(position='Токарь')     # карточкада лавозим ўзгартирилди

        self.assertEqual(self.rows(), [(0, 1), (0, 1)])
        self.assertEqual((self.ids(first), self.ids(second)), ([], []))

    def test_removing_clears_the_binding(self):
        self.make_position('Электрмонтер', staff_count=1)
        second = self.make_position('Электрмонтер', staff_count=1)
        employee = self.newcomer()
        self.add(second, employee)

        response = self.client.delete(f'{POSITIONS_URL}{second.pk}/employees/{employee.pk}/')

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.position, employee.staff_position_id), ('', None))
        self.assertEqual(self.rows(), [(0, 1), (0, 1)])

    def test_deleting_a_row_returns_its_employees_to_the_same_name_rows(self):
        first = self.make_position('Электрмонтер', staff_count=2)
        second = self.make_position('Электрмонтер', staff_count=2)
        employee = self.newcomer()
        self.add(second, employee)

        second.delete()

        employee.refresh_from_db()
        self.assertIsNone(employee.staff_position_id)
        self.assertEqual(self.ids(first), [employee.pk])        # лавозим номи сақланган — ходим йўқолмайди

    def test_moving_to_a_no_staff_section_clears_the_binding(self):
        apprentices = Section.objects.create(department=self.department, name='Шогиртлар')
        self.make_position('Электрмонтер', staff_count=1)
        second = self.make_position('Электрмонтер', staff_count=1)
        employee = self.newcomer()
        self.add(second, employee)

        response = self.client.post(f'/api/template/staff/sections/{apprentices.pk}/employees/', {'employee_id': employee.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.section_id, employee.staff_position_id), (apprentices.pk, None))

    def test_single_name_rows_work_as_before(self):
        only = self.make_position('Токарь', staff_count=2)
        typed = self.make_employee('Токарь', department=self.department)
        added = self.newcomer()

        self.add(only, added)

        self.assertEqual(sorted(self.ids(only)), sorted([typed.pk, added.pk]))
        self.assertEqual(self.rows(), [(2, 0)])


class FreezeExistingSplitMigrationTests(StaffTestCase):
    """0035 миграцияси: ҳозир кўриниб турган тақсимот сақланади, кейин ходимни аниқ кўчириш мумкин."""

    def freeze(self):
        from django.apps import apps
        migration = importlib.import_module('employees.migrations.0035_employee_staff_position')
        migration.bind_same_name_rows(apps, None)

    def test_the_current_split_is_kept_and_written_as_bindings(self):
        first = self.make_position('Электрмонтер', staff_count=2)
        second = self.make_position('Электрмонтер', staff_count=2)
        single = self.make_position('Токарь', staff_count=1)
        staff = [self.make_employee('Электрмонтер', department=self.department) for _ in range(3)]
        turner = self.make_employee('Токарь', department=self.department)
        before = {row.pk: [item.pk for item in employees_of_position(row)] for row in (first, second, single)}

        self.freeze()

        after = {row.pk: [item.pk for item in employees_of_position(row)] for row in (first, second, single)}
        self.assertEqual(after, before)
        bindings = dict(Employee.objects.filter(pk__in=[item.pk for item in staff + [turner]]).values_list('pk', 'staff_position_id'))
        self.assertEqual(sorted(pk for pk, row in bindings.items() if row == first.pk), sorted(before[first.pk]))
        self.assertEqual(sorted(pk for pk, row in bindings.items() if row == second.pk), sorted(before[second.pk]))
        self.assertEqual((len(before[first.pk]), len(before[second.pk])), (2, 1))
        self.assertIsNone(bindings[turner.pk])                 # ягона номли лавозим ходимига тегилмайди

    def test_after_freezing_a_transfer_moves_only_the_chosen_employee(self):
        first = self.make_position('Электрмонтер', staff_count=2)
        second = self.make_position('Электрмонтер', staff_count=2)
        for _ in range(3):
            self.make_employee('Электрмонтер', department=self.department)
        self.freeze()
        in_first = [item.pk for item in employees_of_position(first)]
        in_second = [item.pk for item in employees_of_position(second)]
        moving = in_first[0]

        response = self.client.patch(f'{POSITIONS_URL}{first.pk}/employees/{moving}/', {'position_id': second.pk}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item.pk for item in employees_of_position(first)], in_first[1:])
        self.assertEqual(sorted(item.pk for item in employees_of_position(second)), sorted(in_second + [moving]))
