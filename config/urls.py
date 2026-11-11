from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(urlconf='config.api_schema_urls'), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema', url='/api/schema/'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema', url='/api/schema/'), name='redoc'),
    path('api/v1/', include('employees.api_urls')),
    path('', include('employees.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)