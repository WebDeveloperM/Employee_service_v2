from django.urls import path

from .views import (
    DepartmentReadOnlyDetailApiView,
    DepartmentReadOnlyListApiView,
    EmployeeReadOnlyDetailApiView,
    EmployeeReadOnlyListApiView,
    SectionReadOnlyDetailApiView,
    SectionReadOnlyListApiView,
)


urlpatterns = [
    path('departments/', DepartmentReadOnlyListApiView.as_view()),
    path('departments/<int:pk>/', DepartmentReadOnlyDetailApiView.as_view()),
    path('sections/', SectionReadOnlyListApiView.as_view()),
    path('sections/<int:pk>/', SectionReadOnlyDetailApiView.as_view()),
    path('employees/', EmployeeReadOnlyListApiView.as_view()),
    path('employees/<slug:slug>/', EmployeeReadOnlyDetailApiView.as_view()),
]