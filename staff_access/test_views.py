from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes, HasAnyActiveStaffRole


class AnyStaffView(APIView):
    permission_classes = [IsAuthenticated, HasAnyActiveStaffRole]

    def get(self, request):
        return Response({"detail": "allowed"})


class ManagerOrAdminPermission(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER)


class ManagerOrAdminView(APIView):
    permission_classes = [IsAuthenticated, ManagerOrAdminPermission]

    def get(self, request):
        return Response({"detail": "allowed"})


class AdminOnlyPermission(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN,)


class AdminOnlyView(APIView):
    permission_classes = [IsAuthenticated, AdminOnlyPermission]

    def get(self, request):
        return Response({"detail": "allowed"})
