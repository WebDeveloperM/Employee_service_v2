from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, StaffPosition, UserProfile


EMPLOYEES_URL = '/api/template/employees/'


class EmployeeSearchByWordsTests(TestCase):
    """Поиск в таблице сотрудников: каждое слово должно встретиться в ФИО, табеле или должности."""

    def setUp(self):
        admin = User.objects.create_user(username='search_words_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)
        self.department = Department.objects.create(name='Раҳбарият', sort_order=1)
        self.director = Employee.objects.create(
            first_name='Акбар', last_name='Фазилов', surname='Алишерович', tabel_number='1777',
            department=self.department, position='Завод директори',
        )
        self.namesake = Employee.objects.create(
            first_name='Акбар', last_name='Каримов', surname='Олимович', tabel_number='1778',
            department=self.department, position='Бош муҳандис',
        )
        self.latin = Employee.objects.create(
            first_name='Dilshod', last_name='Karimov', surname='Bahodirovich', tabel_number='2001',
            department=self.department, position='Slesar',
        )

    def found(self, search):
        response = self.client.get(EMPLOYEES_URL, {'search': search, 'page_size': 100})
        self.assertEqual(response.status_code, 200, response.content)
        return {item['tabel_number'] for item in response.json()['data']}

    def test_surname_and_first_name_in_either_order_find_the_employee(self):
        self.assertEqual(self.found('Фазилов Акбар'), {'1777'})
        self.assertEqual(self.found('Акбар Фазилов'), {'1777'})

    def test_the_full_name_as_shown_in_the_table_finds_the_employee(self):
        self.assertEqual(self.found('Фазилов Акбар Алишерович'), {'1777'})

    def test_every_word_must_match(self):
        self.assertEqual(self.found('Фазилов Олимович'), set())   # фамилия бир ходимники, отасининг исми бошқасиники
        self.assertEqual(self.found('Акбар'), {'1777', '1778'})

    def test_words_may_come_from_different_fields(self):
        self.assertEqual(self.found('Каримов муҳандис'), {'1778'})   # фамилия + лавозим
        self.assertEqual(self.found('1777 Акбар'), {'1777'})          # табель + исм

    def test_a_position_phrase_still_works_and_extra_spaces_are_ignored(self):
        self.assertEqual(self.found('Завод директори'), {'1777'})
        self.assertEqual(self.found('  Завод    директори  '), {'1777'})

    def test_search_is_case_insensitive(self):
        self.assertEqual(self.found('karimov DILSHOD'), {'2001'})

    def test_empty_or_blank_search_returns_everyone(self):
        self.assertEqual(self.found(''), {'1777', '1778', '2001'})
        self.assertEqual(self.found('   '), {'1777', '1778', '2001'})

    def test_results_keep_the_staff_tree_order(self):
        by_name = self.client.get(EMPLOYEES_URL, {'search': 'Акбар', 'page_size': 100})
        # штатда «Завод директори» биринчи турса, у фамилия тартибидан (Каримов, Фазилов) олдин келади
        StaffPosition.objects.create(department=self.department, name='Завод директори')
        StaffPosition.objects.create(department=self.department, name='Бош муҳандис')
        by_staff = self.client.get(EMPLOYEES_URL, {'search': 'Акбар', 'page_size': 100})

        self.assertEqual([item['tabel_number'] for item in by_name.json()['data']], ['1778', '1777'])
        self.assertEqual([item['tabel_number'] for item in by_staff.json()['data']], ['1777', '1778'])
