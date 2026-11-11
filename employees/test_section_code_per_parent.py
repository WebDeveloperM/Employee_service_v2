from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Department, ROLE_ADMIN, Section, UserProfile


SECTIONS_URL = '/api/template/sections/'


class SectionCodePerParentTests(TestCase):
    """ID отдела: верхний уровень — внутри цеха, вложенные — внутри своего родителя."""

    def setUp(self):
        admin = User.objects.create_user(username='parent_code_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)
        self.department = Department.objects.create(name='20-Цех')
        self.other_department = Department.objects.create(name='21-Цех')

    def make(self, name, code='', parent=None, department=None):
        return Section.objects.create(department=department or (parent.department if parent else self.department), name=name, code=code, parent=parent)

    def post(self, **payload):
        payload.setdefault('department_id', self.department.pk)
        return self.client.post(SECTIONS_URL, payload, format='json')

    def put(self, section, **payload):
        body = {'name': section.name, 'department_id': section.department_id, 'parent_id': section.parent_id, 'code': section.code, **payload}
        return self.client.put(f'{SECTIONS_URL}{section.pk}/', body, format='json')

    def test_every_parent_has_its_own_numbering_for_inner_sections(self):
        legal = self.make('Юридик хизмати', code='1')
        hr = self.make('Кадрлар хизмати', code='2')

        first = self.post(name='Юридик бўлим', code='1', parent_id=legal.pk)
        second = self.post(name='Кадрлар бўлими', code='1', parent_id=hr.pk)   # бошқа ота — ID 1 яна мумкин

        self.assertEqual((first.status_code, second.status_code), (201, 201), (first.content, second.content))

    def test_exact_case_from_the_sections_page_parent_gets_the_id_its_child_already_has(self):
        # Скриншот: у «Юридик бўлим» (ичида) ID = 1, «Юридик хизмати»га ҳам ID = 1 бермоқчи.
        parent = self.make('Юридик хизмати')
        self.make('Юридик бўлим', code='1', parent=parent)

        response = self.put(parent, code='1')

        self.assertEqual(response.status_code, 200, response.content)
        parent.refresh_from_db()
        self.assertEqual(parent.code, '1')

    def test_inner_section_may_reuse_the_id_of_a_top_level_section(self):
        self.make('Бошқа хизмат', code='1')
        parent = self.make('Юридик хизмати', code='2')

        self.assertEqual(self.post(name='Юридик бўлим', code='1', parent_id=parent.pk).status_code, 201)

    def test_siblings_cannot_share_an_id(self):
        parent = self.make('Юридик хизмати')
        self.make('Биринчи', code='1', parent=parent)

        response = self.post(name='Иккинчи', code='1', parent_id=parent.pk)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], ['Бу юқори бўлимда шундай ID ли ички бўлим аллақачон мавжуд.'])
        self.assertFalse(Section.objects.filter(name='Иккинчи').exists())

    def test_top_level_sections_still_need_unique_ids_inside_the_department(self):
        self.make('Биринчи', code='1')

        response = self.post(name='Иккинчи', code='1')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], ['Бу цехда шундай ID ли асосий бўлим аллақачон мавжуд.'])
        self.assertEqual(self.post(name='Учинчи', code='1', department_id=self.other_department.pk).status_code, 201)

    def test_changing_the_id_of_a_section_keeps_working(self):
        parent = self.make('Ота')
        first = self.make('Биринчи', code='1', parent=parent)
        self.make('Иккинчи', code='2', parent=parent)

        self.assertEqual(self.put(first, code='1').status_code, 200)   # ўз ID си — тўқнашув эмас
        self.assertEqual(self.put(first, code='2').status_code, 400)   # аканинг ID си
        self.assertEqual(self.put(first, code='3').status_code, 200)

    def test_moving_a_section_checks_the_ids_of_its_new_siblings(self):
        old_parent = self.make('Эски ота')
        new_parent = self.make('Янги ота')
        self.make('Янгидаги', code='1', parent=new_parent)
        moved = self.make('Кўчадиган', code='1', parent=old_parent)

        clash = self.put(moved, parent_id=new_parent.pk)
        fixed = self.put(moved, parent_id=new_parent.pk, code='2')

        self.assertEqual(clash.status_code, 400, clash.content)
        self.assertEqual(fixed.status_code, 200, fixed.content)

    def test_detaching_to_the_top_level_checks_top_level_ids(self):
        self.make('Асосий', code='1')
        parent = self.make('Ота', code='2')
        child = self.make('Бола', code='1', parent=parent)

        self.assertEqual(self.put(child, parent_id=None).status_code, 400)
        self.assertEqual(self.put(child, parent_id=None, code='3').status_code, 200)

    def test_moving_without_sending_the_id_uses_the_current_one(self):
        old_parent = self.make('Эски ота')
        new_parent = self.make('Янги ота')
        self.make('Янгидаги', code='1', parent=new_parent)
        moved = self.make('Кўчадиган', code='1', parent=old_parent)

        response = self.client.put(f'{SECTIONS_URL}{moved.pk}/', {'name': 'Кўчадиган', 'department_id': self.department.pk, 'parent_id': new_parent.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)

    def test_empty_ids_are_never_a_conflict(self):
        parent = self.make('Ота')

        self.assertEqual(self.post(name='А', parent_id=parent.pk).status_code, 201)
        self.assertEqual(self.post(name='Б', parent_id=parent.pk).status_code, 201)
        self.assertEqual(self.post(name='В').status_code, 201)
        self.assertEqual(self.post(name='Г').status_code, 201)

    def test_list_keeps_a_sensible_tree_order_when_ids_repeat_under_different_parents(self):
        a = self.make('A', code='1')
        b = self.make('B', code='2')
        self.make('A-2', code='2', parent=a)
        self.make('A-1', code='1', parent=a)
        self.make('B-1', code='1', parent=b)

        response = self.client.get(f'{SECTIONS_URL}?department_id={self.department.pk}&page_size=100').json()['data']

        self.assertEqual([item['name'] for item in response], ['A', 'A-1', 'A-2', 'B', 'B-1'])

    def test_database_enforces_the_same_rule(self):
        parent = self.make('Ота', code='1')
        other = self.make('Бошқа ота', code='2')
        self.make('Бола 1', code='1', parent=parent)
        self.make('Бола 1 (бошқа ота)', code='1', parent=other)      # бошқа ота — мумкин
        self.make('Бола 2', code='', parent=parent)
        self.make('Бола 3', code='', parent=parent)                  # бўш ID чекланмаган

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make('Такрор (ички)', code='1', parent=parent)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make('Такрор (асосий)', code='1')                   # асосийлар орасида 1 банд
        self.make('Бошқа цех', code='1', department=self.other_department)  # бошқа цех — мумкин
