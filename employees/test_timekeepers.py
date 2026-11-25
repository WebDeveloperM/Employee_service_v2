"""«Табелчи» роли ва «Табелчилар» саҳифаси: цех учун битта табелчи, у фақат ўз цехи ходимлари рўйхатини кўради."""
import json
import re

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from .models import (
    Department, Employee, ROLE_ADMIN, ROLE_HR, ROLE_TIMEKEEPER, ROLE_USER, Timekeeper, UserProfile,
)
from .permissions import is_timekeeper, timekeeper_department

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


def put_json(client, url, data):
    return client.put(url, data=json.dumps(data), content_type='application/json')


def patch_json(client, url, data):
    return client.patch(url, data=json.dumps(data), content_type='application/json')


def department_option_ids(html):
    """Ходим формасидаги «Цех» рўйхатидаги цехлар id си (тоифа ва бошқа рўйхатлар қўшилмайди)."""
    return [int(value) for value in re.findall(r'<option value="(\d+)"\s+data-boss-full-name', html)]


def menu_links(html):
    """Ён менюдаги ҳаволалар: [(href, фаол_эканми)]."""
    nav = html[html.index('<nav'):html.index('</nav>')]
    return [(href, 'active' in classes.split()) for href, classes in re.findall(r'<a href="([^"]+)" class="([^"]*)"', nav)]


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

    def assign(self, department, employee, client=None, **extra):
        return put_json(client or self.admin_client, f'/api/template/timekeepers/{department.id}/', {'employee_id': employee.id, **extra})

    def make_timekeeper(self):
        """Али — 1-цех табелчиси (ҳисоби бор), у учун клиент."""
        self.assertEqual(self.assign(self.dept_a, self.ali).status_code, 200)
        return client_for(User.objects.get(username='ali_login'))


