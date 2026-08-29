from django.urls import path

from staff_access.test_views import AdminOnlyView, AnyStaffView, ManagerOrAdminView


urlpatterns = [
    path("test-permissions/any-staff/", AnyStaffView.as_view()),
    path("test-permissions/manager-or-admin/", ManagerOrAdminView.as_view()),
    path("test-permissions/admin-only/", AdminOnlyView.as_view()),
]
