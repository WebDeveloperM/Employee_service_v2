from io import BytesIO
from urllib.parse import quote

import openpyxl
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Department, Employee, Section
from .staff_export import UNASSIGNED_TITLE, build_staff_workbook, staff_export_groups
from .test_staff import StaffTestCase


EXPORT_URL = '/api/template/staff/export/'


def outline(groups):
    """[(даража, сарлавҳа, [табеллар])] — тартибни бир қарашда солиштириш учун."""
    return [(group.level, group.title, [employee.tabel_number for employee in group.employees]) for group in groups]


class StaffExportGroupsTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        Department.objects.all().delete()   # setUp даги цех ва бўлимлар бу ерда керак эмас

    def employee(self, tabel, last_name, position='', department=None, section=None, **extra):
        return Employee.objects.create(
            first_name='Ism', last_name=last_name, tabel_number=tabel, position=position,
            department=department, section=section, **extra,
        )

    def test_unit_row_comes_first_and_its_employees_follow_in_tree_order(self):
        tsex = Department.objects.create(name='7-Цех Ўлчов асбоблари', sort_order=7)
        rahbariyat = Department.objects.create(name='Раҳбарият', sort_order=50)   # дарахтдаги каби биринчи
        second = Section.objects.create(department=tsex, code='2', name='Таъмирлаш участкаси')
        first = Section.objects.create(department=tsex, code='1', name='Цех бошқарув ходимлари')
        nested = Section.objects.create(department=tsex, code='5', name='1-бригада', parent=second)
        self.make_position('Бошлиқ', department=tsex, section=first)
        self.make_position('Муҳандис', staff_count=3, department=tsex, section=first)

        self.employee('D1', 'Цехов', 'Цех бошлиғи', department=tsex)
        self.employee('F3', 'Аҳмедов', 'Муҳандис', department=tsex, section=first)
        self.employee('F1', 'Яхёев', 'Бошлиқ', department=tsex, section=first)
        self.employee('F2', 'Бобоев', 'Муҳандис', department=tsex, section=first)
        self.employee('F4', 'Абдуллаев', 'Котиба', department=tsex, section=first)   # штатда йўқ — охирида
        self.employee('S1', 'Саидов', 'Слесарь', department=tsex, section=second)
        self.employee('N1', 'Нуриев', 'Слесарь', department=tsex, section=nested)
        self.employee('R1', 'Раҳимов', 'Директор', department=rahbariyat)

        self.assertEqual(outline(staff_export_groups()), [
            (0, 'Раҳбарият', ['R1']),
            (0, '7-Цех Ўлчов асбоблари', ['D1']),
            (1, 'Цех бошқарув ходимлари', ['F1', 'F3', 'F2', 'F4']),   # штат лавозимлари тартибида, сўнг фамилия бўйича
            (1, 'Таъмирлаш участкаси', ['S1']),
            (2, 'Таъмирлаш участкаси / 1-бригада', ['N1']),             # ички бўлим ота бўлимдан кейин, йўли билан
        ])

    def test_units_without_active_employees_are_left_out(self):
        tsex = Department.objects.create(name='3-Цех')
        Department.objects.create(name='4-Цех бўш')
        empty = Section.objects.create(department=tsex, code='1', name='Бўш бўлим')
        parent = Section.objects.create(department=tsex, code='2', name='Ота бўлим')
        child = Section.objects.create(department=tsex, code='3', name='Ички бўлим', parent=parent)
        self.employee('X1', 'Ишдан', department=tsex, section=empty, is_active=False)
        self.employee('X2', 'Ўчирилган', department=tsex, section=empty, is_deleted=True)
        self.employee('C1', 'Ичкиев', department=tsex, section=child)

        self.assertEqual(outline(staff_export_groups()), [
            (0, '3-Цех', []),                          # ўз ходими йўқ, лекин бўлимлари бор — сарлавҳа қолади
            (1, 'Ота бўлим', []),
            (2, 'Ота бўлим / Ички бўлим', ['C1']),
        ])

    def test_employees_without_department_go_to_the_end(self):
        tsex = Department.objects.create(name='5-Цех')
        self.employee('A1', 'Бирор', department=tsex)
        self.employee('Z1', 'Жойсиз')

        self.assertEqual(outline(staff_export_groups())[-1], (0, UNASSIGNED_TITLE, ['Z1']))

    def test_nobody_is_lost_when_a_section_is_outside_the_tree(self):
        tsex = Department.objects.create(name='6-Цех')
        other = Department.objects.create(name='8-Цех')
        parent = Section.objects.create(department=other, code='1', name='Бошқа цехдаги ота')
        stray = Section.objects.create(department=tsex, code='2', name='Адашган бўлим', parent=parent)
        self.employee('Y1', 'Адашган', department=tsex, section=stray)

        groups = outline(staff_export_groups())

        self.assertIn((0, '6-Цех / Адашган бўлим', ['Y1']), groups)
        self.assertEqual(sum(len(tabels) for _, _, tabels in groups), Employee.objects.count())


