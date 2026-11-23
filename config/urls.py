from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseRedirect
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from employees.media_views import ProtectedMediaView


def admin_login_via_main_login(request, extra_context=None):
    """/admin/login/ — through the main login page: password + Face ID + brute-force protection.
    The stock admin form checked only the password and gave a session to the whole site without Face ID."""
    if request.user.is_authenticated:
        # Already logged in: staff goes to the admin, everyone else home (no /login/ <-> /admin/ redirect loop).
        return HttpResponseRedirect('/admin/' if request.user.is_staff else '/')
    return redirect_to_login(request.GET.get('next') or '/admin/', login_url='/login/')


admin.site.login = admin_login_via_main_login


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(urlconf='config.api_schema_urls'), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema', url='/api/schema/'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema', url='/api/schema/'), name='redoc'),
    path('api/v1/', include('employees.api_urls')),
    # Photos are Face ID references: only for logged-in users (see employees/media_views.py).
    re_path(r'^media/(?P<path>.+)$', ProtectedMediaView.as_view(), name='protected-media'),
    path('', include('employees.urls')),
]
