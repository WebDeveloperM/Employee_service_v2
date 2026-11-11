from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, ROLE_HR, ROLE_USER, Section, StaffPosition, UserProfile
from .staff import collect_staff_stats, normalize_position


POSITIONS_URL = '/api/template/staff/positions/'
TREE_URL = '/api/template/staff/tree/'


class StaffTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self.make_user('staff_admin', ROLE_ADMIN)
        self.hr = self.make_user('staff_hr', ROLE_HR)
        self.regular = self.make_user('staff_user', ROLE_USER)

        self.department = Department.objects.create(name='1 цех', boss_full_name='Раҳбаров Р.')
        self.other_department = Department.objects.create(name='2 цех')
        self.section = Section.objects.create(department=self.department, name='Юридик бўлим')
        self.employee_seq = 0

        self.client.force_authenticate(user=self.admin)

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password='secret123')
        UserProfile.objects.create(user=user, role=role)
        return user

    def make_employee(self, position, department=None, section=None, **extra):
        self.employee_seq += 1
        return Employee.objects.create(
            first_name='Ism',
            last_name=f'Familiya{self.employee_seq}',
            tabel_number=f'T-{self.employee_seq}',
            position=position,
            department=department,
            section=section,
            **extra,
        )

    def make_position(self, name, staff_count=1, department=None, section=None, salary=None):
        return StaffPosition.objects.create(
            department=department or self.department,
            section=section,
            name=name,
            staff_count=staff_count,
            salary=salary,
        )


