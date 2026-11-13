import json
import re

from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .dashboard_stats import build_dashboard_charts
from .models import EMPLOYEE_STATUS_CHOICES, Employee, ROLE_ADMIN, UserProfile


def make_employee(tabel, **fields):
    return Employee.objects.create(first_name='Ism', last_name=tabel.title(), tabel_number=tabel, **fields)


def option_labels(html):
    select = html[html.index('id="employee_status"'):]
    select = select[:select.index('</select>')]
    return re.findall(r'<option value="([^"]+)"([^>]*)>([^<]+)</option>', select)


class WorkStatusModelAndApiTests(TestCase):
    """«Ҳолати»: Фаол · Меҳнат таътилида · Хизмат сафарида · Касаллик таътилида · Бола парваришлаш таътилида · Фаол эмас."""

    def setUp(self):
        self.admin = User.objects.create_user(username='work_status_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        self.employee = make_employee('WS-1')

    def patch(self, **fields):
        return self.api.patch(f'/api/employees/{self.employee.slug}/', fields, format='multipart')

    def test_new_employees_are_simply_at_work(self):
        self.assertIsNone(self.employee.work_status)
        self.assertEqual(self.employee.status_key, 'working')

    def test_status_key_covers_every_state(self):
        self.assertEqual(Employee(is_active=True, work_status=None).status_key, 'working')
        self.assertEqual(Employee(is_active=True, work_status='').status_key, 'working')
        self.assertEqual(Employee(is_active=True, work_status='vacation').status_key, 'vacation')
        self.assertEqual(Employee(is_active=False, work_status=None).status_key, 'inactive')
        self.assertEqual(Employee(is_active=False, work_status='vacation').status_key, 'inactive')   # фаол эмас — устувор

    def test_choices_match_the_requested_list_in_order(self):
        self.assertEqual([label for _, label in EMPLOYEE_STATUS_CHOICES], [
            'Фаол', 'Меҳнат таътилида', 'Хизмат сафарида', 'Касаллик таътилида', 'Бола парваришлаш таътилида', 'Фаол эмас',
        ])

    def test_each_leave_status_can_be_saved_through_the_edit_form_request(self):
        # Ходим формаси (employee_detail.html) мультипарт PATCH юборади: is_active + work_status.
        for status in ('vacation', 'business_trip', 'sick_leave', 'childcare_leave'):
            with self.subTest(status=status):
                response = self.patch(is_active='true', work_status=status)

                self.assertEqual(response.status_code, 200, response.content)
                self.employee.refresh_from_db()
                self.assertEqual((self.employee.is_active, self.employee.work_status), (True, status))
                self.assertEqual(response.json()['work_status'], status)

    def test_choosing_working_clears_the_leave_status(self):
        self.patch(is_active='true', work_status='sick_leave')

        response = self.patch(is_active='true', work_status='')

        self.assertEqual(response.status_code, 200, response.content)
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.work_status)         # бўш қатор NULL бўлиб сақланади
        self.assertIsNone(response.json()['work_status'])

    def test_inactive_employee_keeps_no_leave_status_even_if_one_is_sent(self):
        response = self.patch(is_active='false', work_status='vacation')

        self.assertEqual(response.status_code, 200, response.content)
        self.employee.refresh_from_db()
        self.assertEqual((self.employee.is_active, self.employee.work_status), (False, None))

    def test_reactivating_an_inactive_employee_puts_them_back_at_work(self):
        self.patch(is_active='true', work_status='vacation')
        self.patch(is_active='false', work_status='')

        self.patch(is_active='true', work_status='')

        self.employee.refresh_from_db()
        self.assertEqual((self.employee.is_active, self.employee.work_status), (True, None))

    def test_unknown_status_is_rejected_with_an_uzbek_message(self):
        response = self.patch(is_active='true', work_status='on_the_moon')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('Номаълум ҳолат', str(response.json()))
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.work_status)

    def test_requests_without_work_status_leave_it_untouched(self):
        self.patch(is_active='true', work_status='vacation')

        response = self.patch(first_name='Yangi')       # ташқи тизимлар work_status ни билмайди

        self.assertEqual(response.status_code, 200, response.content)
        self.employee.refresh_from_db()
        self.assertEqual((self.employee.first_name, self.employee.work_status), ('Yangi', 'vacation'))

    def test_employee_is_still_counted_as_active_on_leave(self):
        self.patch(is_active='true', work_status='vacation')

        response = self.api.get('/api/template/employees/', {'status': 'active'})

        self.assertEqual([item['tabel_number'] for item in response.json()['data']], ['WS-1'])


class WorkStatusFormTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='work_status_form_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        self.web = Client(HTTP_HOST='192.168.101.6')
        self.web.force_login(admin)

    def edit_page(self, employee):
        return self.web.get(f'/employees/{employee.slug}/edit/').content.decode()

    def test_the_status_select_offers_all_six_options_in_order(self):
        options = option_labels(self.edit_page(make_employee('FORM-1')))

        self.assertEqual([(value, label) for value, _, label in options], [
            ('working', 'Фаол'), ('vacation', 'Меҳнат таътилида'), ('business_trip', 'Хизмат сафарида'),
            ('sick_leave', 'Касаллик таътилида'), ('childcare_leave', 'Бола парваришлаш таътилида'), ('inactive', 'Фаол эмас'),
        ])

    def test_the_current_status_is_preselected(self):
        cases = (
            (dict(), 'working'),
            (dict(work_status='vacation'), 'vacation'),
            (dict(work_status='childcare_leave'), 'childcare_leave'),
            (dict(is_active=False), 'inactive'),
        )
        for index, (fields, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                options = option_labels(self.edit_page(make_employee(f'FORM-SEL-{index}', **fields)))

                selected = [value for value, attrs, _ in options if 'selected' in attrs]
                self.assertEqual(selected, [expected])

    def test_the_view_page_shows_the_status_in_a_disabled_select(self):
        employee = make_employee('FORM-VIEW', work_status='sick_leave')

        html = self.web.get(f'/employees/{employee.slug}/').content.decode()

        select = html[html.index('<select id="employee_status"'):]
        self.assertIn('disabled', select[:select.index('>')])
        self.assertIn('<option value="sick_leave" selected>', select)

    def test_saving_splits_the_choice_into_is_active_and_work_status(self):
        html = self.edit_page(make_employee('FORM-JS'))

        self.assertIn("const employeeStatus = document.getElementById('employee_status').value;", html)
        self.assertIn("formData.append('is_active', employeeStatus !== 'inactive');", html)
        self.assertIn("formData.append('work_status', ['inactive', 'working'].includes(employeeStatus) ? '' : employeeStatus);", html)
        self.assertNotIn("getElementById('is_active')", html)     # эски танлов қолмаган

    def test_excel_export_has_no_status_column(self):
        html = self.web.get('/employees/').content.decode()
        export = html[html.index('function writeEmployeesWorkbook(rows)'):html.index("XLSX.writeFile(wb, 'ходимлар.xlsx')")]

        # Экспортда «Ҳолати» устуни йўқ (фойдаланувчи сўрови), шунинг учун ҳолат матнлари рўйхати ҳам керак эмас.
        self.assertNotIn("'Ҳолати':", export)
        self.assertNotIn('WORK_STATUS_LABELS', html)


class StatusChartTests(TestCase):
    def status_chart(self):
        return next(chart for chart in build_dashboard_charts(include_staff=False) if chart['key'] == 'status')

    def rows(self, chart):
        return {row['label']: row['value'] for row in chart['rows']}

    def test_counts_active_employees_by_work_status(self):
        for index in range(3):
            make_employee(f'W-{index}')                                     # NULL — ишда
        make_employee('W-BLANK', work_status='')                            # бўш қатор ҳам — ишда
        for index in range(2):
            make_employee(f'V-{index}', work_status='vacation')
        make_employee('T-1', work_status='business_trip')
        make_employee('S-1', work_status='sick_leave')
        for index in range(2):
            make_employee(f'C-{index}', work_status='childcare_leave')
        make_employee('OFF-1', is_active=False)                             # диаграммага кирмайди
        make_employee('DEL-1', work_status='vacation', is_deleted=True)     # ўчирилган — ҳисобга олинмайди

        chart = self.status_chart()

        self.assertEqual(self.rows(chart), {
            'Ишдаги ходимлар': 4,
            'Меҳнат таътилидаги ходимлар': 2,
            'Хизмат сафаридаги ходимлар': 1,
            'Касаллик таътилида': 1,
            'Бола парваришлаш таътилида': 2,
        })
        self.assertEqual(chart['title'], 'Тизимдаги ходимлар ҳолати')
        self.assertEqual(chart['note'], 'Фаол эмас ходимлар (диаграммага кирмайди): 1')
        self.assertGreater(chart['hole'], 0)                                # «бублик» диаграмма

    def test_all_five_rows_are_listed_even_when_zero_in_the_requested_order(self):
        make_employee('ONLY-1')

        chart = self.status_chart()

        self.assertEqual([row['label'] for row in chart['rows']], [
            'Ишдаги ходимлар', 'Меҳнат таътилидаги ходимлар', 'Хизмат сафаридаги ходимлар',
            'Касаллик таътилида', 'Бола парваришлаш таътилида',
        ])
        self.assertEqual([row['value'] for row in chart['rows']], [1, 0, 0, 0, 0])
        self.assertEqual(chart['note'], '')                                  # фаол эмас ходим йўқ — изоҳ йўқ

    def test_a_leave_status_does_not_remove_the_employee_from_the_other_charts(self):
        make_employee('G-1', gender='F', work_status='vacation')

        gender = next(chart for chart in build_dashboard_charts(include_staff=False) if chart['key'] == 'gender')

        self.assertEqual({row['label']: row['value'] for row in gender['rows']}['Аёллар'], 1)

    def test_dashboard_page_shows_the_status_card_below_the_chart_row(self):
        admin = User.objects.create_user(username='status_chart_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        make_employee('P-1')
        make_employee('P-2', work_status='business_trip')
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(admin)

        html = web.get('/').content.decode()

        card = html.index('Тизимдаги ходимлар ҳолати')
        self.assertGreater(card, html.index('data-chart="gender"'))          # қаторда эмас, ундан пастда
        self.assertEqual(html.count('data-chart="status"'), 1)               # карталар қаторида такрорланмаган
        table = html[card:html.index('</table>', card)]
        self.assertIn('Хизмат сафаридаги ходимлар', table)
        self.assertRegex(table, r'Ишдаги ходимлар\s*</td>\s*<td[^>]*>1</td>')
        self.assertRegex(table, r'Хизмат сафаридаги ходимлар\s*</td>\s*<td[^>]*>1</td>')
        raw = re.search(r'<script id="dashboardChartsData" type="application/json">(.*?)</script>', html, re.S).group(1)
        status = next(chart for chart in json.loads(raw) if chart['key'] == 'status')
        self.assertEqual(status['hole'], 0.52)                              # JS «бублик» чизиши учун
        self.assertEqual([row['value'] for row in status['slices']], [1, 0, 1, 0, 0])
