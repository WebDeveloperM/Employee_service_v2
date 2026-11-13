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

    def test_the_move_request_sent_by_the_sections_page_works_with_the_chosen_id(self):
        # «Ички бўлим қўшиш» да номи мавжуд асосий бўлим билан бир хил бўлса, саҳифа шуни юборади (departments.html, saveSection).
        existing = self.make('Ходимларни тайёрлаш ва қайта тайёрлаш бўлими')
        hr = self.make('HR хизмати')
        self.make('Ташкилий ривожлантириш', parent=hr, code='1')
        self.make('Персонал билан ишлаш', parent=hr, code='3')
        payload = {'name': existing.name, 'department_id': self.department.pk, 'parent_id': hr.pk, 'code': '4'}

        response = self.client.put(f'{SECTIONS_URL}{existing.pk}/', payload, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        existing.refresh_from_db()
        self.assertEqual((existing.parent_id, existing.code, existing.name), (hr.pk, '4', 'Ходимларни тайёрлаш ва қайта тайёрлаш бўлими'))
        self.assertEqual(Section.objects.filter(name=existing.name).count(), 1)   # нусха яратилмаган, фақат кўчирилган

    def test_moving_with_an_id_already_used_by_the_parents_children_is_rejected_clearly(self):
        existing = self.make('Кўчириладиган бўлим')
        hr = self.make('HR хизмати')
        self.make('Бошқа ички бўлим', parent=hr, code='4')

        response = self.client.put(
            f'{SECTIONS_URL}{existing.pk}/',
            {'name': existing.name, 'department_id': self.department.pk, 'parent_id': hr.pk, 'code': '4'}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('Бу юқори бўлимда шундай ID ли ички бўлим аллақачон мавжуд', ' '.join(response.json()['code']))
        existing.refresh_from_db()
        self.assertIsNone(existing.parent_id)   # хато бўлса, бўлим жойида қолади

    def test_sections_page_offers_to_move_instead_of_creating_a_duplicate(self):
        from django.test import Client

        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/sections/').content.decode()

        self.assertIn('if (!sectionId && formData.parent_id) {', html)
        self.assertIn('!section.parent_id', html)                                    # фақат АСОСИЙ бўлим билан тўқнашганда
        self.assertIn('ичига кўчирайликми?', html)
        self.assertIn('Бўлимнинг ходимлари ва лавозимлари сақланади.', html)
        self.assertIn("method: 'PUT'", html)

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
