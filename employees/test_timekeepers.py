"""«Табелчи» роли ва «Табелчилар» саҳифаси: цех учун битта табелчи, у фақат ўз цехи ходимлари рўйхатини кўради."""
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

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

    def test_employee_must_belong_to_the_department(self):
        response = self.assign(self.dept_a, self.sardor, login='sardor.t', password=PASSWORD, password_confirm=PASSWORD)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Timekeeper.objects.exists())
        self.assertFalse(User.objects.filter(username='sardor.t').exists())

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
        self.ali.department = self.dept_a
        self.assertEqual(self.assign(self.dept_a, self.ali).status_code, 200)
        self.ali.department = self.dept_b
        self.ali.save(update_fields=['department'])

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

    def test_candidates_are_the_departments_active_employees_with_their_state(self):
        self.assign(self.dept_a, self.ali)
        make_user('boss_admin', ROLE_ADMIN, employee=make_employee(103, self.dept_a, first_name='Бошлиқ'))
        make_employee(104, self.dept_a, first_name='Чиққан', is_active=False)
        url = f'/api/template/timekeepers/candidates/?department_id={self.dept_a.id}'

        payload = self.admin_client.get(url).json()
        by_tabel = {item['tabel_number']: item for item in payload['data']}

        self.assertEqual(sorted(by_tabel), ['101', '102', '103'])    # фаол эмас ва бошқа цех ходими йўқ
        self.assertEqual(by_tabel['101']['timekeeper_of'], '1-Цех Биринчи')
        self.assertTrue(by_tabel['101']['has_account'])
        self.assertFalse(by_tabel['102']['has_account'])
        self.assertTrue(by_tabel['103']['is_privileged'])
        self.assertFalse(payload['truncated'])
        self.assertEqual([item['tabel_number'] for item in self.admin_client.get(url + '&search=Бобур').json()['data']], ['102'])
        self.assertEqual([item['tabel_number'] for item in self.admin_client.get(url + '&search=103').json()['data']], ['103'])
        self.assertEqual(self.admin_client.get('/api/template/timekeepers/candidates/').status_code, 400)


