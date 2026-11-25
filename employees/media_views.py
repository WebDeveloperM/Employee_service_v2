"""Медиа-файлы (фото сотрудников = эталон Face ID) только для вошедших.

Раньше /media/ отдавал django.views.static.serve без всякой проверки (и только при DEBUG=True):
эталонное фото любого сотрудника, в том числе администратора, можно было скачать без входа и
показать камере на шаге Face ID. Теперь:
  * администратор, HR и другая система по сервисному ключу — любые файлы;
  * обычный сотрудник — только свои фото (карточка сотрудника и фото профиля);
  * без входа — 404.
Проверка включается только MEDIA_REQUIRE_AUTH=true. По умолчанию доступ открыт, как раньше: проект TB
(ppe_product) показывает фото напрямую по ссылке (<img>), а его media-proxy запрашивает их без сервисного
ключа — с проверкой у TB пропадают фото. Без проверки view всё равно не отдаёт «../» и пути вне MEDIA_ROOT,
а не-картинки отдаёт как вложение (не как страницу).

Превью для списков (thumbs/256/<имя>.jpg, см. employees/thumbnails.py) проверяются по правам исходного фото.
С MEDIA_X_ACCEL_REDIRECT=true (docker-compose: за nginx) Django только проверяет доступ, а файл отдаёт nginx
(X-Accel-Redirect на internal-location): поток gunicorn не занят передачей мегабайтов фото.
"""
import mimetypes
import os
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils._os import safe_join
from django.utils.http import content_disposition_header
from django.views import View

from .permissions import is_admin_or_hr
from .thumbnails import ensure_thumbnail, original_name_for_thumbnail


def has_service_api_key(request):
    keys = set(getattr(settings, 'EMPLOYEE_SERVICE_API_KEYS', []) or [])
    if not keys:
        return False
    key = str(request.META.get('HTTP_X_EMPLOYEE_SERVICE_KEY', '') or '').strip()
    if not key:
        header = str(request.META.get('HTTP_AUTHORIZATION', '') or '')
        if header.startswith('Bearer '):
            key = header[len('Bearer '):].strip()
    return bool(key) and key in keys


def owns_media_file(user, name):
    """Файл — фото самого пользователя (карточка сотрудника или профиль)."""
    employee = getattr(user, 'employee', None)
    if employee is not None and employee.base_image and employee.base_image.name == name:
        return True
    profile = getattr(user, 'profile', None)
    return profile is not None and bool(profile.face_image) and profile.face_image.name == name


def can_read_media(request, name):
    if not getattr(settings, 'MEDIA_REQUIRE_AUTH', True):
        return True
    if has_service_api_key(request):
        return True
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return False
    return is_admin_or_hr(user) or owns_media_file(user, name)


class ProtectedMediaView(View):
    http_method_names = ['get', 'head']

    def get(self, request, path):
        name = path.replace('\\', '/').lstrip('/')
        original_name = original_name_for_thumbnail(name)
        try:
            # «../» и абсолютные пути -> SuspiciousFileOperation
            full_path = safe_join(str(settings.MEDIA_ROOT), original_name or name)
        except Exception:
            raise Http404
        # Права превью — права исходного фото. Нет доступа и нет файла выглядят одинаково:
        # по ответу нельзя узнать, чьи фото существуют.
        if not can_read_media(request, original_name or name) or not os.path.isfile(full_path):
            raise Http404
        if original_name is not None:
            thumb_path = ensure_thumbnail(original_name)
            if thumb_path is not None:
                full_path = thumb_path
            else:
                name = original_name   # не картинка или битый файл: отдаём исходник, как раньше
        return media_file_response(name, full_path)


def media_file_response(name, full_path):
    content_type, _ = mimetypes.guess_type(full_path)
    is_image = bool(content_type) and content_type.startswith('image/') and content_type != 'image/svg+xml'
    if getattr(settings, 'MEDIA_X_ACCEL_REDIRECT', False):
        response = HttpResponse(content_type=content_type if is_image else 'application/octet-stream')
        response['X-Accel-Redirect'] = settings.MEDIA_X_ACCEL_PREFIX + quote(name)
        if not is_image:   # как FileResponse(as_attachment=True): кириллица — через filename*=utf-8''
            response['Content-Disposition'] = content_disposition_header(True, os.path.basename(name))
    else:
        response = FileResponse(
            open(full_path, 'rb'),
            content_type=content_type if is_image else 'application/octet-stream',
            as_attachment=not is_image,   # не-картинку браузер не откроет как страницу (HTML/SVG -> XSS)
        )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, max-age=3600'
    return response
