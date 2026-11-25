from django.urls import path

from .timekeeper_api import (
    TimekeeperAssignmentDetailView,
    TimekeeperAssignmentListView,
    TimekeeperEmployeeDetailView,
    TimekeeperEmployeeListView,
    TimekeeperInfoView,
    TimekeeperReferenceView,
)
from .views import (
    DepartmentReadOnlyDetailApiView,
    DepartmentReadOnlyListApiView,
    EmployeeReadOnlyDetailApiView,
    EmployeeReadOnlyListApiView,
    SectionReadOnlyDetailApiView,
    SectionReadOnlyListApiView,
)


urlpatterns = [
    # Табелчилар (бошқа тизим учун, фақат сервис калити). Ҳужжат: API_TIMEKEEPER.md.
    path('timekeeper-assignments/', TimekeeperAssignmentListView.as_view()),
    path('timekeeper-assignments/<int:department_id>/', TimekeeperAssignmentDetailView.as_view()),
    path('timekeeper/<str:timekeeper_tabel>/', TimekeeperInfoView.as_view()),
    path('timekeeper/<str:timekeeper_tabel>/reference/', TimekeeperReferenceView.as_view()),
    path('timekeeper/<str:timekeeper_tabel>/employees/', TimekeeperEmployeeListView.as_view()),
    path('timekeeper/<str:timekeeper_tabel>/employees/<str:tabel_number>/', TimekeeperEmployeeDetailView.as_view()),
    path('departments/', DepartmentReadOnlyListApiView.as_view()),
    path('departments/<int:pk>/', DepartmentReadOnlyDetailApiView.as_view()),
    path('sections/', SectionReadOnlyListApiView.as_view()),
    path('sections/<int:pk>/', SectionReadOnlyDetailApiView.as_view()),
    path('employees/', EmployeeReadOnlyListApiView.as_view()),
    path('employees/<slug:slug>/', EmployeeReadOnlyDetailApiView.as_view()),
]