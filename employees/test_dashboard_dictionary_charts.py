"""Бош саҳифа: Иш тартиби, Лавозим тоифаси ва Ходимлар тоифаси бўйича диаграммалар."""
import json
import re

from django.contrib.auth.models import User
from django.test import Client, TestCase

from .dashboard_stats import CATEGORY_COLORS, OTHER_COLOR, UNSET_LABEL, build_dashboard_charts
from .models import Employee, EmployeeCategory, PositionCategory, ROLE_ADMIN, UserProfile, WorkSchedule


def make_employee(tabel, **fields):
    return Employee.objects.create(first_name='Ism', last_name='Familiya', tabel_number=tabel, **fields)


def chart(key):
    return next(item for item in build_dashboard_charts(include_staff=False) if item['key'] == key)


def counts(item):
    return {row['label']: row['value'] for row in item['rows']}


def colors(item):
    return {row['label']: row['color'] for row in item['rows']}


class WorkScheduleChartTests(TestCase):
    def test_counts_active_employees_per_schedule_and_lists_the_unselected(self):
        five_day = WorkSchedule.objects.get(name='5 кунлик иш ҳафтасида')
        shift = WorkSchedule.objects.get(name='Сменада')
        for index in range(3):
            make_employee(f'D-{index}', work_schedule=five_day)
        make_employee('S-1', work_schedule=shift)
        make_employee('N-1')
        make_employee('N-2')

        schedule = chart('work_schedule')

        self.assertEqual(schedule['title'], 'Иш тартиби бўйича статистика')
        self.assertEqual(counts(schedule), {'5 кунлик иш ҳафтасида': 3, 'Сменада': 1, UNSET_LABEL: 2})
        self.assertEqual([row['label'] for row in schedule['rows']], ['5 кунлик иш ҳафтасида', 'Сменада', UNSET_LABEL])   # id тартиби, танланмаган — охирида

    def test_only_active_not_deleted_employees_are_counted(self):
        shift = WorkSchedule.objects.get(name='Сменада')
        make_employee('A-1', work_schedule=shift)
        make_employee('A-2', work_schedule=shift, is_active=False)
        make_employee('A-3', work_schedule=shift, is_deleted=True)

        self.assertEqual(counts(chart('work_schedule'))['Сменада'], 1)

    def test_a_new_schedule_appears_with_its_own_color_and_keeps_the_others_colors(self):
        before = colors(chart('work_schedule'))
        WorkSchedule.objects.create(name='Тунги смена')
        after = colors(chart('work_schedule'))

        self.assertEqual(after['Тунги смена'], CATEGORY_COLORS[2])
        for label in ('5 кунлик иш ҳафтасида', 'Сменада'):
            self.assertEqual(after[label], before[label])      # мавжудлари қайта бўялмайди
        self.assertEqual((after['5 кунлик иш ҳафтасида'], after['Сменада']), CATEGORY_COLORS[:2])

    def test_a_schedule_without_employees_is_still_listed_with_zero(self):
        self.assertEqual(counts(chart('work_schedule')), {'5 кунлик иш ҳафтасида': 0, 'Сменада': 0})   # ходимлар йўқ — «Танланмаган» ҳам йўқ

    def test_pie_slices_are_the_table_rows_and_it_is_a_plain_pie(self):
        make_employee('Z-1', work_schedule=WorkSchedule.objects.get(name='Сменада'))

        schedule = chart('work_schedule')

        self.assertEqual(schedule['slices'], schedule['rows'])
        self.assertEqual(schedule['hole'], 0)


