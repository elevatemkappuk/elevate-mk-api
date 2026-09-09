from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from .queries import dashboard_projection
from .serializers import DashboardSerializer


class HasDashboardAccess(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER)


class DashboardView(APIView):
    permission_classes = [IsAuthenticated, HasDashboardAccess]

    @extend_schema(
        operation_id="dashboard_retrieve", summary="Staff CRM dashboard projection",
        description="Active BUSINESS People overview and profile; six calendar months of active BUSINESS Person creation and all BUSINESS Membership join dates. Includes archived BUSINESS People and review-required import counts. No Events metrics.",
        responses={200: DashboardSerializer, 401: OpenApiResponse(description="Authentication required."), 403: OpenApiResponse(description="An active CRM role is required.")},
        tags=["Dashboard"],
    )
    def get(self, request):
        return Response(DashboardSerializer(dashboard_projection()).data)
