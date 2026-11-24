"""Табель рақами бўйича қидириш: бўшлиқ билан ажратилган бир нечта рақам («Шогиртлар» саҳифаси)."""
from django.test import Client
from rest_framework.test import APIClient

from .models import Employee
from .staff_views import MULTI_LOOKUP_LIMIT, split_tabel_numbers
from .test_staff import StaffTestCase
from .test_staff_apprentices import LOOKUP_URL, ApprenticeTestCase


class SplitTabelNumbersTests(StaffTestCase):
    def test_separators_and_duplicates(self):
        self.assertEqual(split_tabel_numbers('6380 6263  6024'), ['6380', '6263', '6024'])
        self.assertEqual(split_tabel_numbers('6380,6263; 6024\n5990\t7614'), ['6380', '6263', '6024', '5990', '7614'])
        self.assertEqual(split_tabel_numbers('6380 6380 t-1 T-1'), ['6380', 't-1'])   # такрорлар (катта-кичик ҳарфсиз) олиб ташланади
        self.assertEqual(split_tabel_numbers('  '), [])


class MultiLookupTests(ApprenticeTestCase):
    def numbers(self, term, **extra):
        response = self.client.get(LOOKUP_URL, {'tabel_number': term, **extra})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def make_numbered(self, *numbers, **extra):
        return [
            Employee.objects.create(first_name='Ism', last_name=f'F{number}', tabel_number=number, **extra)
            for number in numbers
        ]

    def test_every_listed_number_is_found_in_the_order_typed(self):
        self.make_numbered('6380', '6263', '6024', '5990')

        body = self.numbers('5990 6380 6024')

        self.assertTrue(body['multiple'])
        self.assertEqual([item['tabel_number'] for item in body['data']], ['5990', '6380', '6024'])
        self.assertEqual((body['searched'], body['not_found']), (3, []))

    def test_numbers_are_matched_exactly_not_by_part(self):
        self.make_numbered('6380', '63801', '16380')

        body = self.numbers('6380 9999')

        self.assertEqual([item['tabel_number'] for item in body['data']], ['6380'])   # «63801» ва «16380» кирмайди
        self.assertEqual(body['not_found'], ['9999'])

    def test_unknown_inactive_and_deleted_numbers_are_reported_as_not_found(self):
        self.make_numbered('100')
        Employee.objects.create(first_name='A', last_name='Inactive', tabel_number='101', is_active=False)
        Employee.objects.create(first_name='A', last_name='Deleted', tabel_number='102', is_deleted=True)

        body = self.numbers('100 101 102 103')

        self.assertEqual([item['tabel_number'] for item in body['data']], ['100'])
        self.assertEqual(body['not_found'], ['101', '102', '103'])
        self.assertEqual(body['searched'], 4)

    def test_case_is_ignored_and_duplicates_are_counted_once(self):
        self.make_numbered('T-77')

        body = self.numbers('t-77 T-77 T-77 5')

        self.assertEqual([item['tabel_number'] for item in body['data']], ['T-77'])
        self.assertEqual((body['searched'], body['not_found']), (2, ['5']))

    def test_single_number_keeps_the_old_partial_search(self):
        self.make_numbered('1234', '12', '120')

        body = self.numbers('12')

        self.assertFalse(body['multiple'])
        self.assertEqual([item['tabel_number'] for item in body['data']], ['12', '120', '1234'])
        self.assertEqual(body['not_found'], [])

    def test_trailing_space_is_still_a_single_number(self):
        self.make_numbered('1234', '12')

        body = self.numbers('12 ')

        self.assertFalse(body['multiple'])
        self.assertEqual(len(body['data']), 2)

    def test_more_than_ten_numbers_are_returned(self):
        numbers = [f'{9000 + index}' for index in range(25)]
        self.make_numbered(*numbers)

        body = self.numbers(' '.join(numbers))

        self.assertEqual([item['tabel_number'] for item in body['data']], numbers)
        self.assertFalse(body['truncated'])

    def test_too_many_numbers_are_cut_and_flagged(self):
        body = self.numbers(' '.join(str(index) for index in range(MULTI_LOOKUP_LIMIT + 5)))

        self.assertTrue(body['truncated'])
        self.assertEqual(body['searched'], MULTI_LOOKUP_LIMIT)

    def test_employees_already_in_the_section_are_flagged(self):
        inside, = self.make_numbered('A-1', section=self.apprentices, department=self.department)
        outside, = self.make_numbered('A-2', department=self.department)

        body = self.numbers('A-1 A-2', section_id=self.apprentices.pk)

        flags = {item['tabel_number']: item['in_position'] for item in body['data']}
        self.assertEqual(flags, {'A-1': True, 'A-2': False})
        self.assertEqual(Employee.objects.get(pk=outside.pk).section_id, None)   # қидириш ҳеч нарсани ўзгартирмайди

    def test_adding_one_of_the_found_employees_works_and_changes_only_that_one(self):
        first, second = self.make_numbered('B-1', 'B-2', department=self.department)

        response = self.client.post(f'/api/template/staff/sections/{self.apprentices.pk}/employees/', {'employee_id': first.pk}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.section_id, second.section_id), (self.apprentices.pk, None))

    def test_regular_user_is_still_refused(self):
        self.assertEqual(self.client.get(LOOKUP_URL, {'tabel_number': '1 2'}).status_code, 200)
        plain = APIClient()
        plain.force_authenticate(user=self.regular)
        self.assertEqual(plain.get(LOOKUP_URL, {'tabel_number': '1 2'}).status_code, 403)

    def test_page_has_the_summary_list_and_keeps_it_after_adding(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/staff/').content.decode()

        self.assertIn('id="unitEmployeeSearchSummary"', html)
        self.assertIn("'Топилди: ' + foundCount + ' / ' + search.searched", html)
        self.assertIn("' · Топилмади (' + search.notFound.length + '): '", html)
        self.assertIn('employee.in_position = true;', html)            # қўшилгандан кейин рўйхат сақланади
        self.assertIn("unitEmployeeSearch.addEventListener('paste'", html)   # Excel устунидан нусха
        self.assertIn('бир нечта бўлса — бўшлиқ билан ажратинг', html)
