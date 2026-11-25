"""Рўйхатлар учун кичик фото (превью).

Рўйхатда ходим фотоси 64×80 px кўринади, лекин аслида телефон камерасидан тушган асл файл (бир неча МБ) юкланарди:
100 қаторли саҳифа ўнлаб-юзлаб МБ тортар ва gunicorn оқимларини фото узатиш билан банд қилиб, бутун сайтни «қотирарди».

Превью асл файлдан биринчи сўровда ясалади ва MEDIA_ROOT/thumbs/<ўлчам>/<асл ном>.jpg да сақланади. Асл файл
алмашса (mtime янгироқ), превью қайта ясалади; асл файл ўчса, превью ҳам берилмайди. Асл фото (base_image_url,
Face ID эталони, TB интеграцияси) ўзгармайди — превью алоҳида майдон (base_image_thumb_url).
"""
import logging
import os
import tempfile

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils._os import safe_join
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

THUMB_PREFIX = 'thumbs'
# Узун томони. Рўйхатдаги энг катта превью 64×80 CSS px: 2x экранда ҳам аниқ кўриниши учун 256 px.
THUMB_SIZE = 256
THUMB_QUALITY = 82


def thumbnail_name(original_name):
    return f'{THUMB_PREFIX}/{THUMB_SIZE}/{original_name}.jpg'


def original_name_for_thumbnail(name):
    """«thumbs/256/<асл ном>.jpg» -> «<асл ном>»; превью йўли бўлмаса — None."""
    prefix = f'{THUMB_PREFIX}/{THUMB_SIZE}/'
    if not name.startswith(prefix) or not name.endswith('.jpg'):
        return None
    original = name[len(prefix):-len('.jpg')]
    return original or None


def thumbnail_url(field_file, request=None):
    """Превью манзили (асл файлнинг ўзи эмас). Фото бўлмаса — None."""
    if not field_file:
        return None
    url = default_storage.url(thumbnail_name(field_file.name))
    return request.build_absolute_uri(url) if request is not None else url


def ensure_thumbnail(original_name):
    """Превью файлининг тўлиқ йўли (керак бўлса ясайди). Асл файл йўқ ёки расм эмас — None."""
    media_root = str(settings.MEDIA_ROOT)
    try:
        original_path = safe_join(media_root, original_name)
        thumb_path = safe_join(media_root, thumbnail_name(original_name))
    except Exception:
        return None
    try:
        original_mtime = os.stat(original_path).st_mtime
    except OSError:
        return None   # асл фото ўчирилган: эски превью ҳам берилмайди
    try:
        if os.stat(thumb_path).st_mtime >= original_mtime:
            return thumb_path
    except OSError:
        pass
    try:
        _render_thumbnail(original_path, thumb_path)
    except Exception:
        logger.warning('Could not build a thumbnail for %s', original_name, exc_info=True)
        return None
    return thumb_path


def _render_thumbnail(original_path, thumb_path):
    with Image.open(original_path) as image:
        # JPEG ни декодлашда дарҳол кичрайтириш (DCT scaling): 12 Мп фото тўлиқ ўлчамда очилмайди.
        image.draft('RGB', (THUMB_SIZE, THUMB_SIZE))
        # Телефон фотосидаги EXIF буриш: браузер аслини буриб кўрсатади, превью ҳам шундай бўлсин.
        image = ImageOps.exif_transpose(image)
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        image.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        directory = os.path.dirname(thumb_path)
        os.makedirs(directory, exist_ok=True)
        # Бир вақтда келган икки сўров ярим ёзилган файлни бермаслиги учун: вақтинчалик файл, кейин атомар алмаштириш.
        handle, temp_path = tempfile.mkstemp(dir=directory, suffix='.tmp')
        try:
            with os.fdopen(handle, 'wb') as output:
                image.save(output, 'JPEG', quality=THUMB_QUALITY, optimize=True, progressive=True)
            os.replace(temp_path, thumb_path)
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