class CategoryChartTests(TestCase):
    def test_position_category_counts_follow_the_dictionary_order(self):
        manager = PositionCategory.objects.get(name='Раҳбар')
        worker = PositionCategory.objects.get(name='Ишчи')
        make_employee('M-1', position_category=manager)
        make_employee('W-1', position_category=worker)
        make_employee('W-2', position_category=worker)
        make_employee('N-1')

        item = chart('position_category')

        self.assertEqual(item['title'], 'Лавозим тоифаси бўйича статистика')
        self.assertEqual(
            [(row['label'], row['value']) for row in item['rows']],
            [('Раҳбар', 1), ('Мутахассис', 0), ('Ишчи', 2), ('Хизматчи', 0), (UNSET_LABEL, 1)],
        )

    def test_employee_category_counts(self):
        make_employee('E-1', employee_category=EmployeeCategory.objects.get(name='Техник ходимлар'))
        make_employee('E-2', employee_category=EmployeeCategory.objects.get(name='Мутахассислар'))
        make_employee('E-3', employee_category=EmployeeCategory.objects.get(name='Мутахассислар'))

        item = chart('employee_category')

        self.assertEqual(item['title'], 'Ходимлар тоифаси бўйича статистика')
        self.assertEqual(counts(item), {
            'Бошқарув ходимлари': 0, 'Мутахассислар': 2, 'Техник ходимлар': 1,
            'Хизмат кўрсатувчи ходимлар': 0, 'Ишлаб чиқариш ходимлари': 0,
        })

    def test_colors_are_the_palette_in_fixed_order_and_never_repeat(self):
        item = chart('employee_category')

        self.assertEqual([row['color'] for row in item['rows']], list(CATEGORY_COLORS[:5]))
        self.assertEqual(len(set(CATEGORY_COLORS)), len(CATEGORY_COLORS))

    def test_values_beyond_the_palette_fold_into_others_instead_of_a_generated_color(self):
        for index in range(len(CATEGORY_COLORS) + 2 - PositionCategory.objects.count()):
            PositionCategory.objects.create(name=f'Қўшимча {index}')
        names = list(PositionCategory.objects.order_by('id').values_list('name', flat=True))
        self.assertEqual(len(names), len(CATEGORY_COLORS) + 2)
        make_employee('F-1', position_category=PositionCategory.objects.get(name=names[-1]))
        make_employee('F-2', position_category=PositionCategory.objects.get(name=names[-2]))
        make_employee('F-3', position_category=PositionCategory.objects.get(name=names[0]))

        item = chart('position_category')

        self.assertEqual(len(item['rows']), len(CATEGORY_COLORS) + 1)             # 8 та қиймат + «Бошқалар»
        self.assertEqual(item['rows'][-1], {'label': 'Бошқалар', 'value': 2, 'color': OTHER_COLOR})
        self.assertEqual(item['rows'][0]['value'], 1)
        self.assertEqual([row['color'] for row in item['rows'][:-1]], list(CATEGORY_COLORS))

    def test_table_rows_add_up_to_the_active_employees(self):
        make_employee('T-1', position_category=PositionCategory.objects.get(name='Раҳбар'))
        make_employee('T-2')
        make_employee('T-3', is_active=False, position_category=PositionCategory.objects.get(name='Раҳбар'))

        total = sum(row['value'] for row in chart('position_category')['rows'])

        self.assertEqual(total, Employee.objects.filter(is_deleted=False, is_active=True).count())


class DashboardPageLayoutTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='dict_charts_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        make_employee('P-1', work_schedule=WorkSchedule.objects.get(name='Сменада'),
                      position_category=PositionCategory.objects.get(name='Ишчи'),
                      employee_category=EmployeeCategory.objects.get(name='Мутахассислар'))
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(admin)
        self.html = web.get('/').content.decode()

    def test_rows_are_status_with_schedule_then_position_with_employee_category(self):
        order = [self.html.index(f'data-chart="{key}"') for key in ('gender', 'status', 'work_schedule', 'position_category', 'employee_category')]

        self.assertEqual(order, sorted(order))

    def test_second_and_third_rows_are_two_equal_columns(self):
        grids = re.findall(r'<div class="grid gap-6 lg:auto-rows-fr lg:grid-cols-2">(.*?)(?=<div class="grid gap-6|</section>)', self.html, re.S)

        self.assertEqual(len(grids), 2)
        self.assertEqual([re.findall(r'data-chart="(\w+)"', grid) for grid in grids], [['status', 'work_schedule'], ['position_category', 'employee_category']])

    def test_every_chart_has_its_table_and_a_pie_container(self):
        raw = re.search(r'<script id="dashboardChartsData" type="application/json">(.*?)</script>', self.html, re.S).group(1)
        charts = {item['key']: item for item in json.loads(raw)}

        self.assertEqual(self.html.count('class="pie-chart '), len(charts))
        self.assertEqual(charts['work_schedule']['rows'][1], {'label': 'Сменада', 'value': 1, 'color': CATEGORY_COLORS[1]})
        for title, value in (('Иш тартиби бўйича статистика', 'Сменада'), ('Лавозим тоифаси бўйича статистика', 'Ишчи'), ('Ходимлар тоифаси бўйича статистика', 'Мутахассислар')):
            card = self.html[self.html.index(title):]
            self.assertIn(value, card[:card.index('</table>')])
