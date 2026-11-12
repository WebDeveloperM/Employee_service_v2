import json
import re
from decimal import Decimal

from django.core.management import call_command
from django.test import Client

from .dashboard_stats import build_dashboard_charts
from .models import StaffPosition
from .staff import plain_number, vacant_places
from .test_staff import POSITIONS_URL, StaffTestCase, TREE_URL


class FractionalStaffCountTests(StaffTestCase):
    """«Штатлар сони» дробли бўлиши мумкин: 0.5, 0.25, 1.5 (ярим ставка ва ҳ.к.)."""

    def post_position(self, staff_count, name='Токарь', **extra):
        return self.client.post(
            POSITIONS_URL, {'department_id': self.department.pk, 'name': name, 'staff_count': staff_count, **extra}, format='json',
        )

    def employees_url(self, position):
        return f'{POSITIONS_URL}{position.pk}/employees/'

    def assign(self, position, employee):
        return self.client.post(self.employees_url(position), {'employee_id': employee.pk}, format='json')

    # --- сақлаш ва қайтариш ---

    def test_fractional_values_are_saved_and_returned_as_numbers_not_strings(self):
        for index, value in enumerate((0.5, 0.25, 1.25, '2.75', 10, 7)):
            with self.subTest(value=value):
                response = self.post_position(value, name=f'Лавозим {index}')

                self.assertEqual(response.status_code, 201, response.content)
                data = response.json()['data']
                self.assertIsInstance(data['staff_count'], (int, float))   # «0.50» матн эмас — сон
                self.assertEqual(data['staff_count'], float(value))
                self.assertEqual(data['vacant'], float(value))              # ҳали ҳеч ким банд қилмаган
                self.assertEqual(StaffPosition.objects.get(name=f'Лавозим {index}').staff_count, Decimal(str(value)))

    def test_default_is_still_one_and_integers_keep_working(self):
        response = self.client.post(POSITIONS_URL, {'department_id': self.department.pk, 'name': 'Юрист'}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(StaffPosition.objects.get().staff_count, 1)
        self.assertEqual(response.json()['data']['staff_count'], 1)

    def test_an_existing_whole_number_position_can_be_edited_to_a_fraction(self):
        position = self.make_position('Слесарь', staff_count=2)

        response = self.client.put(f'{POSITIONS_URL}{position.pk}/', {'name': 'Слесарь', 'staff_count': 0.5}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        position.refresh_from_db()
        self.assertEqual(position.staff_count, Decimal('0.5'))
        self.assertEqual(response.json()['data']['staff_count'], 0.5)

    def test_invalid_values_are_rejected_with_uzbek_messages(self):
        for value in (0, 0.001, 1.255, -0.5, 'abc', 10 ** 8):
            with self.subTest(value=value):
                response = self.post_position(value)

                self.assertEqual(response.status_code, 400, response.content)
                body = response.json()
                self.assertIn('staff_count', body)
                text = str(body)
                self.assertNotIn('Ensure', text)            # DRF нинг инглизча хабарлари чиқмаслиги керак
                self.assertNotIn('valid number', text)
        self.assertFalse(StaffPosition.objects.exists())

    # --- бўш ўринлар ва тўлиш қоидаси ---

    def test_vacancy_math_keeps_fractions_and_never_goes_below_zero(self):
        self.assertEqual(vacant_places(Decimal('1.5'), 1), Decimal('0.5'))
        self.assertEqual(vacant_places(Decimal('0.5'), 0), Decimal('0.5'))
        self.assertEqual(vacant_places(Decimal('0.5'), 1), 0)
        self.assertEqual(vacant_places(Decimal('1.5'), 2), 0)
        self.assertEqual(vacant_places(3, 1), 2)                  # бутун сон ва float ҳам ишлайди
        self.assertEqual(vacant_places(0.1, 0), Decimal('0.1'))   # float 0.1 ёмон «шовқин» билан кирмайди

    def test_positions_list_shows_fractional_vacancy(self):
        self.make_position('Токарь', staff_count=Decimal('1.5'))
        self.make_employee('Токарь', department=self.department)

        item = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').json()['data'][0]

        self.assertEqual((item['staff_count'], item['occupied'], item['vacant']), (1.5, 1, 0.5))

    def test_half_rate_position_takes_exactly_one_person(self):
        position = self.make_position('Токарь', staff_count=Decimal('0.5'))
        first = self.make_employee('', department=self.other_department)
        second = self.make_employee('', department=self.other_department)

        accepted = self.assign(position, first)
        refused = self.assign(position, second)

        self.assertEqual(accepted.status_code, 201, accepted.content)
        self.assertEqual(accepted.json()['position']['vacant'], 0)
        self.assertEqual(refused.status_code, 400, refused.content)

    def test_one_and_a_half_positions_take_two_people_but_not_three(self):
        position = self.make_position('Токарь', staff_count=Decimal('1.5'))
        people = [self.make_employee('', department=self.other_department) for _ in range(3)]

        statuses = [self.assign(position, person).status_code for person in people]

        self.assertEqual(statuses, [201, 201, 400])

    # --- йиғиндилар ---

    def test_tree_totals_add_fractions_exactly(self):
        self.make_position('А', staff_count=Decimal('0.1'))
        self.make_position('Б', staff_count=Decimal('0.2'))
        self.make_position('В', staff_count=Decimal('1.5'), section=self.section)

        data = self.client.get(TREE_URL).json()['data']

        node = next(item for item in data if item['id'] == self.department.pk)
        self.assertEqual(node['staff_total'], 1.8)       # 0.1 + 0.2 + 1.5, 1.8000000000000003 эмас
        self.assertEqual(node['vacant_total'], 1.8)
        section = node['children'][0]
        self.assertEqual((section['staff_total'], section['vacant_total']), (1.5, 1.5))

    def test_plain_number_shows_whole_numbers_without_decimals(self):
        self.assertEqual(plain_number(Decimal('3.00')), 3)
        self.assertIsInstance(plain_number(Decimal('3.00')), int)
        self.assertEqual(plain_number(Decimal('3.50')), 3.5)
        self.assertIsInstance(plain_number(Decimal('3.50')), float)

    def test_dashboard_staff_chart_uses_plain_numbers(self):
        self.make_position('Слесарь', staff_count=Decimal('0.5'))
        self.make_position('Токарь', staff_count=Decimal('1.25'))
        self.make_employee('Слесарь', department=self.department)

        chart = next(item for item in build_dashboard_charts(include_staff=True) if item['key'] == 'staff')

        rows = {row['label']: row['value'] for row in chart['rows']}
        self.assertEqual(rows, {'Штат бирлиги': 1.75, 'Банд ўринлар': 0.5, 'Бўш иш ўринлари': 1.25})
        for value in rows.values():
            self.assertIsInstance(value, (int, float))   # Decimal эмас: json_script уни матнга айлантириб юборарди

    def test_dashboard_page_embeds_fractions_as_json_numbers(self):
        self.make_position('Токарь', staff_count=Decimal('1.25'))
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/').content.decode()

        raw = re.search(r'<script id="dashboardChartsData" type="application/json">(.*?)</script>', html, re.S).group(1)
        staff = next(chart for chart in json.loads(raw) if chart['key'] == 'staff')
        total = next(row for row in staff['rows'] if row['label'] == 'Штат бирлиги')
        self.assertEqual(total['value'], 1.25)
        self.assertIsInstance(total['value'], float)       # матн ("1.25") эмас — JS да диаграмма тўғри чизилади

    # --- экран ---

    def test_staff_form_input_accepts_fractions(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/staff/').content.decode()

        self.assertRegex(html, r'id="positionStaffCount"[^>]*min="0.01"[^>]*step="0.01"[^>]*inputmode="decimal"')

    def test_model_and_migrations_agree(self):
        # Миграция файли моделга мос: «No changes detected», акс ҳолда тест йиқилади (SystemExit).
        call_command('makemigrations', '--check', '--dry-run', verbosity=0)