class StaffAccessTests(StaffTestCase):
    def test_admin_and_hr_can_open_page_and_api(self):
        for user in (self.admin, self.hr):
            with self.subTest(role=user.profile.role):
                client = APIClient()
                client.force_login(user)
                self.assertEqual(client.get('/staff/').status_code, 200)
                self.assertEqual(client.get(TREE_URL).status_code, 200)
                self.assertEqual(client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').status_code, 200)

    def test_regular_user_is_redirected_from_page_and_gets_403_from_api(self):
        client = APIClient()
        client.force_login(self.regular)

        page = client.get('/staff/')
        self.assertEqual(page.status_code, 302, page.content)
        self.assertEqual(page['Location'], '/')

        self.assertEqual(client.get(TREE_URL).status_code, 403)
        self.assertEqual(client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').status_code, 403)
        self.assertEqual(client.post(POSITIONS_URL, {'department_id': self.department.pk, 'name': 'X'}, format='json').status_code, 403)
        self.assertFalse(StaffPosition.objects.exists())

    def test_superuser_without_profile_can_open_page_and_api(self):
        # Так создаются служебные админы (createsuperuser): UserProfile у них нет,
        # но пункт меню «Настройка» им показывается, значит и страница должна открываться.
        superuser = User.objects.create_superuser(username='root_no_profile', password='secret123')
        self.assertFalse(UserProfile.objects.filter(user=superuser).exists())
        client = APIClient()
        client.force_login(superuser)

        self.assertEqual(client.get('/staff/').status_code, 200)
        self.assertEqual(client.get(TREE_URL).status_code, 200)
        created = client.post(POSITIONS_URL, {'department_id': self.department.pk, 'name': 'Слесарь'}, format='json')
        self.assertEqual(created.status_code, 201, created.content)

    def test_non_superuser_without_profile_is_rejected(self):
        orphan = User.objects.create_user(username='no_profile', password='secret123')
        client = APIClient()
        client.force_login(orphan)

        page = client.get('/staff/')
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page['Location'], '/')
        self.assertEqual(client.get(TREE_URL).status_code, 403)

    def test_anonymous_is_rejected(self):
        client = APIClient()
        self.assertIn(client.get(TREE_URL).status_code, {401, 403})
        self.assertEqual(client.get('/staff/').status_code, 302)

    def test_menu_item_is_placed_before_departments(self):
        client = APIClient()
        client.force_login(self.admin)

        html = client.get('/staff/').content.decode()

        self.assertIn('Штат бирлиги ва лавозим', html)
        self.assertLess(html.index('href="/staff/"'), html.index('href="/departments/"'))


class StaffPositionApiTests(StaffTestCase):
    def test_create_position_in_department(self):
        response = self.client.post(
            POSITIONS_URL,
            {'department_id': self.department.pk, 'name': '  Смена   бошлиғи ', 'staff_count': 6, 'salary': '4500000.50'},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.content)
        position = StaffPosition.objects.get()
        self.assertEqual(position.name, 'Смена бошлиғи')
        self.assertEqual(position.department_id, self.department.pk)
        self.assertIsNone(position.section_id)
        self.assertEqual(position.staff_count, 6)
        self.assertEqual(str(position.salary), '4500000.50')
        self.assertEqual(response.json()['data']['vacant'], 6)

    def test_create_position_in_section_derives_department(self):
        response = self.client.post(POSITIONS_URL, {'section_id': self.section.pk, 'name': 'Юрист'}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        position = StaffPosition.objects.get()
        self.assertEqual(position.section_id, self.section.pk)
        self.assertEqual(position.department_id, self.department.pk)
        self.assertEqual(position.staff_count, 1)
        self.assertIsNone(position.salary)

    def test_section_from_another_department_is_rejected(self):
        response = self.client.post(
            POSITIONS_URL,
            {'department_id': self.other_department.pk, 'section_id': self.section.pk, 'name': 'Юрист'},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(StaffPosition.objects.exists())

    def test_unit_is_required_on_create(self):
        response = self.client.post(POSITIONS_URL, {'name': 'Юрист'}, format='json')

        self.assertEqual(response.status_code, 400, response.content)

    def test_invalid_values_are_rejected(self):
        base = {'department_id': self.department.pk}
        for payload in (
            {**base, 'name': '   '},
            {**base, 'name': 'Юрист', 'staff_count': 0},
            {**base, 'name': 'Юрист', 'salary': '-1'},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(POSITIONS_URL, payload, format='json')
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(StaffPosition.objects.exists())

    def test_duplicate_name_is_rejected_per_unit_case_insensitively(self):
        self.make_position('Слесарь')

        duplicate = self.client.post(POSITIONS_URL, {'department_id': self.department.pk, 'name': ' слесарь '}, format='json')
        other_department = self.client.post(POSITIONS_URL, {'department_id': self.other_department.pk, 'name': 'Слесарь'}, format='json')
        in_section = self.client.post(POSITIONS_URL, {'section_id': self.section.pk, 'name': 'Слесарь'}, format='json')

        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertEqual(other_department.status_code, 201, other_department.content)
        self.assertEqual(in_section.status_code, 201, in_section.content)
        self.assertEqual(StaffPosition.objects.count(), 3)

    def test_update_changes_values_but_never_moves_position(self):
        position = self.make_position('Слесарь', staff_count=2)

        response = self.client.put(
            f'{POSITIONS_URL}{position.pk}/',
            {'name': 'Слесарь-ремонтник', 'staff_count': 5, 'salary': None, 'department_id': self.other_department.pk, 'section_id': self.section.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        position.refresh_from_db()
        self.assertEqual(position.name, 'Слесарь-ремонтник')
        self.assertEqual(position.staff_count, 5)
        self.assertEqual(position.department_id, self.department.pk)
        self.assertIsNone(position.section_id)

    def test_update_to_existing_name_is_rejected(self):
        self.make_position('Слесарь')
        other = self.make_position('Токарь')

        response = self.client.patch(f'{POSITIONS_URL}{other.pk}/', {'name': 'слесарь'}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        other.refresh_from_db()
        self.assertEqual(other.name, 'Токарь')

    def test_delete_position(self):
        position = self.make_position('Слесарь')

        response = self.client.delete(f'{POSITIONS_URL}{position.pk}/')

        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(StaffPosition.objects.exists())

    def test_deleting_unit_removes_its_positions_only(self):
        self.make_position('Слесарь')
        self.make_position('Юрист', section=self.section)
        kept = self.make_position('Токарь', department=self.other_department)

        self.department.delete()

        self.assertEqual(list(StaffPosition.objects.all()), [kept])


class StaffPositionListTests(StaffTestCase):
    def test_list_requires_a_unit(self):
        self.assertEqual(self.client.get(POSITIONS_URL).status_code, 400)
        self.assertEqual(self.client.get(f'{POSITIONS_URL}?department_id=999999').status_code, 404)
        self.assertEqual(self.client.get(f'{POSITIONS_URL}?section_id=999999').status_code, 404)

    def test_department_list_shows_only_its_own_positions_and_totals_include_sections(self):
        self.make_position('Смена бошлиғи', staff_count=2)
        self.make_position('Юрист', staff_count=5, section=self.section)
        self.make_position('Токарь', staff_count=9, department=self.other_department)

        department_response = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').json()
        section_response = self.client.get(f'{POSITIONS_URL}?section_id={self.section.pk}').json()

        self.assertEqual([item['name'] for item in department_response['data']], ['Смена бошлиғи'])
        self.assertEqual(department_response['count'], 1)
        self.assertEqual(department_response['unit']['boss_full_name'], 'Раҳбаров Р.')
        self.assertEqual(department_response['summary'], {
            'own_staff': 2, 'own_vacant': 2, 'total_staff': 7, 'total_vacant': 7,
        })

        self.assertEqual([item['name'] for item in section_response['data']], ['Юрист'])
        self.assertEqual(section_response['unit']['department_name'], '1 цех')
        self.assertEqual(section_response['summary'], {
            'own_staff': 5, 'own_vacant': 5, 'total_staff': 5, 'total_vacant': 5,
        })

    def test_search_and_pagination(self):
        for index in range(1, 6):
            self.make_position(f'Слесарь {index}')
        self.make_position('Токарь')

        page = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}&page_size=2&page=3').json()
        search = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}&search=токарь').json()

        self.assertEqual(page['count'], 6)
        self.assertEqual(len(page['data']), 2)
        self.assertEqual([item['name'] for item in search['data']], ['Токарь'])
        # Итоги относятся ко всей единице, а не к текущей странице или поиску.
        self.assertEqual(search['summary']['own_staff'], 6)


class StaffVacancyTests(StaffTestCase):
    def test_vacancies_count_only_active_matching_employees_of_the_same_unit(self):
        position = self.make_position('Слесарь', staff_count=4)
        self.make_employee('Слесарь', department=self.department)
        self.make_employee('  слесарь ', department=self.department)  # регистр и пробелы не важны
        self.make_employee('Слесарь', department=self.department, is_active=False)
        self.make_employee('Слесарь', department=self.department, is_deleted=True)
        self.make_employee('Слесарь', department=self.other_department)
        self.make_employee('Слесарь', department=self.department, section=self.section)  # место отдела, не цеха
        self.make_employee('Токарь', department=self.department)

        response = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').json()

        row = response['data'][0]
        self.assertEqual(row['id'], position.pk)
        self.assertEqual(row['occupied'], 2)
        self.assertEqual(row['vacant'], 2)
        self.assertEqual(response['summary']['own_vacant'], 2)

    def test_section_positions_are_matched_by_section(self):
        self.make_position('Юрист', staff_count=2, section=self.section)
        self.make_employee('Юрист', department=self.department, section=self.section)
        self.make_employee('Юрист', department=self.department)  # сотрудник цеха, не отдела

        row = self.client.get(f'{POSITIONS_URL}?section_id={self.section.pk}').json()['data'][0]

        self.assertEqual(row['occupied'], 1)
        self.assertEqual(row['vacant'], 1)

    def test_vacancies_never_go_negative(self):
        self.make_position('Слесарь', staff_count=1)
        for _ in range(3):
            self.make_employee('Слесарь', department=self.department)

        row = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').json()['data'][0]

        self.assertEqual(row['occupied'], 3)
        self.assertEqual(row['vacant'], 0)

    def test_stats_do_not_modify_employees_or_units(self):
        self.make_position('Слесарь', staff_count=2)
        employee = self.make_employee('Слесарь', department=self.department)
        before = Employee.objects.filter(pk=employee.pk).values().get()

        collect_staff_stats()
        self.client.get(TREE_URL)
        self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}')

        self.assertEqual(Employee.objects.filter(pk=employee.pk).values().get(), before)

    def test_normalize_position(self):
        self.assertEqual(normalize_position('  Смена   Бошлиғи\n'), 'смена бошлиғи')
        self.assertEqual(normalize_position(None), '')


class StaffTreeOrderTests(StaffTestCase):
    def tree_names(self):
        return [node['name'] for node in self.client.get(TREE_URL).json()['data']]

    def test_leadership_and_20th_department_come_first_then_by_sort_order(self):
        Department.objects.create(name='3-Цех Буғҳавогазтаъминоти цехи')
        Department.objects.create(name='20-Цех Завод бошқармаси')
        Department.objects.create(name='Раҳбарият')
        Department.objects.create(name='200-Цех Бошқа')

        self.assertEqual(self.tree_names(), [
            'Раҳбарият',
            '20-Цех Завод бошқармаси',
            '1 цех',
            '2 цех',
            '3-Цех Буғҳавогазтаъминоти цехи',
            '200-Цех Бошқа',  # «200» не принимается за «20»
        ])

    def test_leadership_is_recognised_with_plain_h_and_without_it_the_order_is_unchanged(self):
        self.assertEqual(self.tree_names(), ['1 цех', '2 цех'])

        Department.objects.create(name='Рахбарият')

        self.assertEqual(self.tree_names(), ['Рахбарият', '1 цех', '2 цех'])

    def test_pinning_does_not_change_stored_sort_order(self):
        twentieth = Department.objects.create(name='20-Цех Завод бошқармаси')

        self.tree_names()

        twentieth.refresh_from_db()
        self.assertEqual(twentieth.sort_order, 20)


class StaffTreeTests(StaffTestCase):
    def test_tree_lists_departments_with_sections_and_totals(self):
        second_section = Section.objects.create(department=self.department, name='Техник бўлим', code='T-1')
        self.make_position('Смена бошлиғи', staff_count=2)
        self.make_position('Юрист', staff_count=3, section=self.section)
        self.make_position('Техник', staff_count=4, section=second_section)
        self.make_employee('Юрист', department=self.department, section=self.section)

        tree = self.client.get(TREE_URL).json()['data']

        self.assertEqual([node['name'] for node in tree], ['1 цех', '2 цех'])
        first, second = tree
        self.assertEqual(first['type'], 'department')
        self.assertEqual((first['staff_total'], first['vacant_total']), (9, 8))
        self.assertEqual(
            [(child['type'], child['name'], child['staff_total'], child['vacant_total']) for child in first['children']],
            [('section', 'Техник бўлим', 4, 4), ('section', 'Юридик бўлим', 3, 2)],
        )
        self.assertEqual(second['children'], [])
        self.assertEqual((second['staff_total'], second['vacant_total']), (0, 0))
