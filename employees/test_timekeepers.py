"""Табелчи ES га логин/парол билан кирмайди (2026-11-26): «Табелчи» роли, «Цех ходимлари» саҳифаси ва табелчи учун
очилган веб-API лар йўқ. Табелчи фақат бошқа тизимда (E-IMZO билан) ишлайди, у ES билан сервис калити орқали гаплашади
(test_timekeeper_api.py, test_timekeeper_assignments.py). Тайинлов ES ҳисобига боғлиқ эмас: ҳисоб бор-йўқлиги, унинг роли
ва ўчирилиши тайинловга таъсир қилмайди ва тайинлов ҳам ҳисобни ўзгартирмайди."""
import importlib
import json
import re

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import Client, TestCase

from .models import Department, Employee, ROLE_ADMIN, ROLE_CHOICES, ROLE_HR, ROLE_USER, Timekeeper, UserProfile
from .timekeepers import active_assignment, assign_timekeeper

PASSWORD = 'Zhr29Kqp71'


def make_user(username, role, *, employee=None):
    user = User.objects.create_user(username=username, password=PASSWORD)
    UserProfile.objects.create(user=user, role=role)
    if employee is not None:
        employee.user = user
        employee.login = username
        employee.save(update_fields=['user', 'login'])
    return user


def make_employee(tabel, department, *, first_name='Ишчи', last_name=None, **extra):
    return Employee.objects.create(
        first_name=first_name, last_name=last_name or f'Ходим{tabel}', tabel_number=str(tabel), department=department,
        position=extra.pop('position', 'Фаррош'), phone_number_1=extra.pop('phone_number_1', '+998901112233'), **extra,
    )


def client_for(user):
    client = Client(HTTP_HOST='localhost')
    client.force_login(user)
    return client


def patch_json(client, url, data):
    return client.patch(url, data=json.dumps(data), content_type='application/json')


def menu_links(html):
    """Ён менюдаги ҳаволалар (href)."""
    nav = html[html.index('<nav'):html.index('</nav>')]
    return re.findall(r'<a href="([^"]+)" class="', nav)


class TimekeeperTestBase(TestCase):
    def setUp(self):
        self.dept_a = Department.objects.create(name='1-Цех Биринчи')
        self.dept_b = Department.objects.create(name='2-Цех Иккинчи')
        self.admin = make_user('tk_admin', ROLE_ADMIN)
        self.admin_client = client_for(self.admin)
        self.hr = make_user('tk_hr', ROLE_HR)

        self.ali = make_employee(101, self.dept_a, first_name='Али', last_name='Валиев')
        self.bobur = make_employee(102, self.dept_a, first_name='Бобур', last_name='Тошев')
        self.sardor = make_employee(201, self.dept_b, first_name='Сардор', last_name='Каримов')
        make_user('ali_login', ROLE_USER, employee=self.ali)

    def assign(self, department, employee):
        return assign_timekeeper(department, employee)

    def make_timekeeper(self):
        """Али — 1-цех табелчиси; унинг ES ҳисоби (логин/парол) бор — у билан кирган клиент."""
        self.assign(self.dept_a, self.ali)
        return client_for(User.objects.get(username='ali_login'))


class NoTimekeeperRoleTests(TimekeeperTestBase):
    """«Табелчи» роли йўқ: уни бериб ҳам, танлаб ҳам бўлмайди."""

    def test_the_role_is_not_a_choice_any_more(self):
        self.assertEqual([value for value, _ in ROLE_CHOICES], [ROLE_ADMIN, ROLE_HR, ROLE_USER])

    def test_the_role_cannot_be_set_from_the_users_pages_or_the_employee_form(self):
        profile = User.objects.get(username='ali_login').profile

        response = patch_json(self.admin_client, f'/api/template/users/{profile.id}/', {'role': 'timekeeper'})

        self.assertEqual(response.status_code, 400)
        profile.refresh_from_db()
        self.assertEqual(profile.role, ROLE_USER)
        created = self.admin_client.post('/api/template/users/', data={'username': 'new_tk', 'password': PASSWORD, 'role': 'timekeeper'})
        self.assertEqual(created.status_code, 400)
        employee = self.admin_client.post('/api/template/employees/', data={
            'first_name': 'Янги', 'last_name': 'Ходим', 'tabel_number': '555', 'department_id': self.dept_a.id,
            'login': 'new.employee', 'password': PASSWORD, 'password_confirm': PASSWORD, 'role': 'timekeeper',
        })
        self.assertEqual(employee.status_code, 400)
        self.assertFalse(User.objects.filter(username__in=['new_tk', 'new.employee']).exists())

    def test_role_selects_and_labels_have_no_timekeeper(self):
        for page in ('/settings/users/', '/settings/access/'):
            with self.subTest(page=page):
                html = self.admin_client.get(page).content.decode()
                self.assertNotIn('value="timekeeper"', html)
                self.assertNotIn('Табелчи', html)
                self.assertNotIn("'timekeeper'", html)

    def test_migration_turns_former_timekeeper_accounts_into_plain_users(self):
        migration = importlib.import_module('employees.migrations.0042_remove_timekeeper_role')
        profile = User.objects.get(username='ali_login').profile
        UserProfile.objects.filter(pk=profile.pk).update(role='timekeeper')   # 0041 гача шундай сақланган эди
        self.assign(self.dept_a, self.ali)

        migration.timekeeper_accounts_to_plain_users(django_apps, None)

        profile.refresh_from_db()
        self.assertEqual(profile.role, ROLE_USER)
        self.assertEqual(Timekeeper.objects.get().employee, self.ali)   # тайинлов сақланади
        self.assertEqual(UserProfile.objects.get(user=self.admin).role, ROLE_ADMIN)   # бошқа роллар тегилмайди


