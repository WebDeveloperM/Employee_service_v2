"""Бир нечта ходимни бир вақтда қўшиш: лавозимга (ҳар бирининг штат улуши билан) ва штатсиз бўлимга («Шогиртлар»)."""
import re
from decimal import Decimal

from .models import Employee
from .staff import employees_of_position
from .test_staff import StaffTestCase
from .test_staff_apprentices import ApprenticeTestCase


class PositionBulkAddTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.position = self.make_position('Слесарь', staff_count=3)
        self.people = [self.make_employee('Электрик', department=self.other_department) for _ in range(4)]

    def bulk_url(self, position=None):
        return f'/api/template/staff/positions/{(position or self.position).pk}/employees/bulk/'

    def bulk(self, items, position=None):
        return self.client.post(self.bulk_url(position), {'items': items}, format='json')

    def test_all_selected_employees_are_added_in_one_request_with_their_own_rates(self):
        response = self.bulk([
            {'employee_id': self.people[0].pk, 'staff_rate': 1},
            {'employee_id': self.people[1].pk, 'staff_rate': 0.5},
            {'employee_id': self.people[2].pk},                       # улуш кўрсатилмаса — 1
        ])

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['message'], '3 та ходим лавозимга қўшилди')
        self.assertEqual(body['position']['occupied'], 2.5)
        self.assertEqual(body['position']['vacant'], 0.5)
        self.assertEqual(len(body['data']), 3)
        rates = {employee.pk: employee.staff_rate for employee in Employee.objects.filter(pk__in=[p.pk for p in self.people[:3]])}
        self.assertEqual(rates, {self.people[0].pk: Decimal('1'), self.people[1].pk: Decimal('0.5'), self.people[2].pk: Decimal('1')})
        self.assertEqual({employee.pk for employee in employees_of_position(self.position)}, {p.pk for p in self.people[:3]})
        self.assertTrue(all(Employee.objects.get(pk=p.pk).position == 'Слесарь' for p in self.people[:3]))

    def test_they_move_into_the_positions_department(self):
        self.bulk([{'employee_id': self.people[0].pk, 'staff_rate': 1}, {'employee_id': self.people[1].pk, 'staff_rate': 1}])

        self.assertEqual({Employee.objects.get(pk=p.pk).department_id for p in self.people[:2]}, {self.department.pk})

    def test_when_the_free_staff_is_not_enough_nobody_is_added_and_the_one_that_does_not_fit_is_named(self):
        response = self.bulk([{'employee_id': person.pk, 'staff_rate': 1} for person in self.people])   # 4 штат, лавозимда 3 та

        self.assertEqual(response.status_code, 400, response.content)
        message = response.json()['detail']
        self.assertIn(str(self.people[3]), message)
        self.assertIn('Ҳеч ким қўшилмади', message)
        self.assertEqual(list(employees_of_position(self.position)), [])
        self.assertEqual(Employee.objects.filter(position='Слесарь').count(), 0)
        self.assertTrue(all(Employee.objects.get(pk=p.pk).department_id == self.other_department.pk for p in self.people))

    def test_half_rates_make_room_for_more_people(self):
        response = self.bulk([{'employee_id': person.pk, 'staff_rate': 0.75} for person in self.people])   # 4 × 0,75 = 3

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['position']['vacant'], 0)

    def test_bad_input_is_refused_and_changes_nothing(self):
        cases = [
            {'items': []},
            {},
            {'items': 'x'},
            {'items': [{'employee_id': 'abc'}]},
            {'items': [{'employee_id': self.people[0].pk, 'staff_rate': 0}]},
            {'items': [{'employee_id': self.people[0].pk, 'staff_rate': 3}]},
            {'items': [{'employee_id': self.people[0].pk}, {'employee_id': 999999}]},       # бор бўлмаган ходим — ҳамма бекор
            {'items': ['x']},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(self.bulk_url(), payload, format='json')
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Employee.objects.filter(position='Слесарь').count(), 0)

    def test_inactive_and_deleted_employees_are_refused(self):
        inactive = Employee.objects.create(first_name='A', last_name='Off', tabel_number='X1', is_active=False)

        response = self.bulk([{'employee_id': self.people[0].pk}, {'employee_id': inactive.pk}])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Employee.objects.get(pk=self.people[0].pk).position, 'Электрик')

    def test_duplicates_count_once_and_employees_already_in_the_position_are_skipped(self):
        first = self.bulk([{'employee_id': self.people[0].pk}])
        self.assertEqual(first.status_code, 201)

        response = self.bulk([
            {'employee_id': self.people[0].pk},                 # аллақачон шу лавозимда
            {'employee_id': self.people[1].pk}, {'employee_id': self.people[1].pk, 'staff_rate': 2},   # такрор — биринчиси ҳисобга олинади
        ])

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['message'], '1 та ходим лавозимга қўшилди')
        self.assertEqual(Employee.objects.get(pk=self.people[1].pk).staff_rate, Decimal('1'))
        self.assertEqual(len(response.json()['data']), 2)

    def test_everyone_already_there_is_an_error(self):
        self.bulk([{'employee_id': self.people[0].pk}, {'employee_id': self.people[1].pk}])

        response = self.bulk([{'employee_id': self.people[0].pk}, {'employee_id': self.people[1].pk}])

        self.assertEqual(response.status_code, 400)
        self.assertIn('аллақачон', response.json()['detail'])

    def test_the_number_of_employees_per_request_is_limited(self):
        items = [{'employee_id': self.people[0].pk}] * 201

        self.assertEqual(self.bulk(items).status_code, 400)

    def test_only_admin_and_hr_can_use_it(self):
        from rest_framework.test import APIClient

        payload = {'items': [{'employee_id': self.people[0].pk}]}
        client = APIClient()
        client.force_authenticate(user=self.regular)
        self.assertEqual(client.post(self.bulk_url(), payload, format='json').status_code, 403)
        self.assertIn(APIClient().post(self.bulk_url(), payload, format='json').status_code, (401, 403))
        self.assertEqual(Employee.objects.filter(position='Слесарь').count(), 0)

        client.force_authenticate(user=self.hr)
        self.assertEqual(client.post(self.bulk_url(), payload, format='json').status_code, 201)

    def test_unknown_position_is_404(self):
        response = self.client.post('/api/template/staff/positions/99999/employees/bulk/', {'items': [{'employee_id': self.people[0].pk}]}, format='json')

        self.assertEqual(response.status_code, 404)

    def test_single_add_still_works_and_gives_the_same_free_staff_message(self):
        self.bulk([{'employee_id': person.pk} for person in self.people[:3]])

        response = self.client.post(f'/api/template/staff/positions/{self.position.pk}/employees/', {'employee_id': self.people[3].pk}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('бўш иш ўрни йўқ', response.json()['detail'])


class SectionBulkAddTests(ApprenticeTestCase):
    def setUp(self):
        super().setUp()
        self.people = [self.make_employee('Слесарь', department=self.other_department) for _ in range(3)]

    def bulk_url(self, section=None):
        return f'{self.employees_url(section)}bulk/'

    def test_selected_employees_join_the_section_in_one_request(self):
        response = self.client.post(self.bulk_url(), {'employee_ids': [p.pk for p in self.people]}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['message'], '3 та ходим бўлимга қўшилди')
        self.assertEqual(body['count'], 3)
        for person in self.people:
            person.refresh_from_db()
            self.assertEqual((person.section_id, person.department_id), (self.apprentices.pk, self.department.pk))

    def test_employees_already_in_the_section_are_skipped_and_duplicates_count_once(self):
        self.client.post(self.employees_url(), {'employee_id': self.people[0].pk}, format='json')

        response = self.client.post(self.bulk_url(), {'employee_ids': [self.people[0].pk, self.people[1].pk, self.people[1].pk]}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['message'], '1 та ходим бўлимга қўшилди')

    def test_everyone_already_there_is_an_error(self):
        self.client.post(self.bulk_url(), {'employee_ids': [self.people[0].pk]}, format='json')

        response = self.client.post(self.bulk_url(), {'employee_ids': [self.people[0].pk]}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_bad_input_changes_nothing(self):
        for payload in ({}, {'employee_ids': []}, {'employee_ids': 'x'}, {'employee_ids': ['abc']},
                        {'employee_ids': [self.people[0].pk, 999999]}, {'employee_ids': [self.people[0].pk] * 201}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.bulk_url(), payload, format='json').status_code, 400)
        self.assertEqual(Employee.objects.filter(section=self.apprentices).count(), 0)

    def test_a_section_with_staff_refuses(self):
        response = self.client.post(self.bulk_url(self.section), {'employee_ids': [self.people[0].pk]}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Employee.objects.get(pk=self.people[0].pk).section_id, None)

    def test_over_staff_section_works_the_same(self):
        from .models import Section

        over = Section.objects.create(department=self.department, name='св.шт.', code='9')

        response = self.client.post(self.bulk_url(over), {'employee_ids': [p.pk for p in self.people[:2]]}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Employee.objects.filter(section=over).count(), 2)


class StaffPageBulkControlsTests(StaffTestCase):
    """Саҳифа: белгилаш катаклари, «Барчасини танлаш» ва «Танланганларни қўшиш» иккала ойнада ҳам бор."""

    def setUp(self):
        super().setUp()
        from django.test import Client

        self.page = Client(HTTP_HOST='localhost')
        self.page.force_login(self.admin)

    def html(self):
        return self.page.get('/staff/').content.decode()

    def test_position_window_has_select_all_and_add_selected(self):
        html = self.html()

        for expected in ('id="employeeBulkBar"', 'id="employeeSelectAll"', 'id="employeeBulkAdd"', 'data-select="',
                         "'/template/staff/positions/' + current.id + '/employees/bulk/'", 'function addSelectedEmployeesToPosition()',
                         'id="employeeSearchSummary"'):
            self.assertIn(expected, html)

    def test_no_staff_view_has_select_all_and_add_selected(self):
        html = self.html()

        for expected in ('id="unitBulkBar"', 'id="unitSelectAll"', 'id="unitBulkAdd"', 'data-unit-select="',
                         "'/template/staff/sections/' + selected.id + '/employees/bulk/'", 'function addSelectedEmployeesToUnit()'):
            self.assertIn(expected, html)

    def test_several_numbers_preselect_everyone_found(self):
        html = self.html()

        self.assertEqual(len(re.findall(r'new Set\(staffState\.(?:unitEmployeeSearch|employeeSearch)\.multiple \?', html)), 2)
