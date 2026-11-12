from django.contrib.auth.models import User
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Department, Employee, ROLE_ADMIN, Section, UserProfile


CREATE_URL = '/api/employees/'   # на него отправляет форма «Ходим қўшиш»


class EmployeeSectionIsOptionalTests(TestCase):
    """«Бўлим» при добавлении сотрудника необязателен: можно указать только цех или ничего."""

    def setUp(self):
        self.admin = User.objects.create_user(username='optional_section_admin', password='secret123')
        UserProfile.objects.create(user=self.admin, role=ROLE_ADMIN)
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        self.department = Department.objects.create(name='Раҳбарият', sort_order=1)

    def payload(self, tabel, **extra):
        return {'first_name': 'Ali', 'last_name': 'Valiyev', 'tabel_number': tabel, 'position': 'Директор', **extra}

    def test_employee_can_be_created_with_a_department_and_no_section(self):
        response = self.api.post(CREATE_URL, self.payload('OPT-1', department_id=self.department.pk), format='multipart')

        self.assertEqual(response.status_code, 201, response.content)
        employee = Employee.objects.get(tabel_number='OPT-1')
        self.assertEqual(employee.department_id, self.department.pk)
        self.assertIsNone(employee.section)

    def test_an_empty_section_field_is_treated_as_no_section(self):
        response = self.api.post(CREATE_URL, self.payload('OPT-2', department_id=self.department.pk, section_id=''), format='multipart')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(Employee.objects.get(tabel_number='OPT-2').section)

    def test_a_chosen_section_is_still_saved(self):
        section = Section.objects.create(department=self.department, name='Бош технолог бўлими', code='1')

        response = self.api.post(CREATE_URL, self.payload('OPT-3', department_id=self.department.pk, section_id=section.pk), format='multipart')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Employee.objects.get(tabel_number='OPT-3').section_id, section.pk)

    def test_a_section_of_another_department_is_still_rejected(self):
        other = Department.objects.create(name='20-Цех', sort_order=20)
        foreign = Section.objects.create(department=other, name='Бегона бўлим', code='1')

        response = self.api.post(CREATE_URL, self.payload('OPT-4', department_id=self.department.pk, section_id=foreign.pk), format='multipart')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Employee.objects.filter(tabel_number='OPT-4').exists())

    def test_the_section_can_be_removed_from_an_existing_employee(self):
        section = Section.objects.create(department=self.department, name='Бўлим', code='1')
        employee = Employee.objects.create(first_name='A', last_name='B', tabel_number='OPT-5', department=self.department, section=section)

        response = self.api.patch(f'/api/employees/{employee.slug}/', {'section_id': ''}, format='multipart')

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertIsNone(employee.section)

    def test_create_page_does_not_mark_the_section_as_required(self):
        Section.objects.create(department=self.department, name='Бош технолог бўлими', code='1')
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/employees/create/').content.decode()

        select_start = html.index('id="section_id"')
        select_tag = html[html.rfind('<select', 0, select_start): html.index('>', select_start) + 1]
        self.assertNotIn('required', select_tag)
        label_start = html.rfind('<label', 0, select_start)
        label = html[label_start: html.index('</label>', label_start)]
        self.assertIn('Бўлим', label)
        self.assertNotIn('text-red-500', label)
        # «Цех» по-прежнему обязателен
        department_start = html.index('id="department_id"')
        self.assertIn('required', html[html.rfind('<select', 0, department_start): html.index('>', department_start) + 1])

    def test_moving_an_employee_to_another_department_without_a_section_needs_the_section_to_be_cleared(self):
        old_department = Department.objects.create(name='20-Цех', sort_order=20)
        old_section = Section.objects.create(department=old_department, name='Юридик хизмати', code='1')
        employee = Employee.objects.create(first_name='A', last_name='B', tabel_number='OPT-6', department=old_department, section=old_section)

        kept = self.api.patch(f'/api/employees/{employee.slug}/', {'department_id': self.department.pk}, format='multipart')
        cleared = self.api.patch(f'/api/employees/{employee.slug}/', {'department_id': self.department.pk, 'section_id': ''}, format='multipart')

        # бўлим юборилмаса, эски бўлим қолади ва сервер хатони section_id калити остида қайтаради (форма шуни кўрсатади)
        self.assertEqual(kept.status_code, 400)
        self.assertEqual(kept.json(), {'section_id': ['Бўлим танланган цехга тегишли эмас.']})
        self.assertEqual(cleared.status_code, 200, cleared.content)
        employee.refresh_from_db()
        self.assertEqual(employee.department_id, self.department.pk)
        self.assertIsNone(employee.section)

    def test_hire_date_is_optional(self):
        without = self.api.post(CREATE_URL, self.payload('OPT-7'), format='multipart')
        empty = self.api.post(CREATE_URL, self.payload('OPT-8', date_of_employment=''), format='multipart')
        given = self.api.post(CREATE_URL, self.payload('OPT-9', date_of_employment='01/02/2024'), format='multipart')

        self.assertEqual((without.status_code, empty.status_code, given.status_code), (201, 201, 201), (without.content, empty.content, given.content))
        self.assertIsNone(Employee.objects.get(tabel_number='OPT-7').date_of_employment)
        self.assertIsNone(Employee.objects.get(tabel_number='OPT-8').date_of_employment)
        self.assertEqual(str(Employee.objects.get(tabel_number='OPT-9').date_of_employment), '2024-02-01')

    def test_create_page_does_not_mark_the_hire_date_as_required(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/employees/create/').content.decode()

        input_start = html.index('id="date_of_employment"')
        self.assertNotIn('required', html[html.rfind('<input', 0, input_start): html.index('>', input_start) + 1])
        label_start = html.rfind('<label', 0, input_start)
        label = html[label_start: html.index('</label>', label_start)]
        self.assertIn('Ишга қабул қилинган сана', label)
        self.assertNotIn('text-red-500', label)

    def test_pages_show_the_server_error_text_instead_of_a_generic_message(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)
        employee = Employee.objects.create(first_name='A', last_name='B', tabel_number='OPT-10', department=self.department)

        for url in ('/employees/create/', f'/employees/{employee.slug}/edit/', f'/employees/{employee.slug}/delete/', '/'):
            with self.subTest(url=url):
                html = web.get(url).content.decode()
                self.assertIn('function extractApiErrorMessage', html)   # базадан келади (base.html)
                if url != '/':
                    self.assertIn('extractApiErrorMessage(payload)', html)
                self.assertNotIn('payload?.detail || payload?.message ||', html)

    def test_edit_page_always_sends_the_section_so_that_an_empty_choice_clears_it(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)
        employee = Employee.objects.create(first_name='A', last_name='B', tabel_number='OPT-11', department=self.department)

        html = web.get(f'/employees/{employee.slug}/edit/').content.decode()

        self.assertIn("formData.append('section_id', sectionId);", html)
        self.assertNotIn('if (sectionId) {', html)

    def test_create_page_does_not_pick_the_first_section_by_itself(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/employees/create/').content.decode()

        self.assertNotIn('firstAvailableOption', html)
