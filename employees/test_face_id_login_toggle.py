"""«Face ID орқали кириш»: администратор ходим учун ёқади/ўчиради («Фойдаланувчилар рухсатлари» → «Рухсатни таҳрирлаш»)."""
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import Employee, ROLE_ADMIN, ROLE_HR, ROLE_USER, UserProfile


def photo(name='face.jpg'):
    buffer = BytesIO()
    Image.new('RGB', (32, 32), color='white').save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


def make_user(username, role, *, with_photo=True, face_id_login_enabled=True):
    user = User.objects.create_user(username=username, password='secret123')
    profile = UserProfile.objects.create(user=user, role=role, face_id_login_enabled=face_id_login_enabled)
    Employee.objects.create(
        first_name='Ism', last_name=username, tabel_number=f'T-{username}', login=username, user=user,
        base_image=photo(f'{username}.jpg') if with_photo else None,
    )
    return profile


def api_as(profile):
    client = APIClient()
    client.force_authenticate(user=profile.user)
    return client


def users_url(profile):
    return f'/api/template/users/{profile.pk}/'


class FaceIdLoginDefaultsTests(TestCase):
    def test_existing_behaviour_is_kept_face_id_is_on_by_default(self):
        admin = make_user('def_admin', ROLE_ADMIN)
        hr = make_user('def_hr', ROLE_HR)
        user = make_user('def_user', ROLE_USER)

        self.assertTrue(UserProfile.objects.get(pk=admin.pk).face_id_login_enabled)
        self.assertTrue(admin.requires_face_id_login)
        self.assertTrue(hr.requires_face_id_login)
        self.assertFalse(user.requires_face_id_login)          # оддий фойдаланувчи учун Face ID қадами йўқ (аввалгидек)

    def test_turned_off_face_id_is_not_required_for_admin_and_hr(self):
        self.assertFalse(make_user('off_admin', ROLE_ADMIN, face_id_login_enabled=False).requires_face_id_login)
        self.assertFalse(make_user('off_hr', ROLE_HR, face_id_login_enabled=False).requires_face_id_login)


class FaceIdLoginFlowTests(TestCase):
    def login(self, username):
        client = Client(HTTP_HOST='192.168.101.6')
        response = client.post('/login/', {'username': username, 'password': 'secret123'})
        return client, response

    def test_face_id_on_password_is_followed_by_the_face_step(self):
        profile = make_user('flow_on', ROLE_HR)

        client, response = self.login('flow_on')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Face ID тасдиғи')
        self.assertEqual(client.session.get('pending_face_auth_user_id'), profile.user_id)
        self.assertNotIn('_auth_user_id', client.session)        # ҳали кирмаган — юз тасдиқланишини кутади

    def test_face_id_off_logs_in_with_login_and_password_only(self):
        profile = make_user('flow_off', ROLE_ADMIN, face_id_login_enabled=False)

        client, response = self.login('flow_off')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(client.session['_auth_user_id']), profile.user_id)
        self.assertNotIn('pending_face_auth_user_id', client.session)

    def test_face_id_off_works_even_without_a_photo(self):
        profile = make_user('flow_nophoto', ROLE_HR, with_photo=False, face_id_login_enabled=False)

        client, response = self.login('flow_nophoto')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(client.session['_auth_user_id']), profile.user_id)

    def test_wrong_password_is_still_rejected_when_face_id_is_off(self):
        make_user('flow_wrong', ROLE_ADMIN, face_id_login_enabled=False)
        client = Client(HTTP_HOST='192.168.101.6')

        response = client.post('/login/', {'username': 'flow_wrong', 'password': 'not-the-password1'})

        self.assertContains(response, 'Логин ёки парол нотўғри.')
        self.assertNotIn('_auth_user_id', client.session)

    def test_a_pending_face_session_cannot_be_finished_after_face_id_was_turned_off(self):
        profile = make_user('flow_stale', ROLE_ADMIN, face_id_login_enabled=False)
        client = Client(HTTP_HOST='192.168.101.6')
        session = client.session
        session['pending_face_auth_user_id'] = profile.user_id
        session.save()

        response = client.post('/api/v1/auth/login-face-verify/', data={'captured_images': ['data:image/jpeg;base64,ZmFrZQ==']}, content_type='application/json')

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('_auth_user_id', client.session)

    @patch('employees.web_views.resolve_bnpzid_client_access', return_value={'allowed': True, 'requires_face_id': True, 'error': ''})
    def test_a_bnpzid_client_that_demands_face_id_still_gets_it(self, _access):
        profile = make_user('flow_client', ROLE_ADMIN, face_id_login_enabled=False)
        client = Client(HTTP_HOST='192.168.101.6')

        response = client.post('/bnpzid/authorize/', {
            'username': 'flow_client', 'password': 'secret123', 'client_id': 'tb-project',
            'redirect_uri': 'http://localhost:5175/auth/signin', 'state': 's',
        })

        self.assertContains(response, 'Face ID тасдиғи')       # ташқи тизим талаби бу созламадан устун
        self.assertEqual(client.session.get('pending_face_auth_user_id'), profile.user_id)