class TimekeeperAccessScopeTests(TimekeeperTestBase):
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

    def test_menu_shows_only_the_employee_list(self):
        html = self.timekeeper_client.get('/employees/').content.decode()
        sidebar = html[html.index('<nav'):html.index('</nav>')]

        self.assertIn('href="/employees/"', sidebar)
        self.assertEqual(sidebar.count('<a href='), 1)
        for hidden in ('href="/"', 'Созламалар', '/staff/', '/timekeepers/', '/departments/', '/sections/', '/settings/access/'):
            self.assertNotIn(hidden, sidebar)

    def test_admin_menu_has_the_new_page_right_after_staff_and_the_home_page(self):
        html = self.admin_client.get('/').content.decode()
        sidebar = html[html.index('<nav'):html.index('</nav>')]

        self.assertIn('href="/"', sidebar)
        self.assertLess(sidebar.index('href="/staff/"'), sidebar.index('href="/timekeepers/"'))
        self.assertLess(sidebar.index('href="/timekeepers/"'), sidebar.index('href="/departments/"'))
        self.assertIn('<span>Табелчилар</span>', sidebar)
        self.assertEqual(client_for(self.hr).get('/').status_code, 200)
        self.assertIn('href="/timekeepers/"', client_for(self.hr).get('/').content.decode())

    def test_other_pages_send_the_timekeeper_to_the_employee_list(self):
        pages = ['/', '/staff/', '/departments/', '/sections/', '/timekeepers/', '/settings/users/', '/settings/access/',
                 '/employees/create/', f'/employees/{self.ali.slug}/', f'/employees/{self.ali.slug}/edit/',
                 f'/employees/{self.sardor.slug}/', f'/employees/{self.sardor.slug}/delete/']
        for page in pages:
            with self.subTest(page=page):
                response = self.timekeeper_client.get(page, follow=True)
                self.assertEqual(response.redirect_chain[-1][0], '/employees/')
                self.assertEqual(response.status_code, 200)

    def test_list_page_is_read_only_and_shows_the_department(self):
        html = self.timekeeper_client.get('/employees/').content.decode()

        self.assertIn('const IS_TIMEKEEPER = true;', html)
        self.assertIn(f'const TIMEKEEPER_DEPARTMENT_ID = {self.dept_a.id};', html)
        self.assertIn('Цех: 1-Цех Биринчи', html)
        for forbidden in ('id="openImportModalButton"', 'id="exportButton"', 'href="/employees/create/"', 'Амаллар</th>'):
            self.assertNotIn(forbidden, html)

    def test_admin_list_page_keeps_all_its_buttons(self):
        html = self.admin_client.get('/employees/').content.decode()

        self.assertIn('const IS_TIMEKEEPER = false;', html)
        self.assertIn('const TIMEKEEPER_DEPARTMENT_ID = null;', html)
        for expected in ('id="openImportModalButton"', 'id="exportButton"', 'href="/employees/create/"', 'Амаллар</th>'):
            self.assertIn(expected, html)

    def test_list_api_returns_only_own_department_and_only_list_fields(self):
        data = self.timekeeper_client.get('/api/template/employees/').json()['data']

        self.assertEqual(sorted(item['tabel_number'] for item in data), ['101', '102'])
        for item in data:
            for private in ('phone_number_1', 'phone_number_2', 'login', 'date_of_birth', 'shoe_size', 'metadata', 'password'):
                self.assertNotIn(private, item)
            for needed in ('id', 'slug', 'first_name', 'last_name', 'tabel_number', 'department', 'section', 'position'):
                self.assertIn(needed, item)

    def test_list_api_cannot_be_widened_by_filters(self):
        base = '/api/template/employees/'
        for query in (f'?department_id={self.dept_b.id}', '?search=Сардор', '?tabel_number=201', '?status=', '?page_size=100'):
            with self.subTest(query=query):
                tabels = [item['tabel_number'] for item in self.timekeeper_client.get(base + query).json()['data']]
                self.assertNotIn('201', tabels)
        self.assertEqual(
            [item['tabel_number'] for item in self.timekeeper_client.get(base + '?search=Бобур').json()['data']], ['102'],
        )
        self.assertEqual(self.timekeeper_client.get(base).json()['count'], 2)

    def test_timekeeper_cannot_write_or_open_a_card_through_the_api(self):
        self.assertEqual(self.timekeeper_client.post('/api/template/employees/', data={'first_name': 'Х'}).status_code, 403)
        self.assertEqual(self.timekeeper_client.get(f'/api/template/employees/{self.bobur.id}/').status_code, 403)
        self.assertEqual(
            self.timekeeper_client.patch(f'/api/template/employees/{self.bobur.id}/', data=json.dumps({'position': 'Х'}), content_type='application/json').status_code,
            403,
        )
        self.assertEqual(self.timekeeper_client.delete(f'/api/template/employees/{self.bobur.id}/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/staff/tree/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/user-access/').status_code, 403)
        self.assertEqual(self.timekeeper_client.get('/api/template/users/').status_code, 403)
        self.bobur.refresh_from_db()
        self.assertEqual(self.bobur.position, 'Фаррош')

    def test_public_api_shows_a_timekeeper_nothing_but_himself(self):
        data = self.timekeeper_client.get('/api/v1/employees/').json()
        tabels = [item['tabel_number'] for item in (data['results'] if isinstance(data, dict) else data)]

        self.assertEqual(tabels, ['101'])

    def test_timekeeper_without_a_working_assignment_sees_nothing(self):
        self.ali.is_active = False
        self.ali.save(update_fields=['is_active'])

        page = self.timekeeper_client.get('/employees/')

        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'timekeeper_unassigned.html')
        self.assertEqual(self.timekeeper_client.get('/api/template/employees/').status_code, 403)

    def test_role_without_a_timekeeper_row_sees_nothing(self):
        Timekeeper.objects.all().delete()   # рол қолган, цех тайинлови йўқ (масалан, Django админкасидан берилган)

        self.assertEqual(self.timekeeper_client.get('/api/template/employees/').status_code, 403)
        self.assertTemplateUsed(self.timekeeper_client.get('/employees/'), 'timekeeper_unassigned.html')

    def test_plain_user_still_goes_to_the_own_card(self):
        plain = client_for(make_user('plain2', ROLE_USER, employee=self.bobur))

        response = plain.get('/employees/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], f'/employees/{self.bobur.slug}/')


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
