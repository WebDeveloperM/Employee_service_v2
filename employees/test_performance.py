"""Тезлик тузатишлари: ҳар бири учун тест (қайта секинлашмасин).

- рўйхатларда асл фото эмас, кичик превью (thumbs/256v2/...), асл фото манзили (TB, Face ID) ўзгармайди;
- nginx орқали (MEDIA_X_ACCEL_REDIRECT) файлни gunicorn эмас, nginx юборади;
- Face ID эталони ҳар бир кадр учун эмас, бир марта (ва файл ўзгармагунча кешдан) ҳисобланади;
- рўйхат API лари саҳифа ҳажмига боғлиқ бўлмаган сондаги SQL сўров билан ишлайди (N+1 йўқ);
- CSS/шрифт/иконкалар CDN дан эмас, ўз серверимиздан, ?v=<хэш> билан.
"""
import os
import re
import shutil
import stat
import tempfile
import time
import unittest
from io import BytesIO
from unittest.mock import patch

import numpy as np
from PIL import Image
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import connection
from django.template import Context, Template
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import face
from .models import Department, Employee, ROLE_ADMIN, ROLE_TIMEKEEPER, ROLE_USER, Timekeeper, UserProfile
from .thumbnails import THUMB_DIR, THUMB_SIZE, ensure_thumbnail, original_name_for_thumbnail, thumbnail_name

TEMPLATES_DIR = settings.BASE_DIR / 'employees' / 'templates'


def jpeg_bytes(size=(1200, 1600), color=(180, 120, 90), exif_orientation=None):
    buffer = BytesIO()
    image = Image.new('RGB', size, color=color)
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        image.save(buffer, format='JPEG', exif=exif)
    else:
        image.save(buffer, format='JPEG')
    return buffer.getvalue()


class TempMediaMixin:
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix='es-media-')
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def make_admin(self):
        admin = User.objects.create_user(username='perf_admin_t', password='x')
        UserProfile.objects.create(user=admin, role=ROLE_ADMIN)
        return admin

    def make_employee(self, tabel, *, photo=True, department=None, **extra):
        employee = Employee.objects.create(first_name='Ism', last_name=f'Fam{tabel}', tabel_number=str(tabel),
                                           department=department, **extra)
        if photo:
            employee.base_image.save(f'emp{tabel}.jpg', ContentFile(jpeg_bytes()), save=True)
        return employee