class AssignTimekeeperTests(TimekeeperTestBase):
    def test_assign_employee_with_account_sets_the_role(self):
        response = self.assign(self.dept_a, self.ali)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['timekeeper']['full_name'], str(self.ali))
        self.assertEqual(Timekeeper.objects.get(department=self.dept_a).employee, self.ali)
        self.assertEqual(User.objects.get(username='ali_login').profile.role, ROLE_TIMEKEEPER)

    def test_employee_without_account_needs_login_and_password_and_gets_an_account(self):
        refused = self.assign(self.dept_a, self.bobur)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['field'], 'login')
        self.assertFalse(Timekeeper.objects.exists())

        created = self.assign(self.dept_a, self.bobur, login='bobur.tabel', password=PASSWORD, password_confirm=PASSWORD)

        self.assertEqual(created.status_code, 200)
        self.bobur.refresh_from_db()
        self.assertEqual(self.bobur.user.username, 'bobur.tabel')
        self.assertEqual(self.bobur.login, 'bobur.tabel')
        self.assertEqual(self.bobur.user.profile.role, ROLE_TIMEKEEPER)
        self.assertTrue(Client(HTTP_HOST='localhost').login(username='bobur.tabel', password=PASSWORD))

    def test_new_account_rules_are_the_same_as_in_the_employee_form(self):
        cases = [
            ({'login': 'bad login', 'password': PASSWORD, 'password_confirm': PASSWORD}, 'login'),
            ({'login': 'ali_login', 'password': PASSWORD, 'password_confirm': PASSWORD}, 'login'),     # бошқа ходимнинг логини
            ({'login': 'bobur.tabel', 'password': '', 'password_confirm': ''}, 'password'),
            ({'login': 'bobur.tabel', 'password': PASSWORD, 'password_confirm': PASSWORD + '1'}, 'password_confirm'),
            ({'login': 'bobur.tabel', 'password': 'short1', 'password_confirm': 'short1'}, 'password'),
            ({'login': 'bobur.tabel', 'password': 'onlyletters', 'password_confirm': 'onlyletters'}, 'password'),
        ]
        for data, field in cases:
            with self.subTest(data=data):
                response = self.assign(self.dept_a, self.bobur, **data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['field'], field)
        self.assertFalse(Timekeeper.objects.exists())
        self.assertFalse(User.objects.filter(username='bobur.tabel').exists())

    def test_an_employee_of_another_department_can_be_assigned_and_works_for_the_chosen_one(self):
        response = self.assign(self.dept_a, self.sardor, login='sardor.t', password=PASSWORD, password_confirm=PASSWORD)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Timekeeper.objects.get().department, self.dept_a)
        self.sardor.refresh_from_db()
        self.assertEqual(self.sardor.department, self.dept_b)          # ходимнинг ўз цехи ўзгармайди
        client = Client(HTTP_HOST='localhost')
        self.assertTrue(client.login(username='sardor.t', password=PASSWORD))
        tabels = sorted(item['tabel_number'] for item in client.get('/api/template/employees/').json()['data'])
        self.assertEqual(tabels, ['101', '102'])                       # тайинланган цех ходимлари, ўз цехиники эмас

    def test_leadership_employees_cannot_be_timekeepers(self):
        leadership = Department.objects.create(name='Раҳбарият', sort_order=42)
        boss = make_employee(900, leadership, first_name='Раҳбар')
        make_user('boss_login', ROLE_USER, employee=boss)

        response = self.assign(self.dept_a, boss)

        self.assertEqual(response.status_code, 400)
        self.assertIn('Раҳбарият', response.json()['message'])
        self.assertFalse(Timekeeper.objects.exists())
        self.assertEqual(User.objects.get(username='boss_login').profile.role, ROLE_USER)

    def test_inactive_or_deleted_employee_cannot_be_assigned(self):
        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])
        self.assertEqual(self.assign(self.dept_a, self.ali).status_code, 400)

        self.ali.is_active = True
        self.ali.is_deleted = True
        self.ali.save(update_fields=['is_active', 'is_deleted'])
        self.assertEqual(self.assign(self.dept_a, self.ali).status_code, 404)
        self.assertFalse(Timekeeper.objects.exists())

    def test_admin_and_hr_accounts_cannot_become_timekeepers_and_keep_their_role(self):
        admin_employee = make_employee(103, self.dept_a, first_name='Админ')
        hr_employee = make_employee(104, self.dept_a, first_name='Ҳр')
        admin_user = make_user('emp_admin', ROLE_ADMIN, employee=admin_employee)
        hr_user = make_user('emp_hr', ROLE_HR, employee=hr_employee)
        superuser = User.objects.create_superuser(username='emp_super', password=PASSWORD)
        super_employee = make_employee(105, self.dept_a, first_name='Супер')
        super_employee.user = superuser
        super_employee.save(update_fields=['user'])

        for employee in (admin_employee, hr_employee, super_employee):
            with self.subTest(employee=employee.tabel_number):
                self.assertEqual(self.assign(self.dept_a, employee).status_code, 400)

        self.assertEqual(admin_user.profile.role, ROLE_ADMIN)
        admin_user.profile.refresh_from_db()
        hr_user.profile.refresh_from_db()
        self.assertEqual((admin_user.profile.role, hr_user.profile.role), (ROLE_ADMIN, ROLE_HR))
        self.assertFalse(Timekeeper.objects.exists())

    def test_employee_cannot_be_timekeeper_of_two_departments(self):
        self.assertEqual(self.assign(self.dept_a, self.ali).status_code, 200)

        response = self.assign(self.dept_b, self.ali)

        self.assertEqual(response.status_code, 400)
        self.assertIn('1-Цех Биринчи', response.json()['message'])
        self.assertEqual(Timekeeper.objects.count(), 1)

    def test_replacing_the_timekeeper_returns_the_old_one_to_a_plain_user(self):
        make_user('bobur_login', ROLE_USER, employee=self.bobur)
        self.assign(self.dept_a, self.ali)

        response = self.assign(self.dept_a, self.bobur)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Timekeeper.objects.get(department=self.dept_a).employee, self.bobur)
        self.assertEqual(User.objects.get(username='ali_login').profile.role, ROLE_USER)
        self.assertEqual(User.objects.get(username='bobur_login').profile.role, ROLE_TIMEKEEPER)

    def test_assigning_the_same_employee_again_is_harmless(self):
        self.assign(self.dept_a, self.ali)
        self.assertEqual(self.assign(self.dept_a, self.ali).status_code, 200)
        self.assertEqual(Timekeeper.objects.count(), 1)

    def test_remove_takes_the_role_back_but_keeps_the_account(self):
        self.assign(self.dept_a, self.ali)

        response = self.admin_client.delete(f'/api/template/timekeepers/{self.dept_a.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Timekeeper.objects.exists())
        self.assertEqual(User.objects.get(username='ali_login').profile.role, ROLE_USER)
        self.assertEqual(self.admin_client.delete(f'/api/template/timekeepers/{self.dept_a.id}/').status_code, 404)

    def test_missing_employee_id_and_unknown_department(self):
        self.assertEqual(put_json(self.admin_client, f'/api/template/timekeepers/{self.dept_a.id}/', {}).status_code, 400)
        self.assertEqual(put_json(self.admin_client, '/api/template/timekeepers/9999/', {'employee_id': self.ali.id}).status_code, 404)


class TimekeeperManagementAccessTests(TimekeeperTestBase):
    def test_hr_can_manage_timekeepers(self):
        hr_client = client_for(self.hr)

        self.assertEqual(hr_client.get('/timekeepers/').status_code, 200)
        self.assertEqual(self.assign(self.dept_a, self.ali, client=hr_client).status_code, 200)
        self.assertEqual(hr_client.delete(f'/api/template/timekeepers/{self.dept_a.id}/').status_code, 200)

    def test_plain_user_and_timekeeper_cannot(self):
        plain = client_for(make_user('plain_user', ROLE_USER, employee=self.bobur))
        timekeeper = self.make_timekeeper()

        for client in (plain, timekeeper):
            self.assertEqual(client.get('/api/template/timekeepers/').status_code, 403)
            self.assertEqual(client.get(f'/api/template/timekeepers/candidates/?department_id={self.dept_a.id}').status_code, 403)
            self.assertEqual(self.assign(self.dept_a, self.bobur, client=client).status_code, 403)
            self.assertEqual(client.delete(f'/api/template/timekeepers/{self.dept_a.id}/').status_code, 403)
            self.assertEqual(client.get('/timekeepers/').status_code, 302)
        self.assertEqual(Timekeeper.objects.get().employee, self.ali)

    def test_anonymous_is_refused(self):
        anonymous = Client(HTTP_HOST='localhost')
        self.assertIn(anonymous.get('/api/template/timekeepers/').status_code, (401, 403))
        self.assertEqual(anonymous.get('/timekeepers/').status_code, 302)

    def test_list_has_every_department_in_order_with_its_timekeeper(self):
        self.assign(self.dept_a, self.ali)

        data = self.admin_client.get('/api/template/timekeepers/').json()['data']

        self.assertEqual([row['department']['name'] for row in data], ['1-Цех Биринчи', '2-Цех Иккинчи'])
        self.assertEqual(data[0]['employee_count'], 2)
        self.assertEqual(data[0]['timekeeper']['tabel_number'], '101')
        self.assertEqual(data[0]['timekeeper']['login'], 'ali_login')
        self.assertTrue(data[0]['timekeeper']['has_account'])
        self.assertIsNone(data[1]['timekeeper'])

    def test_table_departments_follow_the_staff_tree_order(self):
        Department.objects.create(name='20-Цех Завод бошқармаси')
        Department.objects.create(name='Раҳбарият')

        names = [row['department']['name'] for row in self.admin_client.get('/api/template/timekeepers/').json()['data']]

        self.assertEqual(names, ['Раҳбарият', '20-Цех Завод бошқармаси', '1-Цех Биринчи', '2-Цех Иккинчи'])

    def test_candidates_list_every_employee_without_a_limit_in_a_few_queries(self):
        for number in range(300, 350):
            make_employee(number, self.dept_a)

        with CaptureQueriesContext(connection) as queries:
            payload = self.admin_client.get('/api/template/timekeepers/candidates/').json()

        self.assertEqual(len(payload['data']), 53)           # 3 та эски ходим + 50 та янги: чекловсиз
        self.assertLess(len(queries), 15)                    # ҳар бир ходим учун алоҳида сўров йўқ

    def test_assign_dialog_searches_in_the_page_and_selects_by_tabel_number(self):
        html = self.admin_client.get('/timekeepers/').content.decode()

        self.assertIn('placeholder="Табель рақами, исм ёки лавозим бўйича қидириш..."', html)
        self.assertIn('id="candidatesCount"', html)
        self.assertIn('function filteredCandidates()', html)
        self.assertIn('tabel === word', html)                # табели сўзга тўлиқ мос келганлар тепада
        self.assertIn("event.key === 'Enter'", html)         # Enter — биринчи топилганни танлайди
        self.assertIn("apiRequest('/template/timekeepers/candidates/')", html)   # барча цехлар, цех бўйича чекланмайди
        self.assertIn('Цех кўрсатилмаган', html)             # цехлар бўйича гуруҳ сарлавҳалари

    def test_candidates_are_every_department_with_the_20th_first_and_without_leadership(self):
        self.assign(self.dept_a, self.ali)
        plant = Department.objects.create(name='20-Цех Завод бошқармаси')
        leadership = Department.objects.create(name='Раҳбарият')
        make_employee(2001, plant, first_name='Заводчи')
        make_employee(900, leadership, first_name='Раҳбар')
        make_user('boss_admin', ROLE_ADMIN, employee=make_employee(103, self.dept_a, first_name='Бошлиқ'))
        make_employee(104, self.dept_a, first_name='Чиққан', is_active=False)
        url = '/api/template/timekeepers/candidates/'

        payload = self.admin_client.get(url).json()
        by_tabel = {item['tabel_number']: item for item in payload['data']}

        # Ходимлар рўйхатидаги тартиб: Раҳбарият (кўрсатилмайди) → 20-Цех → қолган цехлар; фаол эмас ходим йўқ.
        self.assertEqual([item['tabel_number'] for item in payload['data']], ['2001', '101', '102', '103', '201'])
        self.assertEqual(by_tabel['2001']['department_name'], '20-Цех Завод бошқармаси')
        self.assertEqual(by_tabel['201']['department_name'], '2-Цех Иккинчи')
        self.assertEqual(by_tabel['101']['timekeeper_of'], '1-Цех Биринчи')
        self.assertTrue(by_tabel['101']['has_account'])
        self.assertFalse(by_tabel['102']['has_account'])
        self.assertTrue(by_tabel['103']['is_privileged'])
        self.assertEqual([item['tabel_number'] for item in self.admin_client.get(url + '?search=Бобур').json()['data']], ['102'])
        self.assertEqual([item['tabel_number'] for item in self.admin_client.get(url + '?search=103').json()['data']], ['103'])
        self.assertEqual(self.admin_client.get(url + '?search=Раҳбар').json()['data'], [])


class TimekeeperAccessScopeTests(TimekeeperTestBase):
    """Табелчи: «Ходимлар рўйхати» (барча ходимлар, фақат ўқиш) ва «Цех ходимлари» (ўз цехи)."""

    def setUp(self):
        super().setUp()
        self.timekeeper_client = self.make_timekeeper()

    def test_role_helpers(self):
        user = User.objects.get(username='ali_login')

        self.assertTrue(is_timekeeper(user))
        self.assertEqual(timekeeper_department(user), self.dept_a)
        self.assertFalse(is_timekeeper(self.admin))
        self.assertIsNone(timekeeper_department(self.admin))
        self.assertIsNone(timekeeper_department(make_user('someone', ROLE_USER)))

    def test_menu_has_the_employee_list_and_then_the_department_employees(self):
        html = self.timekeeper_client.get('/employees/').content.decode()
        nav = html[html.index('<nav'):html.index('</nav>')]

        self.assertEqual(menu_links(html), [('/employees/', True), ('/department-employees/', False)])
        self.assertLess(nav.index('Ходимлар рўйхати'), nav.index('Цех ходимлари'))
        for hidden in ('href="/"', 'Созламалар', '/staff/', '/timekeepers/', '/departments/', '/sections/', '/settings/access/'):
            self.assertNotIn(hidden, nav)

    def test_menu_highlights_the_department_page_and_the_card_pages_under_it(self):
        for path in ('/department-employees/', f'/employees/{self.bobur.slug}/', f'/employees/{self.bobur.slug}/edit/', '/employees/create/'):
            with self.subTest(path=path):
                html = self.timekeeper_client.get(path).content.decode()
                self.assertEqual(menu_links(html), [('/employees/', False), ('/department-employees/', True)])

    def test_admin_menu_has_the_new_page_right_after_staff_and_the_home_page(self):
        html = self.admin_client.get('/').content.decode()
        sidebar = html[html.index('<nav'):html.index('</nav>')]

        self.assertIn('href="/"', sidebar)
        self.assertLess(sidebar.index('href="/staff/"'), sidebar.index('href="/timekeepers/"'))
        self.assertLess(sidebar.index('href="/timekeepers/"'), sidebar.index('href="/departments/"'))
        self.assertIn('<span>Табелчилар</span>', sidebar)
        self.assertNotIn('Цех ходимлари', sidebar)           # администратор ва HR менюсида йўқ
        self.assertEqual(client_for(self.hr).get('/').status_code, 200)
        self.assertIn('href="/timekeepers/"', client_for(self.hr).get('/').content.decode())

    def test_pages_a_timekeeper_may_not_use_lead_back_to_the_employee_list(self):
        for page in ('/', '/staff/', '/departments/', '/sections/', '/timekeepers/', '/settings/users/', '/settings/access/'):
            with self.subTest(page=page):
                response = self.timekeeper_client.get(page, follow=True)
                self.assertEqual(response.redirect_chain[-1][0], '/employees/')
                self.assertEqual(response.status_code, 200)

    def test_directory_page_is_read_only(self):
        html = self.timekeeper_client.get('/employees/').content.decode()

        self.assertIn("const LIST_MODE = 'directory';", html)
        self.assertIn('Ходимлар рўйхати</h2>', html)
        self.assertNotIn('Цех: 1-Цех', html)
        for forbidden in ('id="openImportModalButton"', 'id="exportButton"', 'href="/employees/create/"', 'Амаллар</th>'):
            self.assertNotIn(forbidden, html)

    def test_department_page_has_the_crud_actions_but_no_import_or_export(self):
        html = self.timekeeper_client.get('/department-employees/').content.decode()

        self.assertIn("const LIST_MODE = 'department';", html)
        self.assertIn(f'const TIMEKEEPER_DEPARTMENT_ID = {self.dept_a.id};', html)
        self.assertIn('Цех ходимлари</h2>', html)
        self.assertIn('Цех: 1-Цех Биринчи', html)
        self.assertIn('href="/employees/create/"', html)
        self.assertIn('Амаллар</th>', html)
        for forbidden in ('id="openImportModalButton"', 'id="exportButton"'):
            self.assertNotIn(forbidden, html)
        self.assertIn('id="treeFilterControl" class="relative hidden"', html)   # цех/бўлим бўйича қидириш чизилмайди
        self.assertIn('/edit/" title="Таҳрирлаш"', html)
        self.assertIn("${DEPARTMENT_LIST ? '' : `<a class=\"${ACTION_BUTTON} border-red-300", html)   # ўчириш тугмаси «Цех ходимлари»да чизилмайди

    def test_admin_and_hr_do_not_use_the_department_page_and_keep_the_full_list(self):
        self.assertEqual(self.admin_client.get('/department-employees/').status_code, 302)
        self.assertEqual(client_for(self.hr).get('/department-employees/').status_code, 302)
        html = self.admin_client.get('/employees/').content.decode()

        self.assertIn("const LIST_MODE = 'full';", html)
        self.assertIn('id="treeFilterControl" class="relative"', html)
        self.assertIn('const TIMEKEEPER_DEPARTMENT_ID = null;', html)
        for expected in ('id="openImportModalButton"', 'id="exportButton"', 'href="/employees/create/"', 'Амаллар</th>'):
            self.assertIn(expected, html)

    def test_directory_api_lists_every_department_read_only_with_list_fields_only(self):
        data = self.timekeeper_client.get('/api/template/directory/').json()['data']

        self.assertEqual(sorted(item['tabel_number'] for item in data), ['101', '102', '201'])   # барча цехлар
        for item in data:
            for private in ('phone_number_1', 'phone_number_2', 'login', 'date_of_birth', 'shoe_size', 'metadata', 'password'):
                self.assertNotIn(private, item)
            for needed in ('id', 'slug', 'first_name', 'last_name', 'tabel_number', 'department', 'section', 'position'):
                self.assertIn(needed, item)
        for write in (self.timekeeper_client.post, self.timekeeper_client.delete):
            self.assertIn(write('/api/template/directory/').status_code, (403, 405))

    def test_directory_api_does_not_let_a_timekeeper_search_by_phone_or_hiring_date(self):
        everyone = self.timekeeper_client.get('/api/template/directory/').json()['count']

        self.assertEqual(self.timekeeper_client.get('/api/template/directory/?phone=000000').json()['count'], everyone)
        self.assertEqual(self.timekeeper_client.get('/api/template/directory/?employment_date=1999-01-01').json()['count'], everyone)
        self.assertEqual(
            [item['tabel_number'] for item in self.timekeeper_client.get('/api/template/directory/?search=Сардор').json()['data']], ['201'],
        )

    def test_department_api_lists_only_own_department_with_the_protection_flag(self):
        boss = make_employee(103, self.dept_a, first_name='Бошлиқ')
        make_user('boss_admin', ROLE_ADMIN, employee=boss)

        data = self.timekeeper_client.get('/api/template/employees/').json()['data']
        by_tabel = {item['tabel_number']: item for item in data}

        self.assertEqual(sorted(by_tabel), ['101', '102', '103'])
        self.assertIn('phone_number_1', by_tabel['101'])         # ўз цехи ходимларини тўлиқ бошқаради
        self.assertEqual({tabel: item['is_protected'] for tabel, item in by_tabel.items()}, {'101': False, '102': False, '103': True})

    def test_department_api_cannot_be_widened_by_filters(self):
        base = '/api/template/employees/'
        for query in (f'?department_id={self.dept_b.id}', '?search=Сардор', '?tabel_number=201', '?status=', '?page_size=100'):
            with self.subTest(query=query):
                tabels = [item['tabel_number'] for item in self.timekeeper_client.get(base + query).json()['data']]
                self.assertNotIn('201', tabels)
        self.assertEqual(
            [item['tabel_number'] for item in self.timekeeper_client.get(base + '?search=Бобур').json()['data']], ['102'],
        )
        self.assertEqual(self.timekeeper_client.get(base).json()['count'], 2)

    def test_timekeeper_still_has_no_access_to_the_rest_of_the_admin_api(self):
        self.assertEqual(self.timekeeper_client.get('/api/template/staff/tree/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/user-access/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/users/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/timekeepers/').status_code, 403)
        self.assertEqual(self.timekeeper_client.post('/api/template/departments/', data={'name': 'Янги'}).status_code, 403)
        self.assertEqual(self.timekeeper_client.post('/api/template/sections/', data={'name': 'Янги', 'department_id': self.dept_a.id}).status_code, 403)
        self.assertEqual(self.timekeeper_client.post('/api/template/employees/import-excel/').status_code, 403)

    def test_public_api_shows_a_timekeeper_nothing_but_himself(self):
        data = self.timekeeper_client.get('/api/v1/employees/').json()
        tabels = [item['tabel_number'] for item in (data['results'] if isinstance(data, dict) else data)]

        self.assertEqual(tabels, ['101'])

    def test_timekeeper_without_a_working_assignment_sees_nothing(self):
        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])

        for page in ('/employees/', '/department-employees/'):
            self.assertTemplateUsed(self.timekeeper_client.get(page), 'timekeeper_unassigned.html')
        for api in ('/api/template/employees/', '/api/template/directory/'):
            self.assertEqual(self.timekeeper_client.get(api).status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/employees/create/', follow=True).redirect_chain[-1][0], '/employees/')

    def test_role_without_a_timekeeper_row_sees_nothing(self):
        Timekeeper.objects.all().delete()   # рол қолган, цех тайинлови йўқ (масалан, Django админкасидан берилган)

        self.assertEqual(self.timekeeper_client.get('/api/template/employees/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/directory/').status_code, 403)
        self.assertTemplateUsed(self.timekeeper_client.get('/employees/'), 'timekeeper_unassigned.html')

    def test_plain_user_still_goes_to_the_own_card(self):
        plain = client_for(make_user('plain2', ROLE_USER, employee=self.bobur))

        response = plain.get('/employees/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], f'/employees/{self.bobur.slug}/')
        self.assertEqual(plain.get('/api/template/directory/').status_code, 403)
        self.assertEqual(plain.get('/department-employees/').status_code, 302)


class TimekeeperManagedEmployeesTests(TimekeeperTestBase):
    """«Цех ходимлари»: табелчи ўз цехи ходимларини қўшади, кўради ва ўзгартиради — чекловлар билан; ўчира олмайди."""

    def setUp(self):
        super().setUp()
        self.timekeeper_client = self.make_timekeeper()
        make_user('bobur_login', ROLE_USER, employee=self.bobur)
        self.boss = make_employee(103, self.dept_a, first_name='Бошлиқ')
        self.boss_user = make_user('boss_admin', ROLE_ADMIN, employee=self.boss)

    def create(self, **extra):
        data = {'first_name': 'Янги', 'last_name': 'Ходим', 'tabel_number': '555', **extra}
        return self.timekeeper_client.post('/api/employees/', data)

    # --- қўшиш ---
    def test_a_new_employee_lands_in_the_timekeepers_department(self):
        response = self.create()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Employee.objects.get(tabel_number='555').department, self.dept_a)

    def test_a_new_employee_cannot_be_put_into_another_department_or_create_a_department(self):
        self.assertEqual(self.create(department_id=self.dept_b.id).status_code, 400)
        self.assertFalse(Employee.objects.filter(tabel_number='555').exists())

        response = self.create(department_name='Тўқима цех', boss_full_name='Ким Бошлиқ', tabel_number='556')

        self.assertEqual(response.status_code, 201)
        self.assertFalse(Department.objects.filter(name='Тўқима цех').exists())
        self.assertEqual(Employee.objects.get(tabel_number='556').department, self.dept_a)

    def test_a_new_employee_gets_a_plain_account_whatever_role_is_sent(self):
        response = self.create(login='new.emp', password=PASSWORD, password_confirm=PASSWORD, role=ROLE_ADMIN)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(username='new.emp').profile.role, ROLE_USER)

    def test_other_roles_cannot_create_through_the_public_api(self):
        plain = client_for(make_user('plain_x', ROLE_USER, employee=self.sardor))
        self.assertEqual(plain.post('/api/employees/', {'first_name': 'А', 'last_name': 'Б', 'tabel_number': '777'}).status_code, 403)

    # --- ўзгартириш ---
    def test_update_changes_the_card_but_not_the_account_the_role_or_the_department(self):
        url = f'/api/employees/{self.bobur.slug}/'

        response = patch_json(self.timekeeper_client, url, {
            'position': 'Электрик', 'phone_number_1': '+998 90 111 22 33', 'login': 'hacked', 'password': 'Hacked123x',
            'password_confirm': 'Hacked123x', 'role': ROLE_ADMIN,
        })

        self.assertEqual(response.status_code, 200)
        self.bobur.refresh_from_db()
        self.assertEqual(self.bobur.position, 'Электрик')
        user = User.objects.get(pk=self.bobur.user_id)
        self.assertEqual(user.username, 'bobur_login')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(user.profile.role, ROLE_USER)
        self.assertEqual(self.bobur.login, 'bobur_login')

    def test_update_cannot_move_an_employee_to_another_department(self):
        response = patch_json(self.timekeeper_client, f'/api/employees/{self.bobur.slug}/', {'department_id': self.dept_b.id})

        self.assertEqual(response.status_code, 400)
        self.bobur.refresh_from_db()
        self.assertEqual(self.bobur.department, self.dept_a)

    def test_update_of_another_department_or_of_a_protected_account_is_refused(self):
        self.assertEqual(patch_json(self.timekeeper_client, f'/api/employees/{self.sardor.slug}/', {'position': 'Х'}).status_code, 403)
        self.assertEqual(patch_json(self.timekeeper_client, f'/api/employees/{self.boss.slug}/', {'position': 'Х'}).status_code, 403)
        self.assertEqual(patch_json(self.timekeeper_client, f'/api/template/employees/{self.boss.id}/', {'position': 'Х'}).status_code, 403)
        self.assertEqual(patch_json(self.timekeeper_client, f'/api/template/employees/{self.sardor.id}/', {'position': 'Х'}).status_code, 404)
        self.sardor.refresh_from_db()
        self.boss.refresh_from_db()
        self.assertEqual((self.sardor.position, self.boss.position), ('Фаррош', 'Фаррош'))

    def test_template_detail_api_is_scoped_to_the_department(self):
        self.assertEqual(self.timekeeper_client.get(f'/api/template/employees/{self.bobur.id}/').status_code, 200)
        self.assertEqual(self.timekeeper_client.get(f'/api/template/employees/{self.sardor.id}/').status_code, 404)

    # --- ўчириш: табелчига берилмайди ---
    def test_a_timekeeper_cannot_delete_anyone(self):
        everyone = [self.bobur, self.sardor, self.boss, self.ali]   # ўз цехи ходими, бошқа цех, администратор ҳисоби ва ўзи

        for employee in everyone:
            with self.subTest(employee=employee.tabel_number):
                response = self.timekeeper_client.delete(f'/api/template/employees/{employee.id}/')
                self.assertEqual(response.status_code, 403, response.content)

        self.assertEqual(Employee.objects.filter(pk__in=[e.pk for e in everyone]).count(), 4)
        self.assertTrue(User.objects.filter(username__in=['bobur_login', 'boss_admin', 'ali_login']).count() == 3)

    def test_the_employee_list_api_has_no_delete_for_a_timekeeper(self):
        for url in ('/api/template/employees/', '/api/template/directory/', f'/api/employees/{self.bobur.slug}/'):
            with self.subTest(url=url):
                self.assertIn(self.timekeeper_client.delete(url).status_code, (403, 405))
        self.assertTrue(Employee.objects.filter(pk=self.bobur.pk).exists())

    def test_admin_still_deletes_an_employee_of_the_department(self):
        response = self.admin_client.delete(f'/api/template/employees/{self.bobur.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Employee.objects.filter(pk=self.bobur.pk).exists())
        self.assertFalse(User.objects.filter(username='bobur_login').exists())

    # --- саҳифалар ---
    def test_create_page_offers_only_the_own_department_and_no_dictionary_buttons(self):
        response = self.timekeeper_client.get('/employees/create/')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(department_option_ids(html), [self.dept_a.id])
        self.assertIn('Ходим сизнинг цехингизга қўшилади', html)
        photo_start = html.index('id="base_image"')
        self.assertNotIn('required', html[html.rfind('<input', 0, photo_start): html.index('>', photo_start) + 1])   # фотосурат ихтиёрий
        for hidden in ('id="openDepartmentInlineModalButton"', 'id="openSectionInlineModalButton"', 'id="openPositionCategoryModalButton"',
                       'id="openWorkScheduleModalButton"', 'id="openEmployeeCategoryModalButton"'):
            self.assertNotIn(hidden, html)
        self.assertIn('href="/department-employees/"', html)           # орқага — «Цех ходимлари»га

    def test_admin_create_page_keeps_every_department_and_the_plus_buttons(self):
        html = self.admin_client.get('/employees/create/').content.decode()

        self.assertEqual(department_option_ids(html), [self.dept_a.id, self.dept_b.id])
        self.assertIn('id="openDepartmentInlineModalButton"', html)
        self.assertNotIn('Ходим сизнинг цехингизга қўшилади', html)

    def test_card_pages_of_the_own_department_open_without_account_controls(self):
        for suffix in ('', 'edit/'):
            with self.subTest(page=suffix):
                self.assertEqual(self.timekeeper_client.get(f'/employees/{self.bobur.slug}/{suffix}').status_code, 200)

        html = self.timekeeper_client.get(f'/employees/{self.bobur.slug}/edit/').content.decode()
        self.assertNotIn('Ҳисоб маълумотлари', html)
        self.assertIn('<input type="hidden" id="login"', html)
        self.assertNotIn('id="openDepartmentInlineModalButton"', html)
        self.assertEqual(department_option_ids(html), [self.dept_a.id])
        self.assertIn('href="/department-employees/"', html)
        self.assertIn('Ҳисоб маълумотлари', self.admin_client.get(f'/employees/{self.bobur.slug}/edit/').content.decode())

    def test_card_pages_of_other_departments_and_protected_accounts_lead_back_to_the_department_page(self):
        for owner in (self.sardor, self.boss):
            for suffix in ('', 'edit/', 'delete/'):
                with self.subTest(employee=owner.tabel_number, page=suffix):
                    response = self.timekeeper_client.get(f'/employees/{owner.slug}/{suffix}', follow=True)
                    self.assertEqual(response.redirect_chain[-1][0], '/department-employees/')

    def test_delete_page_is_closed_to_a_timekeeper_even_for_the_own_department(self):
        response = self.timekeeper_client.get(f'/employees/{self.bobur.slug}/delete/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], '/department-employees/')
        self.assertNotIn('Ходимни ўчириш', response.content.decode())
        self.assertEqual(self.admin_client.get(f'/employees/{self.bobur.slug}/delete/').status_code, 200)

    def test_card_page_has_no_delete_link_for_a_timekeeper_but_admin_keeps_it(self):
        link = f'href="/employees/{self.bobur.slug}/delete/"'

        self.assertNotIn(link, self.timekeeper_client.get(f'/employees/{self.bobur.slug}/').content.decode())
        self.assertIn(link, self.admin_client.get(f'/employees/{self.bobur.slug}/').content.decode())


class TimekeeperRoleConsistencyTests(TimekeeperTestBase):
    def setUp(self):
        super().setUp()
        self.make_timekeeper()
        self.profile = User.objects.get(username='ali_login').profile

    def patch_role(self, role, profile=None):
        profile = profile or self.profile
        return self.admin_client.patch(f'/api/template/users/{profile.id}/', data=json.dumps({'role': role}), content_type='application/json')

    def test_taking_the_role_away_removes_the_assignment(self):
        response = self.patch_role(ROLE_USER)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Timekeeper.objects.exists())

    def test_saving_the_account_with_the_same_role_keeps_the_assignment(self):
        response = self.patch_role(ROLE_TIMEKEEPER)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Timekeeper.objects.count(), 1)

    def test_the_role_cannot_be_given_outside_the_timekeepers_page(self):
        bobur_user = make_user('bobur_login', ROLE_USER, employee=self.bobur)

        response = self.patch_role(ROLE_TIMEKEEPER, bobur_user.profile)

        self.assertEqual(response.status_code, 400)
        bobur_user.profile.refresh_from_db()
        self.assertEqual(bobur_user.profile.role, ROLE_USER)
        created = self.admin_client.post('/api/template/users/', data={'username': 'new_tk', 'password': PASSWORD, 'role': ROLE_TIMEKEEPER})
        self.assertEqual(created.status_code, 400)

    def test_employee_api_cannot_create_an_account_with_the_role(self):
        response = self.admin_client.post('/api/template/employees/', data={
            'first_name': 'Янги', 'last_name': 'Ходим', 'tabel_number': '555', 'department_id': self.dept_a.id,
            'login': 'new.employee', 'password': PASSWORD, 'password_confirm': PASSWORD, 'role': ROLE_TIMEKEEPER,
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username='new.employee').exists())

    def test_deleting_the_account_removes_the_assignment(self):
        response = self.admin_client.delete(f'/api/template/users/{self.profile.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Timekeeper.objects.exists())

    def test_deleting_the_employee_removes_the_assignment(self):
        response = self.admin_client.delete(f'/api/template/employees/{self.ali.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Timekeeper.objects.exists())

    def test_access_page_shows_the_timekeeper_role(self):
        data = self.admin_client.get('/api/template/user-access/?search=101').json()['data']

        self.assertEqual(data[0]['role'], ROLE_TIMEKEEPER)

    def test_role_is_listed_with_its_label(self):
        data = self.admin_client.get(f'/api/template/users/{self.profile.id}/').json()['data']

        self.assertEqual((data['role'], data['role_display']), (ROLE_TIMEKEEPER, 'Табелчи'))

    def test_timekeeper_does_not_need_face_id(self):
        self.assertFalse(self.profile.requires_face_id_login)
