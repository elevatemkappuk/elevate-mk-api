from django.conf import settings
from django.http import Http404
from rest_framework.permissions import BasePermission

from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.authentication import SessionAuthentication


class ApiDocumentationAccessPermission(BasePermission):
    """Environment-aware access for every documentation representation."""

    message = "Technical API documentation access is not available."

    def has_permission(self, request, view):
        if settings.ELEVATE_ENVIRONMENT == "local":
            return True
        if (
            settings.ELEVATE_ENVIRONMENT == "production"
            and not settings.API_DOCUMENTATION_PRODUCTION_ENABLED
        ):
            raise Http404
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        return bool(
            user.is_superuser
            or user.has_perm(settings.API_DOCUMENTATION_PERMISSION)
        )


class ProtectedDocumentationMixin:
    authentication_classes = (SessionAuthentication,)
    permission_classes = (ApiDocumentationAccessPermission,)


class ProtectedSpectacularAPIView(ProtectedDocumentationMixin, SpectacularAPIView):
    pass


class ProtectedSpectacularSwaggerView(ProtectedDocumentationMixin, SpectacularSwaggerView):
    pass


class ProtectedSpectacularRedocView(ProtectedDocumentationMixin, SpectacularRedocView):
    pass
