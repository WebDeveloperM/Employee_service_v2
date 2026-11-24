"""Ходим неча штатда ишлайди (Employee.staff_rate): қўшишда киритилади, «Банд» — улушлар йиғиндиси."""
from decimal import Decimal

from django.test import Client

from .models import Employee
from .staff import collect_staff_stats, distribute_unbound, employees_of_position, parse_staff_rate
from .test_staff import POSITIONS_URL, StaffTestCase, TREE_URL


class StaffRateTests(StaffTestCase):
    def employees_url(self, position):
        return f'{POSITIONS_URL}{position.pk}/employees/'

    def add(self, position, employee, rate=None):
        payload = {'employee_id': employee.pk}
        if rate is not None:
            payload['staff_rate'] = rate
        return self.client.post(self.employees_url(position), payload, format='json')

    def newcomer(self):
        return self.make_employee('', department=self.other_department)

    # --- қўшиш ---

    def test_without_a_rate_the_employee_takes_a_full_position(self):
        position = self.make_position('Фаррош', staff_count=6)
        employee = self.newcomer()

        response = self.add(position, employee)

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual(employee.staff_rate, Decimal('1'))
        meta = response.json()['position']
        self.assertEqual((meta['staff_count'], meta['occupied'], meta['vacant']), (6, 1, 5))
        self.assertEqual(response.json()['data'][0]['staff_rate'], 1)

    def test_half_rate_is_saved_and_counted_as_half(self):
        position = self.make_position('Фаррош', staff_count=6)
        employee = self.newcomer()

        response = self.add(position, employee, 0.5)

        self.assertEqual(response.status_code, 201, response.content)
        employee.refresh_from_db()
        self.assertEqual(employee.staff_rate, Decimal('0.5'))
        meta = response.json()['position']
        self.assertEqual((meta['occupied'], meta['vacant']), (0.5, 5.5))
        self.assertEqual(response.json()['data'][0]['staff_rate'], 0.5)

    def test_rate_accepts_a_comma_and_a_string(self):
        position = self.make_position('Фаррош', staff_count=6)
        for value, expected in (('0,5', Decimal('0.5')), ('0.25', Decimal('0.25')), ('1', Decimal('1')), ('', Decimal('1'))):
            with self.subTest(value=value):
                employee = self.newcomer()
                response = self.add(position, employee, value)
                self.assertEqual(response.status_code, 201, response.content)
                employee.refresh_from_db()
                self.assertEqual(employee.staff_rate, expected)

    def test_bad_rates_are_refused_in_uzbek_and_nothing_changes(self):
        position = self.make_position('Фаррош', staff_count=6)
        employee = self.newcomer()
        for value in (0, -0.5, 2.5, 'abc', '0.125', 'NaN', 'Infinity'):
            with self.subTest(value=value):
                response = self.add(position, employee, value)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('staff_rate', response.json())
                self.assertIn('Штат улуш', str(response.json()))
        employee.refresh_from_db()
        self.assertEqual((employee.position, employee.staff_position_id), ('', None))

    def test_two_halves_fill_one_position_and_a_third_person_is_refused(self):
        position = self.make_position('Фаррош', staff_count=1)

        statuses = [self.add(position, self.newcomer(), 0.5).status_code for _ in range(3)]

        self.assertEqual(statuses, [201, 201, 400])

    def test_a_rate_bigger_than_the_free_part_is_refused_with_the_free_amount(self):
        position = self.make_position('Фаррош', staff_count=Decimal('1.5'))
        self.assertEqual(self.add(position, self.newcomer()).status_code, 201)

        refused = self.add(position, self.newcomer())   # тўлиқ ставка, бўш эса 0,5

        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn('бўш штат: 0,5', refused.json()['detail'])
        self.assertEqual(self.add(position, self.newcomer(), '0,5').status_code, 201)   # ярим ставка сиғади

    def test_half_staff_position_takes_a_half_rate_person(self):
        position = self.make_position('Фаррош', staff_count=Decimal('0.5'))

        self.assertEqual(self.add(position, self.newcomer()).status_code, 400)   # тўлиқ ставка сиғмайди
        accepted = self.add(position, self.newcomer(), 0.5)

        self.assertEqual(accepted.status_code, 201, accepted.content)
        self.assertEqual(accepted.json()['position']['vacant'], 0)

    # --- рўйхат, дарахт ва йиғиндилар бир хил ---

    def test_positions_list_tree_and_window_show_the_same_sum_of_rates(self):
        position = self.make_position('Фаррош', staff_count=6, section=self.section)
        self.add(position, self.newcomer(), 0.5)
        self.add(position, self.newcomer(), 1)
        self.add(position, self.newcomer(), 0.25)

        window = self.client.get(self.employees_url(position)).json()['position']
        listing = self.client.get(f'{POSITIONS_URL}?section_id={self.section.pk}').json()
        tree = self.client.get(TREE_URL).json()['data']
        department = next(node for node in tree if node['id'] == self.department.pk)

        self.assertEqual((window['occupied'], window['vacant']), (1.75, 4.25))
        self.assertEqual((listing['data'][0]['occupied'], listing['data'][0]['vacant']), (1.75, 4.25))
        self.assertEqual(listing['summary']['own_vacant'], 4.25)
        self.assertEqual(department['vacant_total'], 4.25)

    def test_removing_frees_the_rate_and_resets_it_to_one(self):
        position = self.make_position('Фаррош', staff_count=1)
        employee = self.newcomer()
        self.add(position, employee, 0.5)

        response = self.client.delete(f'{self.employees_url(position)}{employee.pk}/')

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.position, employee.staff_rate), ('', Decimal('1')))
        self.assertEqual(response.json()['position']['vacant'], 1)

    def test_transfer_keeps_the_rate_and_needs_only_that_much_room(self):
        source = self.make_position('Фаррош', staff_count=2)
        target = self.make_position('Гардеробчи', staff_count=Decimal('0.5'))
        employee = self.newcomer()
        self.add(source, employee, 0.5)

        response = self.client.patch(f'{self.employees_url(source)}{employee.pk}/', {'position_id': target.pk}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        employee.refresh_from_db()
        self.assertEqual((employee.staff_position_id, employee.staff_rate), (target.pk, Decimal('0.5')))

    def test_transfer_of_a_full_rate_person_into_half_free_room_is_refused(self):
        source = self.make_position('Фаррош', staff_count=2)
        target = self.make_position('Гардеробчи', staff_count=Decimal('0.5'))
        employee = self.newcomer()
        self.add(source, employee)

        response = self.client.patch(f'{self.employees_url(source)}{employee.pk}/', {'position_id': target.pk}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('бўш штат: 0,5', response.json()['detail'])

    # --- карточка/импорт орқали келганлар (привязкасиз) ва бир хил номли лавозимлар ---

    def test_existing_employees_count_as_one_like_before(self):
        position = self.make_position('Слесарь', staff_count=3)
        self.make_employee('Слесарь', department=self.department)
        self.make_employee('Слесарь', department=self.department)

        item = self.client.get(f'{POSITIONS_URL}?department_id={self.department.pk}').json()['data'][0]

        self.assertEqual((item['occupied'], item['vacant']), (2, 1))
        self.assertEqual(collect_staff_stats().occupied_for(position), 2)

    def test_same_name_rows_share_unbound_people_by_their_rates(self):
        first = self.make_position('Слесарь', staff_count=1)
        second = self.make_position('Слесарь', staff_count=1)
        # Фамилия бўйича тартиб: Familiya1 (0,5), Familiya2 (0,5), Familiya3 (1).
        people = [
            self.make_employee('Слесарь', department=self.department, staff_rate=Decimal('0.5')),
            self.make_employee('Слесарь', department=self.department, staff_rate=Decimal('0.5')),
            self.make_employee('Слесарь', department=self.department),
        ]

        stats = collect_staff_stats()

        self.assertEqual([employee.pk for employee in employees_of_position(first)], [people[0].pk, people[1].pk])
        self.assertEqual([employee.pk for employee in employees_of_position(second)], [people[2].pk])
        self.assertEqual((stats.occupied_for(first), stats.occupied_for(second)), (1, 1))

    def test_distribution_with_whole_rates_is_unchanged(self):
        self.assertEqual(distribute_unbound([Decimal('0.5')], [0], 2), [2])
        self.assertEqual(distribute_unbound([1, 1], [0, 0], 3), [1, 2])
        self.assertEqual(distribute_unbound([Decimal('1.5'), 1], [0, 0], 3), [2, 1])   # 1,5 штатга 2 киши, олдингидек
        self.assertEqual(distribute_unbound([1, 1], [0, 0], [Decimal('0.5'), Decimal('0.5'), 1]), [2, 1])

    def test_parse_staff_rate(self):
        self.assertEqual(parse_staff_rate(None), Decimal('1'))
        self.assertEqual(parse_staff_rate('0,75'), Decimal('0.75'))
        self.assertEqual(parse_staff_rate(2), Decimal('2'))
        for value in ('0', '2.01', '1e5', 'x', '0.001'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_staff_rate(value)

    def test_lookup_returns_the_current_rate(self):
        employee = self.make_employee('Слесарь', department=self.department, staff_rate=Decimal('0.5'))

        data = self.client.get('/api/template/staff/employee-lookup/', {'tabel_number': employee.tabel_number}).json()['data']

        self.assertEqual(data[0]['staff_rate'], 0.5)

    # --- саҳифа ---

    def test_page_has_the_rate_input_next_to_the_add_button_and_sends_it(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin)

        html = web.get('/staff/').content.decode()

        self.assertIn('data-rate-for="\' + employee.id + \'" value="1" min="0.25" max="2" step="0.25" inputmode="decimal"', html)
        self.assertIn('rateInput + \'<button type="button" data-add="', html)
        self.assertIn('flex flex-wrap items-center gap-3 rounded-2xl', html)   # телефонда тугма қатордан чиқмайди
        self.assertIn('body: JSON.stringify({ employee_id: employeeId, staff_rate: rate })', html)
        self.assertIn('>Штат улуши</th>', html)
        self.assertEqual(Employee._meta.get_field('staff_rate').default, Decimal('1'))