class FaceIdLoginPermissionTests(TestCase):
    def setUp(self):
        self.admin = make_user('perm_admin', ROLE_ADMIN)
        self.hr = make_user('perm_hr', ROLE_HR)
        self.target = make_user('perm_target', ROLE_HR)

    def test_admin_turns_it_off_and_on(self):
        api = api_as(self.admin)

        off = api.patch(users_url(self.target), {'role': ROLE_HR, 'face_id_login_enabled': 'false'}, format='multipart')
        self.assertEqual(off.status_code, 200, off.content)
        self.assertFalse(off.json()['data']['face_id_login_enabled'])
        self.target.refresh_from_db()
        self.assertFalse(self.target.face_id_login_enabled)
        self.assertEqual(self.target.role, ROLE_HR)

        on = api.patch(users_url(self.target), {'role': ROLE_HR, 'face_id_login_enabled': 'true'}, format='multipart')
        self.assertEqual(on.status_code, 200, on.content)
        self.target.refresh_from_db()
        self.assertTrue(self.target.face_id_login_enabled)

    def test_hr_cannot_change_it(self):
        response = api_as(self.hr).patch(users_url(self.target), {'face_id_login_enabled': 'false'}, format='multipart')

        self.assertEqual(response.status_code, 403)
        self.target.refresh_from_db()
        self.assertTrue(self.target.face_id_login_enabled)

    def test_nobody_but_admin_can_turn_off_their_own_face_id(self):
        for profile in (self.hr, make_user('perm_plain', ROLE_USER)):
            response = api_as(profile).patch(users_url(profile), {'face_id_login_enabled': 'false'}, format='multipart')

            self.assertEqual(response.status_code, 403, profile.user.username)
            profile.refresh_from_db()
            self.assertTrue(profile.face_id_login_enabled)

    def test_hr_can_still_save_other_fields_when_the_value_is_unchanged(self):
        # «Фойдаланувчилар» саҳифаси бу майдонни юбормайди; юборилса ҳам, ўзгармаган қиймат тўсилмайди.
        response = api_as(self.hr).patch(users_url(self.target), {'first_name': 'Янги', 'face_id_login_enabled': 'true'}, format='multipart')

        self.assertEqual(response.status_code, 200, response.content)
        self.target.refresh_from_db()
        self.assertEqual(self.target.user.first_name, 'Янги')

    def test_the_photo_is_required_only_while_face_id_is_on(self):
        no_photo = make_user('perm_nophoto', ROLE_USER, with_photo=False)
        api = api_as(self.admin)

        refused = api.patch(users_url(no_photo), {'role': ROLE_HR, 'face_id_login_enabled': 'true'}, format='multipart')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('role', refused.json().get('errors', refused.json()))

        allowed = api.patch(users_url(no_photo), {'role': ROLE_HR, 'face_id_login_enabled': 'false'}, format='multipart')
        self.assertEqual(allowed.status_code, 200, allowed.content)
        no_photo.refresh_from_db()
        self.assertEqual((no_photo.role, no_photo.face_id_login_enabled), (ROLE_HR, False))


class FaceIdLoginAccessPageTests(TestCase):
    def setUp(self):
        self.admin = make_user('page_admin', ROLE_ADMIN)
        self.target = make_user('page_target', ROLE_HR, face_id_login_enabled=False)

    def test_access_list_reports_the_setting(self):
        response = api_as(self.admin).get('/api/template/user-access/')

        rows = {row['login']: row for row in response.json()['data']}
        self.assertEqual((rows['page_admin']['face_id_login_enabled'], rows['page_target']['face_id_login_enabled']), (True, False))

    def test_edit_modal_has_the_switch_and_sends_it(self):
        web = Client(HTTP_HOST='192.168.101.6')
        web.force_login(self.admin.user)

        html = web.get('/settings/access/').content.decode()

        self.assertIn('Face ID орқали кириш', html)
        self.assertIn('id="accessFaceIdLogin" role="switch"', html)
        self.assertIn("formData.append('face_id_login_enabled', faceIdLoginEnabled ? 'true' : 'false');", html)
        self.assertIn("document.getElementById('accessFaceIdLogin').checked = user.face_id_login_enabled !== false;", html)
        self.assertIn('Face ID ўчирилган', html)                 # жадвалдаги белги
