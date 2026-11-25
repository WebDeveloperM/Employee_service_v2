"""{% asset 'app/app.css' %} -> /static/app/app.css?v=<content hash>.

The hash changes only when the file content changes, so nginx can let browsers cache /static/ for a year
(nginx.standalone.conf) and a new deploy is still picked up at once. Works with DEBUG on (the manifest storage
ignores hashed names under DEBUG, which is why it is not used here).
"""
import hashlib
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# Part of every ?v= value. Bump it to make all browsers refetch every asset: the first deploy of nginx's one-year
# "immutable" caching also attached it to 404 responses, so a browser that once got a 404 for
# /static/app/app.css?v=<hash> kept it for a year under exactly that URL. New value = new URLs = no stale 404.
ASSET_VERSION = '2'

_digests = {}   # path -> ((found path, mtime_ns, size), digest)


def content_digest(path):
    found = finders.find(path)
    if not found:
        return ''
    try:
        stat = os.stat(found)
    except OSError:
        return ''
    signature = (found, stat.st_mtime_ns, stat.st_size, ASSET_VERSION)
    cached = _digests.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    with open(found, 'rb') as handle:
        digest = hashlib.md5(ASSET_VERSION.encode() + handle.read(), usedforsecurity=False).hexdigest()[:12]
    _digests[path] = (signature, digest)
    return digest


@register.simple_tag
def asset(path):
    url = static(path)
    digest = content_digest(path)
    return f'{url}?v={digest}' if digest else url