class TimekeeperHasNoEsWebAccessTests(TimekeeperTestBase):
    """Тайинланган табелчи ES ҳисоби билан кирса — оддий фойдаланувчи: фақат ўз карточкаси."""

    def setUp(self):
        super().setUp()
        self.timekeeper_client = self.make_timekeeper()

    def test_the_department_employees_page_and_the_directory_api_are_gone(self):
        for client in (self.admin_client, client_for(self.hr), self.timekeeper_client):
            for url in ('/department-employees/', '/api/template/directory/', '/timekeepers/', '/api/template/timekeepers/',
                        f'/api/template/timekeepers/{self.dept_a.id}/'):
                with self.subTest(url=url):
                    self.assertEqual(client.get(url).status_code, 404)

    def test_a_timekeeper_lands_on_the_own_card_like_any_plain_user(self):
        own = f'/employees/{self.ali.slug}/'
        for page in ('/', '/employees/', f'/employees/{self.bobur.slug}/', f'/employees/{self.bobur.slug}/edit/'):
            with self.subTest(page=page):
                response = self.timekeeper_client.get(page, follow=True)
                self.assertEqual(response.redirect_chain[-1][0], own)
        html = self.timekeeper_client.get(own).content.decode()
        self.assertNotIn('Цех ходимлари', html)
        self.assertNotIn('/department-employees/', html)

    def test_the_web_api_gives_a_timekeeper_nothing_of_the_department(self):
        self.assertEqual(self.timekeeper_client.get('/api/template/employees/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get(f'/api/template/employees/{self.bobur.id}/').status_code, 403)
        self.assertEqual(patch_json(self.timekeeper_client, f'/api/template/employees/{self.bobur.id}/', {'position': 'Х'}).status_code, 403)
        self.assertEqual(self.timekeeper_client.get(f'/api/template/employees/{self.bobur.id}/replacement-candidates/').status_code, 403)
        self.assertEqual(self.timekeeper_client.post('/api/template/employees/', {'first_name': 'А', 'last_name': 'Б', 'tabel_number': '777'}).status_code, 403)
        self.bobur.refresh_from_db()
        self.assertEqual(self.bobur.position, 'Фаррош')

    def test_the_public_api_with_a_web_session_shows_only_himself(self):
        data = self.timekeeper_client.get('/api/v1/employees/').json()
        tabels = [item['tabel_number'] for item in (data['results'] if isinstance(data, dict) else data)]

        self.assertEqual(tabels, ['101'])
        self.assertEqual(patch_json(self.timekeeper_client, f'/api/employees/{self.bobur.slug}/', {'position': 'Х'}).status_code, 403)
        self.assertEqual(self.timekeeper_client.post('/api/employees/', {'first_name': 'А', 'last_name': 'Б', 'tabel_number': '777'}).status_code, 403)
        self.assertFalse(Employee.objects.filter(tabel_number='777').exists())

    def test_admin_keeps_the_full_list_and_menu(self):
        html = self.admin_client.get('/employees/').content.decode()

        for expected in ('id="openImportModalButton"', 'id="exportButton"', 'href="/employees/create/"', 'Амаллар</th>',
                         'id="treeFilterControl" class="relative"'):
            self.assertIn(expected, html)
        for gone in ('LIST_MODE', 'TIMEKEEPER_DEPARTMENT_ID', '/template/directory/', 'Цех ходимлари'):
            self.assertNotIn(gone, html)
        links = menu_links(html)
        self.assertIn('/', links)
        self.assertIn('/employees/', links)
        self.assertNotIn('/department-employees/', links)

    def test_admin_forms_have_no_timekeeper_leftovers(self):
        for page in ('/employees/create/', f'/employees/{self.bobur.slug}/', f'/employees/{self.bobur.slug}/edit/'):
            with self.subTest(page=page):
                html = self.admin_client.get(page).content.decode()
                self.assertNotIn('/department-employees/', html)
                self.assertNotIn('Ходим сизнинг цехингизга қўшилади', html)
        self.assertIn(f'href="/employees/{self.bobur.slug}/delete/"', self.admin_client.get(f'/employees/{self.bobur.slug}/').content.decode())


class AssignmentIsIndependentOfTheEsAccountTests(TimekeeperTestBase):
    """Тайинлов ES ҳисобига тегмайди ва ундан боғлиқ эмас."""

    def setUp(self):
        super().setUp()
        self.make_timekeeper()
        self.profile = User.objects.get(username='ali_login').profile

    def test_the_assignment_leaves_the_account_role_alone(self):
        self.assertEqual(self.profile.role, ROLE_USER)
        self.assertFalse(self.profile.requires_face_id_login)

    def test_changing_the_account_role_keeps_the_assignment(self):
        response = patch_json(self.admin_client, f'/api/template/users/{self.profile.id}/', {'role': ROLE_USER})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Timekeeper.objects.get().employee, self.ali)

    def test_deleting_the_account_keeps_the_assignment(self):
        response = self.admin_client.delete(f'/api/template/users/{self.profile.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='ali_login').exists())
        self.assertEqual(active_assignment('101').department, self.dept_a)   # бошқа тизим орқали ишлашда давом этади

    def test_deleting_the_employee_removes_the_assignment(self):
        response = self.admin_client.delete(f'/api/template/employees/{self.ali.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Timekeeper.objects.exists())

    def test_access_page_shows_no_role_for_a_timekeeper(self):
        data = self.admin_client.get('/api/template/user-access/?search=101').json()['data']

        self.assertIsNone(data[0]['role'])
