from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Department, Section


SECTIONS_URL = '/api/template/sections/'


class SectionCodePerDepartmentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=User.objects.create_user(username='code_user', password='secret123'))
        self.first = Department.objects.create(name='1-Цех')
        self.second = Department.objects.create(name='Раҳбарият')

    def create(self, name, code=None, department=None):
        payload = {'name': name, 'department_id': (department or self.first).pk}
        if code is not None:
            payload['code'] = code
        return self.client.post(SECTIONS_URL, payload, format='json')

    def update(self, section, **fields):
        payload = {'name': section.name, 'department_id': section.department_id, **fields}
        return self.client.put(f'{SECTIONS_URL}{section.pk}/', payload, format='json')

    def test_every_department_can_start_its_own_numbering_from_one(self):
        # Ровно сценарий со страницы «Отделы»: в 1-Цех уже есть ID 1, в новом цехе тоже нужен ID 1.
        self.assertEqual(self.create('1-Цех Технология', code='1').status_code, 201)

        response = self.create('Бош технолог бўлими', code='1', department=self.second)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            sorted(Section.objects.filter(code='1').values_list('department__name', flat=True)),
            ['1-Цех', 'Раҳбарият'],
        )

    def test_duplicate_inside_one_department_is_rejected_with_a_message_on_the_code_field(self):
        self.create('Первый', code='7')

        response = self.create('Второй', code='7')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ['code'])
        self.assertIn('Бу цехда', str(response.data['code'][0]))

    def test_codes_are_compared_after_trimming(self):
        self.create('Первый', code='7')

        self.assertEqual(self.create('Второй', code=' 7 ').status_code, 400)

    def test_editing_a_section_to_a_code_used_in_its_department_is_rejected(self):
        self.create('Первый', code='1')
        second = Section.objects.get(pk=self.create('Второй', code='2').json()['data']['id'])

        self.assertEqual(self.update(second, code='1').status_code, 400)
        self.assertEqual(self.update(second, code='3').status_code, 200)
        second.refresh_from_db()
        self.assertEqual(second.code, '3')

    def test_moving_a_section_to_a_department_that_already_uses_its_code_is_rejected(self):
        self.create('В первом', code='1')
        in_second = Section.objects.get(pk=self.create('Во втором', code='1', department=self.second).json()['data']['id'])

        clash = self.client.put(
            f'{SECTIONS_URL}{in_second.pk}/', {'name': 'Во втором', 'code': '1', 'department_id': self.first.pk}, format='json',
        )
        # Без code в теле код берётся из самого отдела — столкновение всё равно ловится.
        clash_without_code = self.client.put(
            f'{SECTIONS_URL}{in_second.pk}/', {'name': 'Во втором', 'department_id': self.first.pk}, format='json',
        )

        self.assertEqual(clash.status_code, 400)
        self.assertEqual(clash_without_code.status_code, 400)
        in_second.refresh_from_db()
        self.assertEqual(in_second.department_id, self.second.pk)

    def test_keeping_own_code_on_save_is_not_a_conflict_and_missing_code_does_not_wipe_it(self):
        section = Section.objects.get(pk=self.create('Первый', code='5').json()['data']['id'])

        self.assertEqual(self.update(section, code='5').status_code, 200)
        self.assertEqual(self.client.put(f'{SECTIONS_URL}{section.pk}/', {'name': 'Первый', 'department_id': self.first.pk}, format='json').status_code, 200)
        section.refresh_from_db()
        self.assertEqual(section.code, '5')

    def test_sections_without_code_are_still_unlimited_and_names_stay_unique_per_department(self):
        self.assertEqual(self.create('А').status_code, 201)
        self.assertEqual(self.create('Б', code='').status_code, 201)
        self.assertEqual(self.create('В', code='').status_code, 201)

        duplicate_name = self.create('А')

        self.assertEqual(duplicate_name.status_code, 400)
        self.assertEqual(self.create('А', department=self.second).status_code, 201)

    def test_public_api_follows_the_same_rule(self):
        self.client.post('/api/v1/sections/', {'name': 'Первый', 'code': '1', 'department_id': self.first.pk}, format='json')

        same_department = self.client.post('/api/v1/sections/', {'name': 'Второй', 'code': '1', 'department_id': self.first.pk}, format='json')
        other_department = self.client.post('/api/v1/sections/', {'name': 'Третий', 'code': '1', 'department_id': self.second.pk}, format='json')

        self.assertEqual(same_department.status_code, 400)
        self.assertEqual(other_department.status_code, 201)

    def test_database_itself_enforces_uniqueness_per_department_only(self):
        Section.objects.create(department=self.first, name='А', code='9')
        Section.objects.create(department=self.second, name='А', code='9')  # другой цех — можно

        with self.assertRaises(IntegrityError), transaction.atomic():
            Section.objects.create(department=self.first, name='Б', code='9')

        Section.objects.create(department=self.first, name='В', code='')
        Section.objects.create(department=self.first, name='Г', code='')  # пустой ID не ограничен
