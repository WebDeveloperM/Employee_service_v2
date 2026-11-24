"""«Св.шт.» (штатдан ташқари) бўлими «Шогиртлар» каби штатсиз: ходимлари рўйхатда, лекин штат ва бўш ўринларга кирмайди."""
from django.test import SimpleTestCase

from .dashboard_stats import staff_chart
from .models import Section
from .staff import collect_staff_stats, is_no_staff_section_name
from .test_staff import POSITIONS_URL, TREE_URL, StaffTestCase


class OverStaffNameTests(SimpleTestCase):
    def test_spellings_of_sv_sht_are_recognised(self):
        for name in ('св.шт.', 'Св.шт.', 'СВ.ШТ.', 'св.шт', 'св. шт.', ' СВ  ШТ ', 'св шт', 'сверх штата', 'Сверх штат',
                     'sv.sht.', 'SV.SHT', 'cb.шт.', 'CB.ШТ.', 'СВ.ШT.'):
            with self.subTest(name=name):
                self.assertTrue(is_no_staff_section_name(name))

    def test_apprentices_still_have_no_staff(self):
        for name in ('Шогиртлар', 'шогирдлар', 'Shogirtlar'):
            with self.subTest(name=name):
                self.assertTrue(is_no_staff_section_name(name))

    def test_other_names_keep_their_staff(self):
        for name in ('Свободный отдел', 'Св', 'Св.штатные работы', 'Санитария', 'Сув таъминоти', 'Юридик бўлим', 'Сверхурочные', '', None):
            with self.subTest(name=name):
                self.assertFalse(is_no_staff_section_name(name))


class OverStaffSectionTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.over_staff = Section.objects.create(department=self.department, name='св.шт.', code='9')

    def employees_url(self):
        return f'/api/template/staff/sections/{self.over_staff.pk}/employees/'

    def test_tree_marks_the_section_as_without_staff(self):
        nodes = {}
        for department in self.client.get(TREE_URL).json()['data']:
            for section in department['children']:
                nodes[section['name']] = section

        self.assertTrue(nodes['св.шт.']['no_staff'])
        self.assertFalse(nodes['Юридик бўлим']['no_staff'])

    def test_a_position_cannot_be_added_to_it(self):
        response = self.client.post(POSITIONS_URL, {'section_id': self.over_staff.pk, 'name': 'Слесарь', 'staff_count': 1}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.over_staff.staff_positions.count(), 0)

    def test_its_employees_are_listed_but_take_no_staff_places(self):
        self.make_position('Слесарь', staff_count=2)
        listed = [
            self.make_employee('св.шт.', department=self.department, section=self.over_staff),
            self.make_employee('Слесарь', department=self.department, section=self.over_staff),
        ]

        body = self.client.get(self.employees_url()).json()
        stats = collect_staff_stats()
        tree = {item['name']: item for item in self.client.get(TREE_URL).json()['data']}

        self.assertEqual({item['id'] for item in body['data']}, {item.pk for item in listed})   # рўйхати бор
        self.assertTrue(body['unit']['no_staff'])
        self.assertEqual((tree['1 цех']['staff_total'], tree['1 цех']['vacant_total']), (2, 2))   # штат ва бўш ўрин ўзгармайди
        self.assertEqual(stats.own[('department', self.department.pk)]['vacant'], 2)
        self.assertNotIn(('section', self.over_staff.pk), stats.own)

    def test_old_positions_of_the_section_are_ignored_in_every_total(self):
        self.make_position('Слесарь', staff_count=4, section=self.over_staff)
        self.make_position('Токарь', staff_count=1)
        self.make_employee('Токарь', department=self.department)

        dashboard = {row['label']: row['value'] for row in staff_chart()['rows']}

        self.assertEqual((dashboard['Штат бирлиги'], dashboard['Бўш иш ўринлари']), (1, 0))

    def test_employees_can_be_added_and_removed_like_in_apprentices(self):
        employee = self.make_employee('Слесарь', department=self.other_department)

        added = self.client.post(self.employees_url(), {'employee_id': employee.pk}, format='json')
        employee.refresh_from_db()
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual((employee.department_id, employee.section_id), (self.department.pk, self.over_staff.pk))

        removed = self.client.delete(f'{self.employees_url()}{employee.pk}/')
        employee.refresh_from_db()
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertEqual((employee.department_id, employee.section_id), (self.department.pk, None))

    def test_moving_an_employee_into_it_frees_the_place_of_his_old_position(self):
        self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)
        self.assertEqual(collect_staff_stats().own[('department', self.department.pk)]['vacant'], 0)

        self.client.post(self.employees_url(), {'employee_id': employee.pk}, format='json')

        self.assertEqual(collect_staff_stats().own[('department', self.department.pk)]['vacant'], 1)
