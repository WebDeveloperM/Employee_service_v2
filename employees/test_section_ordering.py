from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Department, Section


SECTIONS_URL = '/api/template/sections/'


class SectionOrderByCodeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=User.objects.create_user(username='sections_user', password='secret123'))
        self.department = Department.objects.create(name='1-Цех')

    def make_sections(self, *codes_and_names, department=None):
        for code, name in codes_and_names:
            Section.objects.create(department=department or self.department, code=code, name=name)

    def codes(self, query=''):
        response = self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=100{query}')
        self.assertEqual(response.status_code, 200, response.content)
        return [item['code'] for item in response.json()['data']]

    def test_numeric_codes_are_sorted_as_numbers_not_text(self):
        self.make_sections(('10', 'a'), ('2', 'b'), ('1', 'c'), ('100', 'd'), ('3', 'e'))

        self.assertEqual(self.codes(), ['1', '2', '3', '10', '100'])

    def test_order_ignores_names_and_creation_order(self):
        # Как на странице «Отделы»: код 2 создан раньше кода 1, а название «1-блок» идёт раньше «1-Цех …».
        self.make_sections(('2', '1-блок'), ('1', '1-Цех Технология'), ('3', '2-блок'), ('4', '3-блок'), ('5', '4-блок'))

        self.assertEqual(self.codes(), ['1', '2', '3', '4', '5'])

    def test_non_numeric_codes_follow_numbers_and_empty_codes_come_last_by_name(self):
        self.make_sections(('', 'Б отдел'), ('B-1', 'x'), ('', 'А отдел'), ('2', 'y'), ('A-5', 'z'), ('10', 'w'))

        response = self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=100').json()['data']

        self.assertEqual(
            [(item['code'], item['name']) for item in response],
            [('2', 'y'), ('10', 'w'), ('A-5', 'z'), ('B-1', 'x'), ('', 'А отдел'), ('', 'Б отдел')],
        )

    def test_leading_zeros_count_as_numbers_and_overlong_digit_strings_do_not_break(self):
        self.make_sections(('007', 'a'), ('8', 'b'), ('1' * 25, 'c'), ('6', 'd'))

        self.assertEqual(self.codes(), ['6', '007', '8', '1' * 25])

    def test_departments_stay_grouped_in_their_own_order(self):
        second = Department.objects.create(name='2-Цех')
        self.make_sections(('9', 'a'), ('1', 'b'))
        self.make_sections(('5', 'c'), ('2', 'd'), department=second)

        response = self.client.get(f'{SECTIONS_URL}?page_size=100').json()['data']

        self.assertEqual(
            [(item['department']['name'], item['code']) for item in response],
            [('1-Цех', '1'), ('1-Цех', '9'), ('2-Цех', '2'), ('2-Цех', '5')],
        )

    def test_search_keeps_the_order(self):
        self.make_sections(('10', 'Блок А'), ('2', 'Блок Б'), ('3', 'Другой'))

        self.assertEqual(self.codes('&search=Блок'), ['2', '10'])

    def test_pages_are_consecutive_and_complete(self):
        self.make_sections(*[(str(number), f'Отдел {number}') for number in (12, 3, 11, 1, 20, 2, 10)])

        seen = []
        for page in (1, 2, 3, 4):
            seen += [
                item['code']
                for item in self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=2&page={page}').json()['data']
            ]

        self.assertEqual(seen, ['1', '2', '3', '10', '11', '12', '20'])
