from django.urls import include, path


urlpatterns = [
    path('api/v1/', include('employees.public_api_urls')),
]