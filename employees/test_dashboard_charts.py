import json
import re
from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase

from .dashboard_stats import age_on, build_dashboard_charts
from .models import Department, Employee, ROLE_ADMIN, ROLE_HR, StaffPosition, UserProfile


TODAY = date(2026, 11, 12)


def make_employee(tabel, **fields):
    return Employee.objects.create(first_name='Ism', last_name='Familiya', tabel_number=tabel, **fields)


def chart_by_key(charts, key):
    return next(chart for chart in charts if chart['key'] == key)


def values(chart):
    return {row['label']: row['value'] for row in chart['rows']}


class AgeOnTests(TestCase):
    def test_birthday_not_reached_yet_this_year_does_not_count(self):
        self.assertEqual(age_on(TODAY, date(2000, 11, 12)), 26)
        self.assertEqual(age_on(TODAY, date(2000, 11, 13)), 25)
        self.assertEqual(age_on(TODAY, date(2000, 1, 1)), 26)


class AgeChartTests(TestCase):
    def age_chart(self):
        return chart_by_key(build_dashboard_charts(include_staff=False, today=TODAY), 'age')

    def test_groups_split_on_their_boundaries(self):
        # Ёши (2026-11-12 ҳолатига): 18, 24, 25, 40, 41, 60, 60 (61 га эртага тўлади), 61
        born_dates = [date(2008, 11, 12), date(2002, 11, 12), date(2001, 11, 12), date(1986, 1, 1),
                      date(1985, 11, 12), date(1966, 11, 12), date(1965, 11, 13), date(1965, 11, 12)]
        for index, born in enumerate(born_dates):
            make_employee(f'AGE-{index}', date_of_birth=born)

        chart = self.age_chart()

        self.assertEqual(values(chart), {'18-24 ёш': 2, '25-40 ёш': 2, '41-60 ёш': 3, '60 ёшдан катта': 1})
        self.assertEqual(chart['note'], '')

    def test_under_18_row_appears_only_when_there_is_such_employee(self):
        self.assertNotIn('18 ёшгача', values(self.age_chart()))

        make_employee('AGE-KID', date_of_birth=date(2010, 5, 5))

        self.assertEqual(values(self.age_chart())['18 ёшгача'], 1)

    def test_missing_birth_dates_are_reported_in_the_note_and_not_charted(self):
        make_employee('AGE-OK', date_of_birth=date(1990, 1, 1))
        make_employee('AGE-NONE-1')
        make_employee('AGE-NONE-2')

        chart = self.age_chart()

        self.assertEqual(sum(row['value'] for row in chart['slices']), 1)
        self.assertEqual(chart['note'], 'Туғилган санаси киритилмаган: 2')

    def test_deleted_and_inactive_employees_are_ignored(self):
        make_employee('AGE-DELETED', date_of_birth=date(1990, 1, 1), is_deleted=True)
        make_employee('AGE-INACTIVE', date_of_birth=date(1990, 1, 1), is_active=False)
        make_employee('AGE-LIVE', date_of_birth=date(1990, 1, 1))

        self.assertEqual(values(self.age_chart())['25-40 ёш'], 1)


class GenderChartTests(TestCase):
    def test_counts_men_and_women_and_notes_unspecified(self):
        make_employee('G-1', gender='M')
        make_employee('G-2', gender='M')
        make_employee('G-3', gender='F')
        make_employee('G-4')
        make_employee('G-5', gender='F', is_deleted=True)

        chart = chart_by_key(build_dashboard_charts(include_staff=False, today=TODAY), 'gender')

        self.assertEqual(values(chart), {'Эркаклар': 2, 'Аёллар': 1})
        self.assertEqual(chart['note'], 'Жинси кўрсатилмаган: 1')


class StaffChartTests(TestCase):
    def test_total_occupied_and_vacant_come_from_staff_positions(self):
        department = Department.objects.create(name='1-Цех', sort_order=1)
        StaffPosition.objects.create(department=department, name='Слесарь', staff_count=3)
        StaffPosition.objects.create(department=department, name='Бошлиқ', staff_count=2)
        make_employee('S-1', department=department, position='Слесарь')
        make_employee('S-2', department=department, position='Бошлиқ')
        make_employee('S-3', department=department, position='Бошлиқ')

        chart = chart_by_key(build_dashboard_charts(include_staff=True, today=TODAY), 'staff')

        self.assertEqual(values(chart), {'Штат бирлиги': 5, 'Банд ўринлар': 3, 'Бўш иш ўринлари': 2})
        # Доирада штат жами эмас, банд ва бўш ўринлар кўрсатилади (жами икки марта ҳисобланмаслиги учун).
        self.assertEqual([row['label'] for row in chart['slices']], ['Банд ўринлар', 'Бўш иш ўринлари'])

    def test_staff_chart_is_left_out_when_not_requested(self):
        keys = [chart['key'] for chart in build_dashboard_charts(include_staff=False, today=TODAY)]

        self.assertEqual(keys, ['age', 'gender'])


class DashboardPageChartsTests(TestCase):
    def client_for(self, user):
        client = Client(HTTP_HOST='192.168.2.72')
        client.force_login(user)
        return client

    def embedded_charts(self, html):
        match = re.search(r'<script id="dashboardChartsData" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match, 'диаграмма маълумоти саҳифада йўқ')
        return json.loads(match.group(1))

    def test_admin_sees_all_three_charts_with_their_tables(self):
        admin = User.objects.create_user(username='charts_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        make_employee('P-1', gender='M', date_of_birth=date(1990, 1, 1))

        html = self.client_for(admin).get('/').content.decode()

        self.assertEqual([chart['key'] for chart in self.embedded_charts(html)], ['staff', 'age', 'gender'])
        for title in ('Штат бирлиги &amp; Бўш иш ўринлари', 'Ёш бўйича статистика', 'Эркаклар &amp; Аёллар'):
            self.assertIn(title, html)
        self.assertEqual(html.count('data-chart="'), 3)

    def test_hr_sees_the_staff_chart_too(self):
        hr = User.objects.create_user(username='charts_hr', password='secret123')
        UserProfile.objects.create(user=hr, role=ROLE_HR)

        html = self.client_for(hr).get('/').content.decode()

        self.assertIn('data-chart="staff"', html)

    def test_staff_chart_is_hidden_from_people_without_staff_rights(self):
        plain = User.objects.create_user(username='charts_noprofile', password='secret123')

        html = self.client_for(plain).get('/').content.decode()

        self.assertNotIn('data-chart="staff"', html)
        self.assertNotIn('Штат бирлиги &amp; Бўш иш ўринлари', html)
        self.assertIn('data-chart="age"', html)
        self.assertEqual([chart['key'] for chart in self.embedded_charts(html)], ['age', 'gender'])

    def test_employee_list_page_has_no_charts(self):
        admin = User.objects.create_user(username='charts_list_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)

        html = self.client_for(admin).get('/employees/').content.decode()

        self.assertNotIn('dashboardChartsData', html)
