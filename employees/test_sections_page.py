import re

from django.contrib.auth.models import User
from django.test import Client, TestCase

from .models import ROLE_ADMIN, ROLE_USER, UserProfile


def active_menu_labels(html):
    """Подписи пунктов бокового меню, помеченных как активные."""
    links = re.findall(r'<a href="([^"]+)" class="([^"]*)"', html)
    return [href for href, classes in links if 'sidebar-link' in classes.split() and 'active' in classes.split()]


class SectionsPageTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='sections_admin', password='secret123')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        self.client = Client(HTTP_HOST='192.168.2.72')
        self.client.force_login(admin)

    def test_sections_url_is_a_page_of_its_own_not_a_redirect(self):
        response = self.client.get('/sections/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.templates[0].name, 'departments.html')

    def test_sections_page_contains_only_sections(self):
        html = self.client.get('/sections/').content.decode()

        self.assertIn('const SECTIONS_ONLY = true;', html)
        self.assertIn('id="sectionsTableBody"', html)
        self.assertIn('id="sectionModal"', html)
        for departments_only in ('id="departmentsTableBody"', 'id="departmentModal"', 'id="departmentSearchInput"', 'Цех қўшиш</button>'):
            self.assertNotIn(departments_only, html)
        self.assertIn('<title>Бўлимлар - Ходимлар хизмати</title>', html)

    def test_departments_page_keeps_its_departments_table(self):
        html = self.client.get('/departments/').content.decode()

        self.assertIn('const SECTIONS_ONLY = false;', html)
        self.assertIn('id="departmentsTableBody"', html)
        self.assertIn('id="departmentModal"', html)
        self.assertIn('id="sectionsTableBody"', html)
        self.assertIn('<title>Цехлар ва бўлимлар - Ходимлар хизмати</title>', html)

    def test_menu_has_separate_links_and_marks_only_the_current_one_active(self):
        sections = self.client.get('/sections/').content.decode()
        departments = self.client.get('/departments/').content.decode()

        self.assertIn('<a href="/sections/"', sections)
        self.assertNotIn('/departments/#sections', sections)
        self.assertIn('/sections/', active_menu_labels(sections))
        self.assertNotIn('/departments/', active_menu_labels(sections))
        self.assertIn('/departments/', active_menu_labels(departments))
        self.assertNotIn('/sections/', active_menu_labels(departments))

    def test_anonymous_user_is_sent_to_login(self):
        response = Client(HTTP_HOST='192.168.2.72').get('/sections/')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/login/'))
