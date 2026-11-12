from .models import Employee, Section
from .test_section_hierarchy import HierarchyTestCase, SECTIONS_URL


class SectionNameConflictTests(HierarchyTestCase):
    """Бўлим номи цех ичида ноёб (ички бўлимлар учун ҳам), лекин хато тушунарли ва ўзбекча бўлиши керак."""

    def error(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        self.assertNotIn('unique set', str(payload))   # аввалги инглизча DRF хабари қайтмаслиги керак
        return payload

    def test_inner_section_named_like_an_existing_top_level_section_explains_how_to_move_it(self):
        Section.objects.create(department=self.department, name='Ходимларни тайёрлаш бўлими')
        hr = self.make('HR хизмати')

        response = self.post(name='Ходимларни тайёрлаш бўлими', parent_id=hr.pk)

        message = ' '.join(self.error(response)['name'])
        self.assertIn('«Ходимларни тайёрлаш бўлими»', message)
        self.assertIn('асосий бўлим', message)           # мавжуд бўлим қаерда турганини айтади
        self.assertIn('«HR хизмати» ичига кўчириш', message)
        self.assertIn('«Юқори бўлим»', message)           # нима қилиш кераклиги
        self.assertEqual(Section.objects.filter(name='Ходимларни тайёрлаш бўлими').count(), 1)

    def test_name_taken_by_an_inner_section_shows_its_full_path(self):
        parent = self.make('Юридик хизмати')
        self.make('Юридик бўлим', parent=parent)
        other = self.make('Бошқа хизмат')

        message = ' '.join(self.error(self.post(name='Юридик бўлим', parent_id=other.pk))['name'])

        self.assertIn('Юридик хизмати / Юридик бўлим', message)
        self.assertNotIn('Юқори бўлим', message)           # кўчириш маслаҳати фақат асосий бўлим учун

    def test_top_level_duplicate_has_no_move_hint(self):
        self.make('Такрор')

        message = ' '.join(self.error(self.post(name='Такрор'))['name'])

        self.assertIn('аллақачон мавжуд', message)
        self.assertNotIn('кўчириш', message)

    def test_renaming_into_an_existing_name_is_rejected_without_the_creation_hint(self):
        taken = self.make('Банд ном')
        other = self.make('Бошқа')

        message = ' '.join(self.error(self.put(other, name=taken.name))['name'])

        self.assertIn('«Банд ном»', message)
        self.assertNotIn('янгисини яратманг', message)

    def test_same_name_is_still_fine_in_another_department(self):
        self.make('Умумий ном')

        response = self.post(name='Умумий ном', department_id=self.other_department.pk)

        self.assertEqual(response.status_code, 201, response.content)

    def test_saving_a_section_under_its_own_name_is_not_a_conflict(self):
        section = self.make('Ўз номи')

        response = self.put(section, code='5')

        self.assertEqual(response.status_code, 200, response.content)

    def test_creating_without_a_department_is_a_clean_400_not_a_server_error(self):
        response = self.client.post(SECTIONS_URL, {'name': 'Цехсиз'}, format='json')

        self.assertIn('department_id', self.error(response))
        self.assertFalse(Section.objects.filter(name='Цехсиз').exists())

    def test_existing_top_level_section_can_be_moved_inside_a_parent_keeping_its_employees(self):
        # Тавсия этилган йўл: янги нусха яратиш ўрнига мавжуд бўлимни таҳрирлаб «Юқори бўлим» танлаш.
        existing = self.make('Ходимларни тайёрлаш бўлими')
        hr = self.make('HR хизмати')
        employee = Employee.objects.create(
            first_name='A', last_name='B', tabel_number='MOVE-1', department=self.department, section=existing,
        )

        response = self.put(existing, parent_id=hr.pk)

        self.assertEqual(response.status_code, 200, response.content)
        existing.refresh_from_db()
        employee.refresh_from_db()
        self.assertEqual(existing.parent_id, hr.pk)
        self.assertEqual(employee.section_id, existing.pk)   # ходим ўз бўлимида қолади
        self.assertEqual(response.json()['data']['path'], 'HR хизмати / Ходимларни тайёрлаш бўлими')