class StaffExportWorkbookTests(StaffTestCase):
    def test_workbook_has_header_then_unit_rows_with_their_employees(self):
        position = self.make_position('Юрист', section=self.section)
        Employee.objects.create(first_name='Ali', last_name='Valiyev', surname='Gani o‘g‘li', tabel_number='007',
                                position='  Юрист ', department=self.department, section=self.section)
        self.assertTrue(position.pk)

        sheet = openpyxl.load_workbook(BytesIO(build_staff_workbook(staff_export_groups()))).active
        rows = [tuple(cell.value for cell in row) for row in sheet.iter_rows()]

        self.assertEqual(rows, [
            ('№', 'Ф.И.Ш.', 'Табель рақами', 'Лавозим'),
            ('1 цех', None, None, None),
            ('Юридик бўлим', None, None, None),
            (1, 'Valiyev Ali Gani o‘g‘li', '007', 'Юрист'),   # табелдаги бош нол сақланади
        ])
        self.assertEqual({str(merged) for merged in sheet.merged_cells.ranges}, {'A2:D2', 'A3:D3'})
        self.assertTrue(sheet['A1'].font.b)
        self.assertTrue(sheet['A2'].font.b and sheet['A3'].font.b)
        self.assertFalse(sheet['B4'].font.b)
        self.assertNotEqual(sheet['A2'].fill.fgColor.rgb, sheet['A3'].fill.fgColor.rgb)   # цех ва бўлим сатри фарқли
        self.assertEqual(sheet.freeze_panes, 'A2')

    def test_empty_organisation_still_gives_a_header_only_file(self):
        Department.objects.all().delete()
        sheet = openpyxl.load_workbook(BytesIO(build_staff_workbook(staff_export_groups()))).active
        self.assertEqual(sheet.max_row, 1)


class StaffExportEndpointTests(StaffTestCase):
    def test_admin_and_hr_download_the_xlsx_with_a_dated_cyrillic_file_name(self):
        self.make_employee('Юрист', department=self.department, section=self.section)
        expected_name = f'Штат бирлиги ва лавозим - {timezone.localdate():%d.%m.%Y}.xlsx'

        for user in (self.admin, self.hr):
            with self.subTest(role=user.profile.role):
                client = APIClient()
                client.force_authenticate(user=user)
                response = client.get(EXPORT_URL)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.assertEqual(response['Content-Disposition'], f"attachment; filename*=utf-8''{quote(expected_name)}")
                sheet = openpyxl.load_workbook(BytesIO(response.content)).active
                self.assertEqual(sheet['A2'].value, '1 цех')

    def test_session_login_works_like_the_page_does(self):
        client = APIClient()
        client.force_login(self.admin)
        self.assertEqual(client.get(EXPORT_URL).status_code, 200)

    def test_regular_user_and_anonymous_cannot_download(self):
        client = APIClient()
        client.force_authenticate(user=self.regular)
        self.assertEqual(client.get(EXPORT_URL).status_code, 403)
        self.assertIn(APIClient().get(EXPORT_URL).status_code, (401, 403))


class StaffExportButtonTests(TestCase):
    def test_staff_page_has_the_export_button(self):
        from django.contrib.auth.models import User
        from .models import ROLE_ADMIN, UserProfile

        user = User.objects.create_user(username='export_admin', password='secret123')
        UserProfile.objects.create(user=user, role=ROLE_ADMIN)
        self.client.force_login(user)

        html = self.client.get('/staff/').content.decode()

        self.assertIn('id="staffExportButton" onclick="exportStaffExcel()"', html)
        self.assertIn("fetch(API_BASE + '/template/staff/export/'", html)
        self.assertIn('Excel экспорти', html)
