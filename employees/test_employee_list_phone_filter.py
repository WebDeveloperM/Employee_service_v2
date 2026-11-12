from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Employee, ROLE_ADMIN, UserProfile


EMPLOYEES_URL = '/api/template/employees/'


class EmployeeListPhoneFilterTests(TestCase):
    """Телефон бўйича қидирув: Телефон 1 ва 2, база ичидаги ҳар қандай формат, рақамлар бўйича."""

    def setUp(self):
        self.admin = User.objects.create_user(username='phone_filter_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        # Базада форма маскаси («+998 90 123 45 67»), импорт («+998935556677», «901234567») ва иккинчи телефон учрайди.
        self.make('FORM', phone_number_1='+998 90 123 45 67')
        self.make('IMPORT', phone_number_1='+998935556677')
        self.make('SECOND', phone_number_2='(91) 777-88-99')
        self.make('LOCAL', phone_number_1='901234567')
        self.make('NOPHONE')
        self.make('GONE', phone_number_1='+998 90 123 00 00', is_deleted=True)
        self.make('OFF', phone_number_1='+998 90 123 99 99', is_active=False)

    def make(self, tabel, **fields):
        return Employee.objects.create(first_name='Ism', last_name=tabel.title(), tabel_number=tabel, **fields)

    def tabels(self, phone, **params):
        response = self.api.get(EMPLOYEES_URL, {'page_size': 100, 'phone': phone, **params})
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload['count'], len(payload['data']))
        return sorted(item['tabel_number'] for item in payload['data'])

    def test_finds_by_digits_whatever_the_stored_format(self):
        # «+998 90 123 45 67» (форма) ҳам, «901234567» (импорт) ҳам битта рақам сифатида топилади.
        self.assertEqual(self.tabels('901234567'), ['FORM', 'LOCAL'])

    def test_query_formatting_does_not_matter(self):
        expected = self.tabels('901234567')
        for query in ('90 123 45 67', '90-123-45-67', '(90) 123 45 67', '+998 90 123 45 67', '  901234567  '):
            with self.subTest(query=query):
                self.assertEqual(self.tabels(query), expected)

    def test_partial_numbers_match_anywhere_in_the_number(self):
        self.assertEqual(self.tabels('5556677'), ['IMPORT'])
        self.assertEqual(self.tabels('777'), ['SECOND'])
        self.assertEqual(self.tabels('90123'), ['FORM', 'LOCAL', 'OFF'])

    def test_second_phone_is_searched_too(self):
        self.assertEqual(self.tabels('917778899'), ['SECOND'])
        self.assertEqual(self.tabels('(91) 777'), ['SECOND'])

    def test_country_code_in_the_query_also_finds_numbers_stored_without_it(self):
        # «+998 90 123…» деб ёзилса, давлат кодисиз сақланган «901234567» (LOCAL) ҳам топилади.
        self.assertEqual(self.tabels('+998 90 123'), ['FORM', 'LOCAL', 'OFF'])
        self.assertEqual(self.tabels('998901234567'), ['FORM', 'LOCAL'])

    def test_no_match_returns_an_empty_list(self):
        self.assertEqual(self.tabels('000111222'), [])

    def test_text_without_digits_matches_nobody_instead_of_returning_everything(self):
        self.assertEqual(self.tabels('abc'), [])
        self.assertEqual(self.tabels('+ - ()'), [])

    def test_deleted_employees_are_never_found(self):
        self.assertNotIn('GONE', self.tabels('9012300'))
        self.assertEqual(self.tabels('9012300'), [])

    def test_combines_with_status_and_tabel_filters(self):
        self.assertEqual(self.tabels('90123', status='active'), ['FORM', 'LOCAL'])
        self.assertEqual(self.tabels('90123', status='inactive'), ['OFF'])
        self.assertEqual(self.tabels('90123', status='active', tabel_number='LOC'), ['LOCAL'])

    def test_empty_phone_parameter_changes_nothing(self):
        response = self.api.get(EMPLOYEES_URL, {'page_size': 100, 'phone': '   '})

        self.assertEqual(response.json()['count'], 6)    # ўчирилмаган барча ходимлар

    def test_phone_search_uses_the_paginated_count(self):
        response = self.api.get(EMPLOYEES_URL, {'page_size': 1, 'page': 1, 'phone': '90123'})

        payload = response.json()
        self.assertEqual((payload['count'], len(payload['data'])), (3, 1))


class EmployeeListPhoneFilterPageTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='phone_page_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(admin)
        self.html = web.get('/employees/').content.decode()

    def test_phone_field_lives_in_the_filter_accordion_next_to_tabel_and_status(self):
        panel = self.html.index('id="filterPanel"')
        tabel = self.html.index('id="tabelFilter"')
        status = self.html.index('id="statusFilter"')
        phone = self.html.index('id="phoneFilter"')
        table = self.html.index('<table')

        self.assertLess(panel, tabel)
        self.assertLess(tabel, status)
        self.assertLess(status, phone)    # Ҳолати дан кейин (белгиланган жойда)
        self.assertLess(phone, table)
        self.assertIn('Телефон рақами', self.html[status:table])
        self.assertIn('type="tel"', self.html[phone - 40:phone + 80])

    def test_phone_is_sent_to_the_api_and_counted_in_the_filter_badge(self):
        self.assertIn("if (phone) params.set('phone', phone);", self.html)
        self.assertIn("document.getElementById('phoneFilter').value.trim() ? 1 : 0", self.html)
        self.assertIn("['tabelFilter', 'phoneFilter'].forEach", self.html)   # ёзилганда ва Enter да қидиради
