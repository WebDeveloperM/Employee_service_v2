from rest_framework.test import APIClient

from .models import Employee, Section
from .test_staff import POSITIONS_URL, TREE_URL, StaffTestCase


class StaffTreeSectionOrderTests(StaffTestCase):
    def test_sections_in_the_tree_are_ordered_by_id_code(self):
        # Как на странице «Отделы»: код 2 создан раньше кода 1, а «1-блок» по названию идёт раньше «1-Цех …».
        for code, name in (('2', '1-блок'), ('1', '1-Цех Технология'), ('10', '2-блок'), ('', 'Без кода Б'), ('', 'Без кода А'), ('A-5', 'Буквенный')):
            Section.objects.create(department=self.department, code=code, name=name)
        Section.objects.filter(pk=self.section.pk).delete()  # «Юридик бўлим» из setUp в этом тесте не нужен

        tree = self.client.get(TREE_URL).json()['data']

        children = next(node for node in tree if node['name'] == '1 цех')['children']
        self.assertEqual(
            [(child['code'], child['name']) for child in children],
            [('1', '1-Цех Технология'), ('2', '1-блок'), ('10', '2-блок'), ('A-5', 'Буквенный'), ('', 'Без кода А'), ('', 'Без кода Б')],
        )


class StaffPositionEmployeeActionsTests(StaffTestCase):
    def url(self, position, employee):
        return f'{POSITIONS_URL}{position.pk}/employees/{employee.pk}/'

    def snapshot(self, employee):
        return Employee.objects.filter(pk=employee.pk).values().get()

    def changed_fields(self, before, after):
        return {key for key in before if before[key] != after[key]}

    def test_list_includes_the_positions_of_the_same_unit_with_vacancies(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        tokar = self.make_position('Токарь', staff_count=1)
        self.make_position('Юрист', staff_count=4, section=self.section)  # другая единица
        self.make_employee('Токарь', department=self.department)

        body = self.client.get(f'{POSITIONS_URL}{slesar.pk}/employees/').json()

        self.assertEqual(
            body['unit_positions'],
            [
                {'id': slesar.pk, 'name': 'Слесарь', 'staff_count': 2, 'vacant': 2},
                {'id': tokar.pk, 'name': 'Токарь', 'staff_count': 1, 'vacant': 0},
            ],
        )

    # ---------- перевод (Таҳрирлаш) ----------

    def test_transfer_moves_employee_to_another_position_of_the_same_unit(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        tokar = self.make_position('Токарь', staff_count=2)
        employee = self.make_employee('Слесарь', department=self.department)
        before = self.snapshot(employee)

        response = self.client.patch(self.url(slesar, employee), {'position_id': tokar.pk}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertEqual(employee.position, 'Токарь')
        self.assertEqual(self.changed_fields(before, self.snapshot(employee)), {'position', 'updated_at'})

        body = response.json()
        self.assertEqual(body['data'], [])
        self.assertEqual(body['position']['occupied'], 0)
        self.assertEqual({item['id']: item['vacant'] for item in body['unit_positions']}, {slesar.pk: 2, tokar.pk: 1})

    def test_transfer_works_inside_a_section_too(self):
        first = self.make_position('Юрист', staff_count=1, section=self.section)
        second = self.make_position('Старший юрист', staff_count=1, section=self.section)
        employee = self.make_employee('Юрист', department=self.department, section=self.section)

        response = self.client.put(self.url(first, employee), {'position_id': second.pk}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.section_id, employee.position), (self.section.pk, 'Старший юрист'))

    def test_transfer_is_rejected_for_a_position_of_another_unit(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        elsewhere = self.make_position('Юрист', staff_count=2, section=self.section)
        other_department = self.make_position('Токарь', staff_count=2, department=self.other_department)
        employee = self.make_employee('Слесарь', department=self.department)

        for target in (elsewhere, other_department):
            with self.subTest(target=target.name):
                response = self.client.patch(self.url(slesar, employee), {'position_id': target.pk}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
        employee.refresh_from_db()
        self.assertEqual(employee.position, 'Слесарь')

    def test_transfer_is_rejected_without_vacancy_to_same_position_or_with_bad_input(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        full = self.make_position('Токарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)
        self.make_employee('Токарь', department=self.department)

        self.assertEqual(self.client.patch(self.url(slesar, employee), {'position_id': full.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.url(slesar, employee), {'position_id': slesar.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.url(slesar, employee), {}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.url(slesar, employee), {'position_id': 'abc'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.url(slesar, employee), {'position_id': 999999}, format='json').status_code, 400)
        employee.refresh_from_db()
        self.assertEqual(employee.position, 'Слесарь')

    def test_transfer_requires_the_employee_to_be_in_the_source_position(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        tokar = self.make_position('Токарь', staff_count=2)
        stranger = self.make_employee('Токарь', department=self.department)

        self.assertEqual(self.client.patch(self.url(slesar, stranger), {'position_id': tokar.pk}, format='json').status_code, 404)
        self.assertEqual(self.client.patch(f'{POSITIONS_URL}999999/employees/{stranger.pk}/', {'position_id': tokar.pk}, format='json').status_code, 404)

    # ---------- снятие с должности (Ўчириш) ----------

    def test_remove_clears_only_the_position_and_keeps_employee_and_unit(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        employee = self.make_employee('Слесарь', department=self.department, section=None)
        before = self.snapshot(employee)

        response = self.client.delete(self.url(slesar, employee))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())
        employee.refresh_from_db()
        self.assertEqual(employee.position, '')
        self.assertEqual(self.changed_fields(before, self.snapshot(employee)), {'position', 'updated_at'})
        self.assertEqual((employee.is_active, employee.is_deleted, employee.department_id), (True, False, self.department.pk))

        body = response.json()
        self.assertEqual(body['data'], [])
        self.assertEqual((body['position']['occupied'], body['position']['vacant']), (0, 2))

    def test_removed_employee_frees_the_place_and_can_be_added_back(self):
        slesar = self.make_position('Слесарь', staff_count=1)
        employee = self.make_employee('Слесарь', department=self.department)
        self.client.delete(self.url(slesar, employee))

        again = self.client.post(f'{POSITIONS_URL}{slesar.pk}/employees/', {'employee_id': employee.pk}, format='json')

        self.assertEqual(again.status_code, 201, again.content)
        employee.refresh_from_db()
        self.assertEqual(employee.position, 'Слесарь')

    def test_remove_requires_the_employee_to_be_in_the_position(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        stranger = self.make_employee('Токарь', department=self.department)

        self.assertEqual(self.client.delete(self.url(slesar, stranger)).status_code, 404)
        stranger.refresh_from_db()
        self.assertEqual(stranger.position, 'Токарь')

    def test_regular_user_cannot_transfer_or_remove(self):
        slesar = self.make_position('Слесарь', staff_count=2)
        tokar = self.make_position('Токарь', staff_count=2)
        employee = self.make_employee('Слесарь', department=self.department)
        client = APIClient()
        client.force_authenticate(user=self.regular)

        self.assertEqual(client.patch(self.url(slesar, employee), {'position_id': tokar.pk}, format='json').status_code, 403)
        self.assertEqual(client.delete(self.url(slesar, employee)).status_code, 403)
        employee.refresh_from_db()
        self.assertEqual(employee.position, 'Слесарь')
