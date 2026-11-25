from django.urls import path

from .timekeeper_api import (
    TimekeeperEmployeeDetailView,
    TimekeeperEmployeeListView,
    TimekeeperLogoutView,
    TimekeeperMeView,
    TimekeeperReferenceView,
    TimekeeperTokenView,
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
    # Табелчи API си (бошқа тизим учун): токен билан, фақат ўз цехи. Ҳужжат: API_TIMEKEEPER.md.
    path('timekeeper/token/', TimekeeperTokenView.as_view()),
    path('timekeeper/logout/', TimekeeperLogoutView.as_view()),
    path('timekeeper/me/', TimekeeperMeView.as_view()),
    path('timekeeper/reference/', TimekeeperReferenceView.as_view()),
    path('timekeeper/employees/', TimekeeperEmployeeListView.as_view()),
    path('timekeeper/employees/<str:tabel_number>/', TimekeeperEmployeeDetailView.as_view()),
    path('departments/', DepartmentReadOnlyListApiView.as_view()),
    path('departments/<int:pk>/', DepartmentReadOnlyDetailApiView.as_view()),
    path('sections/', SectionReadOnlyListApiView.as_view()),
    path('sections/<int:pk>/', SectionReadOnlyDetailApiView.as_view()),
    path('employees/', EmployeeReadOnlyListApiView.as_view()),
    path('employees/<slug:slug>/', EmployeeReadOnlyDetailApiView.as_view()),
]