class ThumbnailTests(TempMediaMixin, TestCase):
    def thumb_path(self, employee):
        return '/' + settings.MEDIA_URL.strip('/') + '/' + thumbnail_name(employee.base_image.name)

    def test_name_round_trip(self):
        self.assertEqual(original_name_for_thumbnail(thumbnail_name('employee_base_images/a b.jpg')), 'employee_base_images/a b.jpg')
        self.assertIsNone(original_name_for_thumbnail('employee_base_images/a.jpg'))
        self.assertIsNone(original_name_for_thumbnail(f'{THUMB_DIR}/a.png'))

    def test_old_thumbnail_urls_are_not_reused(self):
        """Биринчи версиядаги превьюлар (thumbs/256/...) 0600 эди ва браузер улар учун nginx 403 ини бир соат кешлаган.
        Янги манзил браузерни уларни қайта сўрашга мажбур қилади."""
        self.assertTrue(thumbnail_name('employee_base_images/a.jpg').startswith(f'thumbs/{THUMB_SIZE}v'))
        self.assertIsNone(original_name_for_thumbnail(f'thumbs/{THUMB_SIZE}/employee_base_images/a.jpg.jpg'))

    def test_list_api_gives_a_thumbnail_and_keeps_the_original_url(self):
        admin = self.make_admin()
        employee = self.make_employee(5001)
        client = Client()
        client.force_login(admin)
        row = client.get('/api/template/employees/').json()['data'][0]
        self.assertTrue(row['base_image_url'].endswith('/media/' + employee.base_image.name))   # TB / Face ID: as before
        self.assertTrue(row['base_image_thumb_url'].endswith('/media/' + thumbnail_name(employee.base_image.name)))

    def test_thumbnail_is_small_and_cached_on_disk(self):
        employee = self.make_employee(5002)
        response = Client().get(self.thumb_path(employee))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        body = b''.join(response.streaming_content)
        with Image.open(BytesIO(body)) as image:
            self.assertLessEqual(max(image.size), THUMB_SIZE)
            self.assertEqual(image.size, (192, 256))   # 1200×1600 -> пропорция сақланади
        stored = os.path.join(self.media_root, thumbnail_name(employee.base_image.name))
        self.assertTrue(os.path.isfile(stored))
        self.assertLess(len(body), 30 * 1024)

    def test_exif_rotation_is_applied_like_the_browser_does(self):
        employee = self.make_employee(5003, photo=False)
        employee.base_image.save('rotated.jpg', ContentFile(jpeg_bytes(size=(1600, 1200), exif_orientation=6)), save=True)
        path = ensure_thumbnail(employee.base_image.name)
        with Image.open(path) as image:
            self.assertEqual(image.size, (192, 256))   # 90° га бурилган: портрет

    def test_thumbnail_is_rebuilt_when_the_original_changes_and_gone_when_it_is_deleted(self):
        employee = self.make_employee(5004)
        first = ensure_thumbnail(employee.base_image.name)
        with Image.open(first) as image:
            old_pixel = image.getpixel((5, 5))
        original = os.path.join(self.media_root, employee.base_image.name)
        with open(original, 'wb') as handle:
            handle.write(jpeg_bytes(color=(10, 200, 10)))
        future = time.time() + 5
        os.utime(original, (future, future))
        with Image.open(ensure_thumbnail(employee.base_image.name)) as image:
            self.assertNotEqual(image.getpixel((5, 5)), old_pixel)
        os.remove(original)
        self.assertIsNone(ensure_thumbnail(employee.base_image.name))
        self.assertEqual(Client().get(self.thumb_path(employee)).status_code, 404)

    @unittest.skipUnless(os.name == 'posix', 'файл рухсатлари фақат Linux/Unix да маъноли (Windows да ҳамма файл 666)')
    def test_thumbnail_can_be_read_by_nginx_which_runs_as_another_user(self):
        """tempfile.mkstemp файлни 0600 қилади: nginx (бошқа фойдаланувчи) X-Accel орқали уни ўқий олмай 403 берарди ва
        рўйхатдаги барча расмлар «синган» кўринарди (Docker Desktop/Windows да ҳамма файл 777, шунинг учун у ерда сезилмайди)."""
        employee = self.make_employee(5008)
        path = ensure_thumbnail(employee.base_image.name)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode) & 0o044, 0o044)   # гуруҳ ва бошқалар ўқий олади

    @unittest.skipUnless(os.name == 'posix', 'файл рухсатлари фақат Linux/Unix да маъноли')
    def test_a_thumbnail_made_earlier_with_private_permissions_is_healed_on_first_use(self):
        employee = self.make_employee(5009)
        path = ensure_thumbnail(employee.base_image.name)
        os.chmod(path, 0o600)   # сервердаги ҳозирги ҳолат
        self.assertEqual(ensure_thumbnail(employee.base_image.name), path)
        self.assertTrue(os.stat(path).st_mode & stat.S_IROTH)

    def test_not_an_image_falls_back_to_the_original_file(self):
        Employee.objects.create(first_name='A', last_name='B', tabel_number='5005')
        os.makedirs(os.path.join(self.media_root, 'employee_base_images'), exist_ok=True)
        with open(os.path.join(self.media_root, 'employee_base_images', 'broken.jpg'), 'wb') as handle:
            handle.write(b'not an image')
        response = Client().get('/media/' + thumbnail_name('employee_base_images/broken.jpg'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'not an image')

    def test_traversal_through_the_thumbnail_path_is_refused(self):
        for path in (f'/media/{THUMB_DIR}/../../config/settings.py.jpg', f'/media/{THUMB_DIR}/%2e%2e/x.jpg.jpg'):
            with self.subTest(path=path):
                self.assertEqual(Client().get(path).status_code, 404)

    @override_settings(MEDIA_REQUIRE_AUTH=True)
    def test_thumbnail_has_the_same_access_rules_as_the_original(self):
        owner = User.objects.create_user(username='thumb_owner', password='x')
        UserProfile.objects.create(user=owner, role=ROLE_USER)
        employee = self.make_employee(5006, user=owner)
        other = self.make_employee(5007)
        self.assertEqual(Client().get(self.thumb_path(employee)).status_code, 404)
        client = Client()
        client.force_login(owner)
        self.assertEqual(client.get(self.thumb_path(employee)).status_code, 200)
        self.assertEqual(client.get(self.thumb_path(other)).status_code, 404)


@override_settings(MEDIA_X_ACCEL_REDIRECT=True)
class XAccelRedirectTests(TempMediaMixin, TestCase):
    def test_django_only_checks_access_and_nginx_sends_the_file(self):
        employee = self.make_employee(5101)
        response = Client().get('/media/' + employee.base_image.name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Accel-Redirect'], '/protected-media/' + employee.base_image.name)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        # Кеш сарлавҳасини nginx фақат муваффақиятли жавобга қўяди: Django қўйса, nginx уни 403/404 ида ҳам сақларди.
        self.assertNotIn('Cache-Control', response)
        self.assertEqual(response.content, b'')

    def test_thumbnail_is_built_then_handed_to_nginx(self):
        employee = self.make_employee(5102)
        name = thumbnail_name(employee.base_image.name)
        response = Client().get('/media/' + name)
        self.assertEqual(response['X-Accel-Redirect'], '/protected-media/' + name)
        self.assertTrue(os.path.isfile(os.path.join(self.media_root, name)))

    def test_non_ascii_names_are_url_encoded_and_non_images_stay_attachments(self):
        os.makedirs(os.path.join(self.media_root, 'docs'), exist_ok=True)
        with open(os.path.join(self.media_root, 'docs', 'Ҳужжат 1.html'), 'w', encoding='utf-8') as handle:
            handle.write('<script>alert(1)</script>')
        response = Client().get('/media/docs/Ҳужжат 1.html')
        self.assertEqual(response['X-Accel-Redirect'], '/protected-media/docs/%D2%B2%D1%83%D0%B6%D0%B6%D0%B0%D1%82%201.html')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['Content-Disposition'], "attachment; filename*=utf-8''%D2%B2%D1%83%D0%B6%D0%B6%D0%B0%D1%82%201.html")

    def test_off_by_default_so_runserver_and_the_tb_embedded_copy_keep_working(self):
        from config import settings as project_settings
        self.assertFalse(project_settings.MEDIA_X_ACCEL_REDIRECT)


class FaceReferenceTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        face._REFERENCE_CACHE.clear()

    def test_reference_is_prepared_once_for_all_frames(self):
        reference = Image.new('RGB', (64, 64), 'white')
        prepared = face.ReferenceFace(np.ones(128, dtype=np.float32), np.full((160, 160), 100, dtype=np.uint8))
        captured = [Image.new('RGB', (64, 64), 'white') for _ in range(3)]
        with patch.object(face, 'prepare_reference_face', return_value=prepared) as prepare, \
                patch.object(face, '_extract_face_embedding', return_value=(np.ones(128, dtype=np.float32), None)), \
                patch.object(face, 'extract_primary_face', return_value=np.full((160, 160), 100, dtype=np.uint8)):
            ref = face.prepare_reference_face(reference)
            scores = [face.calculate_face_similarity(ref, image) for image in captured]
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(len(set(scores)), 1)

    def test_prepared_reference_gives_exactly_the_old_score(self):
        rng = np.random.default_rng(3)
        reference = Image.fromarray(rng.integers(0, 255, (200, 160, 3), dtype=np.uint8))
        captured = Image.fromarray(rng.integers(0, 255, (200, 160, 3), dtype=np.uint8))
        ref_face = rng.integers(0, 255, (160, 160), dtype=np.uint8)
        cap_face = rng.integers(0, 255, (160, 160), dtype=np.uint8)
        ref_emb, cap_emb = rng.random(128).astype(np.float32), rng.random(128).astype(np.float32)

        def fake_embedding(image):
            return (ref_emb.copy() if image is reference else cap_emb.copy()), None

        def fake_face(image):
            return (ref_face.copy() if image is reference else cap_face.copy())

        with patch.object(face, '_extract_face_embedding', side_effect=fake_embedding), \
                patch.object(face, 'extract_primary_face', side_effect=fake_face):
            legacy = face.calculate_face_similarity(reference, captured)                    # PIL эталон: илгаригидек
            prepared = face.prepare_reference_face(reference)
            new = [face.calculate_face_similarity(prepared, captured) for _ in range(3)]
        self.assertEqual(new, [legacy] * 3)
        self.assertFalse(prepared.face.flags.writeable)   # кешдаги эталон ўзгармайди

    def test_reference_from_file_is_cached_until_the_file_changes(self):
        employee = self.make_employee(5201)
        prepared = face.ReferenceFace(np.ones(128, dtype=np.float32), None)
        with patch.object(face, 'prepare_reference_face', return_value=prepared) as prepare:
            face.reference_face_from_file(employee.base_image)
            face.reference_face_from_file(employee.base_image)
            self.assertEqual(prepare.call_count, 1)
            path = employee.base_image.path
            future = time.time() + 5
            os.utime(path, (future, future))
            face.reference_face_from_file(employee.base_image)
            self.assertEqual(prepare.call_count, 2)

    def test_reference_without_a_face_is_not_cached(self):
        employee = self.make_employee(5202)
        with patch.object(face, 'prepare_reference_face', return_value=face.ReferenceFace(None, None)) as prepare:
            face.reference_face_from_file(employee.base_image)
            face.reference_face_from_file(employee.base_image)
        self.assertEqual(prepare.call_count, 2)

    def test_unreadable_reference_raises_the_dedicated_error(self):
        employee = self.make_employee(5203, photo=False)
        employee.base_image.save('bad.jpg', ContentFile(b'not an image'), save=True)
        with self.assertRaises(face.UnreadableReferenceImage):
            face.reference_face_from_file(employee.base_image)

    def test_missing_opencv_keeps_the_old_per_frame_failure(self):
        with patch.object(face, '_extract_face_embedding', side_effect=ValueError('no cv2')):
            reference = face.prepare_reference_face(Image.new('RGB', (32, 32)))
        self.assertEqual(reference, face.ReferenceFace(None, None))


class QueryCountTests(TempMediaMixin, TestCase):
    """SQL сўровлар сони саҳифадаги қаторлар сонига боғлиқ эмас (N+1 йўқ)."""

    def count(self, client, url):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(client.get(url).status_code, 200)
        return len(ctx.captured_queries)

    def test_users_list(self):
        admin = self.make_admin()
        client = Client()
        client.force_login(admin)
        for index in range(3):
            user = User.objects.create_user(username=f'qc_user{index}', password='x')
            UserProfile.objects.create(user=user, role=ROLE_USER)
            self.make_employee(5300 + index, user=user)
        few = self.count(client, '/api/template/users/?page_size=100')
        for index in range(3, 15):
            user = User.objects.create_user(username=f'qc_user{index}', password='x')
            UserProfile.objects.create(user=user, role=ROLE_USER)
            self.make_employee(5300 + index, user=user)
        self.assertEqual(self.count(client, '/api/template/users/?page_size=100'), few)

    def test_timekeeper_department_list(self):
        department = Department.objects.create(name='9-Цех')
        tk_user = User.objects.create_user(username='qc_tk', password='x')
        UserProfile.objects.create(user=tk_user, role=ROLE_TIMEKEEPER)
        tk_employee = self.make_employee(5400, department=department, user=tk_user, photo=False)
        Timekeeper.objects.create(department=department, employee=tk_employee)
        client = Client()
        client.force_login(tk_user)

        def add_staff(start, stop):
            for index in range(start, stop):
                user = User.objects.create_user(username=f'qc_staff{index}', password='x')
                UserProfile.objects.create(user=user, role=ROLE_USER)
                self.make_employee(5401 + index, department=department, user=user, photo=False)

        add_staff(0, 2)
        few = self.count(client, '/api/template/employees/?page_size=100')
        add_staff(2, 14)
        self.assertEqual(self.count(client, '/api/template/employees/?page_size=100'), few)


class StaticAssetsTests(TestCase):
    def test_pages_load_nothing_from_external_cdns(self):
        for name in ('base.html', 'login.html'):
            html = (TEMPLATES_DIR / name).read_text(encoding='utf-8')
            with self.subTest(template=name):
                self.assertIsNone(re.search(r'(src|href)="https?://', html))   # ҳеч қандай ташқи скрипт/стиль/шрифт
                self.assertIn("{% asset 'vendor/fontawesome/css/all.min.css' %}", html)

    def test_built_css_and_vendor_files_exist(self):
        static = settings.BASE_DIR / 'employees' / 'static'
        for path in ('app/app.css', 'app/login.css', 'vendor/fonts/fonts.css', 'vendor/fontawesome/css/all.min.css',
                     'vendor/fontawesome/webfonts/fa-solid-900.woff2', 'vendor/imask/imask.min.js', 'vendor/xlsx/xlsx.full.min.js'):
            with self.subTest(path=path):
                self.assertTrue((static / path).is_file())
        css = (static / 'app' / 'app.css').read_text(encoding='utf-8')
        self.assertIn('--tw-', css)
        self.assertIn('rgb(65 84 241', css)   # #4154f1 — сайтнинг blue-600 палитраси

    def test_heavy_libraries_load_only_where_they_are_used(self):
        base = (TEMPLATES_DIR / 'base.html').read_text(encoding='utf-8')
        self.assertNotIn('xlsx.full.min.js', base)
        self.assertNotIn('imask.min.js', base)
        employees = (TEMPLATES_DIR / 'employees_list.html').read_text(encoding='utf-8')
        self.assertIn('function loadXlsx()', employees)
        self.assertIn('await loadXlsx();', employees)
        for name in ('employee_create.html', 'employee_detail.html'):
            self.assertIn("vendor/imask/imask.min.js' %}\" defer", (TEMPLATES_DIR / name).read_text(encoding='utf-8'))

    def test_asset_url_carries_a_content_hash(self):
        html = Template("{% load assets %}{% asset 'app/app.css' %}").render(Context())
        self.assertRegex(html, r'^/static/app/app\.css\?v=[0-9a-f]{12}$')
        self.assertEqual(Template("{% load assets %}{% asset 'app/missing.css' %}").render(Context()), '/static/app/missing.css')

    def test_bumping_the_asset_version_changes_every_url(self):
        from .templatetags import assets
        render = lambda: Template("{% load assets %}{% asset 'app/app.css' %}").render(Context())
        before = render()
        with patch.object(assets, 'ASSET_VERSION', 'other'):
            self.assertNotEqual(render(), before)
        self.assertEqual(render(), before)

    def test_list_pages_show_thumbnails_not_full_photos(self):
        employees = (TEMPLATES_DIR / 'employees_list.html').read_text(encoding='utf-8')
        self.assertIn('employee.base_image_thumb_url || fullPhoto', employees)
        self.assertIn('openImageModal(this.dataset.full, this.alt)', employees)
        self.assertIsNone(re.search(r'\w\.innerHTML \+=', employees))   # квадратик «+=» йўқ (изоҳдан ташқари)


class InfrastructureSettingsTests(TestCase):
    def test_database_connections_are_reused(self):
        from config import settings as project_settings
        self.assertGreater(project_settings.DATABASES['default']['CONN_MAX_AGE'], 0)
        self.assertTrue(project_settings.DATABASES['default']['CONN_HEALTH_CHECKS'])

    def test_gunicorn_runs_several_workers_with_a_shared_cache(self):
        gunicorn_conf = (settings.BASE_DIR / 'config' / 'gunicorn.conf.py').read_text(encoding='utf-8')
        self.assertIn("workers = _int('GUNICORN_WORKERS'", gunicorn_conf)
        compose = (settings.BASE_DIR / 'docker-compose.yml').read_text(encoding='utf-8')
        self.assertIn('gunicorn -c config/gunicorn.conf.py', compose)
        self.assertIn('MEDIA_X_ACCEL_REDIRECT: "true"', compose)
        # Под manage.py test кэш — Dummy; в работе — файловый, общий для всех worker-процессов контейнера.
        source = (settings.BASE_DIR / 'config' / 'settings.py').read_text(encoding='utf-8')
        self.assertIn("'django.core.cache.backends.filebased.FileBasedCache'", source)
        self.assertNotIn("else 'django.core.cache.backends.locmem.LocMemCache'", source)

    def test_nginx_serves_static_and_media_and_compresses(self):
        for name in ('nginx.standalone.conf', 'nginx.conf'):
            conf = (settings.BASE_DIR / name).read_text(encoding='utf-8')
            with self.subTest(conf=name):
                self.assertIn('location /protected-media/ {\n        internal;', conf)
                self.assertIn('location /static/ {', conf)
                self.assertIn('gzip on;', conf)
                self.assertIn('http2 on;', conf)
                self.assertIn('ssl_session_cache shared:SSL:10m;', conf)

    def test_a_missing_static_file_falls_back_to_django_and_errors_are_never_cached(self):
        """nginx.standalone.conf: «Cache-Control ... always» 404 га ҳам 1 йиллик «immutable» ёпиштирарди — браузер бир марта
        олинган 404 ни (?v= ўзгармагунча) йил давомида сақлаб, CSS оддий F5 билан ҳам тикланмасди."""
        for name in ('nginx.standalone.conf', 'nginx.conf'):
            conf = (settings.BASE_DIR / name).read_text(encoding='utf-8')
            with self.subTest(conf=name):
                static_block = conf[conf.index('location /static/ {'):conf.index('location /protected-media/ {')]
                self.assertIn('add_header Cache-Control $static_cache_control;', static_block)   # «always» йўқ
                self.assertNotIn('Cache-Control $static_cache_control always', conf)
                self.assertIn('error_page 403 404 = @django;', static_block)   # nginx кўрмаса — Django (whitenoise) беради
                media_block = conf[conf.index('location /protected-media/ {'):conf.index('location @django {')]
                self.assertIn('add_header Cache-Control "private, max-age=3600";', media_block)   # «always» йўқ: хатога эмас
                # @django ва «location /» бир хил прокси созламаларига эга (бири ўзгарса, иккинчиси ҳам).
                named = re.search(r'location @django \{(.*?)\n    \}', conf, re.S).group(1)
                main = re.search(r'location / \{(.*?)\n    \}', conf, re.S).group(1)
                self.assertEqual(named.strip(), main.strip())
