from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Department, ROLE_ADMIN, UserProfile


DEPARTMENTS_URL = '/api/template/departments/'


class DepartmentSortOrderTests(TestCase):
    """«Цех ID си» в форме цеха — это Department.sort_order: по нему сортируется таблица."""

    def setUp(self):
        admin = User.objects.create_user(username='department_order_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        self.admin = admin
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def post(self, **payload):
        return self.client.post(DEPARTMENTS_URL, payload, format='json')

    def put(self, department, **payload):
        body = {'name': department.name, **payload}
        return self.client.put(f'{DEPARTMENTS_URL}{department.pk}/', body, format='json')

    def names(self, response):
        return [item['name'] for item in response.json()['data']]

    def test_department_is_created_with_the_typed_id(self):
        response = self.post(name='Янги цех', sort_order=77)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['data']['sort_order'], 77)
        self.assertEqual(Department.objects.get(name='Янги цех').sort_order, 77)

    def test_typed_id_wins_over_the_number_in_the_name(self):
        self.post(name='5-Цех', sort_order=99)

        self.assertEqual(Department.objects.get(name='5-Цех').sort_order, 99)

    def test_zero_which_is_the_form_default_picks_the_number_from_the_name(self):
        response = self.post(name='20-Цех Завод бошқармаси', sort_order=0)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['data']['sort_order'], 20)

    def test_zero_without_a_number_in_the_name_takes_the_next_free_id(self):
        Department.objects.create(name='3-Цех')
        Department.objects.create(name='9-Цех')

        response = self.post(name='Раҳбарият', sort_order=0)

        self.assertEqual(response.json()['data']['sort_order'], 10)

    def test_id_is_optional(self):
        response = self.post(name='7-Цех')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['data']['sort_order'], 7)

    def test_invalid_ids_are_rejected_with_uzbek_messages(self):
        for value, message in ((-1, 'ID манфий бўлмаслиги керак.'), ('abc', 'ID бутун сон бўлиши керак.'), (1.5, 'ID бутун сон бўлиши керак.')):
            with self.subTest(sort_order=value):
                response = self.post(name=f'Цех {value}', sort_order=value)

                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()['sort_order'], [message])
        self.assertFalse(Department.objects.filter(name__startswith='Цех ').exists())

    def test_the_system_id_cannot_be_chosen_through_the_form_field(self):
        response = self.post(name='Тизим ID', sort_order=5, id=900001)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotEqual(response.json()['data']['id'], 900001)
        self.assertEqual(response.json()['data']['sort_order'], 5)

    def test_id_of_an_existing_department_can_be_edited(self):
        department = Department.objects.create(name='Раҳбарият', sort_order=42)

        response = self.put(department, sort_order=1)

        self.assertEqual(response.status_code, 200, response.content)
        department.refresh_from_db()
        self.assertEqual(department.sort_order, 1)

    def test_editing_without_the_id_keeps_the_current_one(self):
        department = Department.objects.create(name='Раҳбарият', sort_order=42)

        response = self.put(department, boss_full_name='Бошлиқ')

        self.assertEqual(response.status_code, 200, response.content)
        department.refresh_from_db()
        self.assertEqual((department.sort_order, department.boss_full_name), (42, 'Бошлиқ'))

    def test_table_is_ordered_by_the_id_and_changing_the_id_moves_the_row(self):
        first = Department.objects.create(name='А цех', sort_order=1)
        Department.objects.create(name='Б цех', sort_order=2)
        last = Department.objects.create(name='В цех', sort_order=42)

        self.assertEqual(self.names(self.client.get(f'{DEPARTMENTS_URL}?page_size=100')), ['А цех', 'Б цех', 'В цех'])

        self.put(last, sort_order=1)   # «В цех» энди 1, «А цех» ҳам 1 — тенг бўлса, ном бўйича
        self.put(first, sort_order=50)

        self.assertEqual(self.names(self.client.get(f'{DEPARTMENTS_URL}?page_size=100')), ['В цех', 'Б цех', 'А цех'])

    def test_id_order_holds_across_pages(self):
        for position in (50, 40, 60, 30):
            Department.objects.create(name=f'Цех {position}', sort_order=position)

        first = self.client.get(f'{DEPARTMENTS_URL}?page_size=2&page=1')
        second = self.client.get(f'{DEPARTMENTS_URL}?page_size=2&page=2')

        self.assertEqual(self.names(first) + self.names(second), ['Цех 30', 'Цех 40', 'Цех 50', 'Цех 60'])

    def test_departments_page_has_the_id_field_with_default_zero(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        departments_html = web.get('/departments/').content.decode()
        sections_html = web.get('/sections/').content.decode()

        self.assertIn('Цех ID си', departments_html)
        self.assertRegex(departments_html, r'<input type="number" id="departmentSortOrder"[^>]*value="0"')
        self.assertNotIn('id="departmentSortOrder"', sections_html)
