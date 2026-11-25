from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.db.models import Q
from rest_framework import generics, serializers, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import CRM_SESSION_AUTHENTICATION_CLASSES
from audit.models import AuditEvent, LegalHold, LegalHoldReleaseRequest
from audit.policies import (
    filter_person_audit_visibility_for_user,
    get_person_audit_description,
    get_privacy_audit_queryset_for_user,
    record_privacy_authorization_failure,
)
from audit.privacy import approve_legal_hold_release, create_legal_hold, reject_legal_hold_release, request_legal_hold_release
from people.models import Person
from staff_access.models import CapabilityDefinition, StaffRole
from staff_access.permissions import HasCrmOperationalAccess, user_has_any_active_staff_role, user_has_capability

EVENT_REFERENCE_SALT = "privacy-audit-event-reference"


def _event_reference(event_id):
    return signing.dumps(int(event_id), salt=EVENT_REFERENCE_SALT, compress=True)


def _resolve_event_reference(value):
    try:
        event_id = signing.loads(str(value), salt=EVENT_REFERENCE_SALT, max_age=60 * 60 * 24 * 365)
    except signing.BadSignature:
        return None
    return AuditEvent.objects.filter(pk=event_id, audit_domain=AuditEvent.Domain.PRIVACY).first()


class PrivacyHistoryPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class PrivacyHistorySerializer(serializers.Serializer):
    id = serializers.CharField()
    action = serializers.CharField()
    description = serializers.SerializerMethodField()
    occurred_at = serializers.DateTimeField()
    entity_type = serializers.CharField()
    actor = serializers.SerializerMethodField()
    outcome = serializers.SerializerMethodField()
    superuser_operation = serializers.SerializerMethodField()
    capability = serializers.SerializerMethodField()

    def get_description(self, event):
        return get_person_audit_description(event.action)

    def get_actor(self, event):
        actor = event.actor_user
        if actor is None:
            return {"kind": "SYSTEM", "id": None, "display_name": "System", "email": None, "contact_restricted": False}
        viewer = self.context["request"].user
        can_view_contact = user_has_capability(viewer, CapabilityDefinition.PERSON_CONTACT_READ)
        return {
            "kind": "USER", "id": actor.pk, "display_name": f"User {actor.pk}",
            "email": actor.email if can_view_contact else None,
            "contact_restricted": not can_view_contact,
        }

    def get_outcome(self, event):
        return (event.metadata or {}).get("outcome", "SUCCESS")

    def get_superuser_operation(self, event):
        return (event.metadata or {}).get("superuser_authority") is True

    def get_capability(self, event):
        metadata = event.metadata or {}
        return metadata.get("capability") or metadata.get("capability_code")


class LegalHoldSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    scope = serializers.CharField()
    status = serializers.CharField()
    person_id = serializers.IntegerField(allow_null=True)
    audit_event_reference = serializers.SerializerMethodField()
    placed_by_id = serializers.IntegerField()
    placed_at = serializers.DateTimeField()
    released_by_id = serializers.IntegerField(allow_null=True)
    released_at = serializers.DateTimeField(allow_null=True)
    justification = serializers.SerializerMethodField()
    permitted_actions = serializers.SerializerMethodField()
    execution_mode = serializers.SerializerMethodField()

    def get_audit_event_reference(self, hold):
        return _event_reference(hold.audit_event_id) if hold.audit_event_id else None

    def get_justification(self, hold):
        request = self.context.get("request")
        if request and (request.user.is_superuser or user_has_capability(request.user, CapabilityDefinition.LEGAL_HOLD_ADMIN)):
            return hold.reason
        return None

    def get_permitted_actions(self, hold):
        request = self.context.get("request")
        if not request or hold.status != LegalHold.Status.ACTIVE:
            return []
        if request.user.is_superuser or user_has_capability(request.user, CapabilityDefinition.LEGAL_HOLD_ADMIN):
            return ["request_release"]
        return []

    def get_execution_mode(self, hold):
        request = self.context.get("request")
        return "DIRECT_SUPERUSER" if request and request.user.is_superuser else "GOVERNED"


class PrivacyAdministratorPermission(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            record_privacy_authorization_failure(request=request, denial_code="AUTHENTICATION_REQUIRED")
            return False
        user = request.user
        if not user.is_active or not user_has_any_active_staff_role(user, StaffRole.canonical_codes()):
            record_privacy_authorization_failure(request=request, denial_code="CRM_ADMISSION_REQUIRED")
            return False
        return True


class PrivacyAuditHistoryView(generics.ListAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [PrivacyAdministratorPermission, HasCrmOperationalAccess]
    serializer_class = PrivacyHistorySerializer
    pagination_class = PrivacyHistoryPagination

    def get_queryset(self):
        person_id = self.kwargs["person_id"]
        person = Person.objects.filter(pk=person_id).first()
        if person is None or person.record_type == Person.RecordType.TECHNICAL:
            record_privacy_authorization_failure(request=self.request, denial_code="PERSON_SCOPE_DENIED", person_id=person_id)
            return AuditEvent.objects.none()
        if person.record_type == Person.RecordType.STAFF and not user_has_any_active_staff_role(self.request.user, (StaffRole.CRM_ADMIN,)):
            record_privacy_authorization_failure(request=self.request, denial_code="STAFF_PERSON_SCOPE_DENIED", person_id=person_id)
            return AuditEvent.objects.none()
        if not user_has_capability(self.request.user, CapabilityDefinition.PRIVACY_AUDIT_READ, request=self.request):
            record_privacy_authorization_failure(request=self.request, denial_code="PRIVACY_AUDIT_READ_REQUIRED", person_id=person_id)
            return AuditEvent.objects.none()
        queryset = filter_person_audit_visibility_for_user(
            get_privacy_audit_queryset_for_user(self.request.user, person=person), self.request.user
        )
        params = self.request.query_params
        if params.get("occurred_from"):
            queryset = queryset.filter(occurred_at__gte=params["occurred_from"])
        if params.get("occurred_to"):
            queryset = queryset.filter(occurred_at__lte=params["occurred_to"])
        if params.get("activity_type"):
            queryset = queryset.filter(action=params["activity_type"])
        if params.get("outcome"):
            queryset = queryset.filter(metadata__outcome=params["outcome"])
        if params.get("superuser_operation") in {"true", "false"}:
            queryset = queryset.filter(metadata__superuser_authority=params["superuser_operation"] == "true")
        return queryset


class LegalHoldPermission(PrivacyAdministratorPermission):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.is_superuser:
            return True
        allowed = user_has_capability(request.user, CapabilityDefinition.LEGAL_HOLD_ADMIN, request=request)
        if not allowed:
            record_privacy_authorization_failure(request=request, denial_code="LEGAL_HOLD_ADMIN_REQUIRED")
        return allowed


class LegalHoldListCreateView(generics.ListCreateAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [LegalHoldPermission, HasCrmOperationalAccess]
    serializer_class = LegalHoldSerializer

    def get_queryset(self):
        queryset = LegalHold.objects.select_related("person", "audit_event", "placed_by", "released_by")
        person_id = self.request.query_params.get("person_id")
        if person_id:
            queryset = queryset.filter(Q(person_id=person_id) | Q(audit_event__subjects__person_id=person_id)).distinct()
        return queryset

    def create(self, request, *args, **kwargs):
        person_id = request.data.get("person_id")
        event_reference = request.data.get("audit_event_reference")
        if bool(person_id) == bool(event_reference):
            return Response({"detail": "Exactly one hold target is required."}, status=status.HTTP_400_BAD_REQUEST)
        person = None
        event = None
        if person_id:
            person = Person.objects.filter(pk=person_id).exclude(record_type=Person.RecordType.TECHNICAL).first()
        else:
            event = _resolve_event_reference(event_reference)
        if person is None and event is None:
            record_privacy_authorization_failure(request=request, denial_code="TARGET_SCOPE_DENIED")
            return Response({"detail": "Target not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            hold = create_legal_hold(actor_user=request.user, reason=request.data.get("reason"), person=person, audit_event=event, request=request)
        except (PermissionDenied, ValidationError) as error:
            record_privacy_authorization_failure(request=request, denial_code="LEGAL_HOLD_POLICY_DENIED")
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(hold).data, status=status.HTTP_201_CREATED)


class LegalHoldDetailView(generics.RetrieveAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [LegalHoldPermission, HasCrmOperationalAccess]
    serializer_class = LegalHoldSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    queryset = LegalHold.objects.select_related("person", "audit_event", "placed_by", "released_by")


class LegalHoldReleaseRequestView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [LegalHoldPermission, HasCrmOperationalAccess]

    def post(self, request, public_id):
        hold = LegalHold.objects.filter(public_id=public_id).first()
        if hold is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            release_request = request_legal_hold_release(actor_user=request.user, hold=hold, reason=request.data.get("reason"))
        except (PermissionDenied, ValidationError) as error:
            record_privacy_authorization_failure(request=request, denial_code="LEGAL_HOLD_RELEASE_DENIED")
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"public_id": str(release_request.public_id), "status": release_request.status}, status=status.HTTP_201_CREATED)


class LegalHoldReleaseApprovalView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [LegalHoldPermission, HasCrmOperationalAccess]

    def post(self, request, public_id):
        release_request = LegalHoldReleaseRequest.objects.select_related("legal_hold").filter(public_id=public_id).first()
        if release_request is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            hold = approve_legal_hold_release(actor_user=request.user, release_request=release_request, reason=request.data.get("reason"))
        except (PermissionDenied, ValidationError) as error:
            record_privacy_authorization_failure(request=request, denial_code="LEGAL_HOLD_RELEASE_APPROVAL_DENIED")
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(LegalHoldSerializer(hold, context={"request": request}).data, status=status.HTTP_200_OK)


class LegalHoldReleaseRejectionView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [LegalHoldPermission, HasCrmOperationalAccess]

    def post(self, request, public_id):
        release_request = LegalHoldReleaseRequest.objects.select_related("legal_hold").filter(public_id=public_id).first()
        if release_request is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            rejected = reject_legal_hold_release(actor_user=request.user, release_request=release_request, reason=request.data.get("reason"))
        except (PermissionDenied, ValidationError) as error:
            record_privacy_authorization_failure(request=request, denial_code="LEGAL_HOLD_RELEASE_REJECTION_DENIED")
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "public_id": str(rejected.public_id),
            "status": rejected.status,
            "permitted_actions": ["request_release"],
            "execution_mode": "DIRECT_SUPERUSER" if request.user.is_superuser else "GOVERNED",
        }, status=status.HTTP_200_OK)


class LegalHoldDirectReleaseView(LegalHoldReleaseApprovalView):
    """Explicit transport for the audited Django-superuser release exception."""

    def post(self, request, public_id):
        if not request.user.is_superuser:
            record_privacy_authorization_failure(request=request, denial_code="SUPERUSER_REQUIRED", operation="LEGAL_HOLD_DIRECT_RELEASE")
            return Response({"detail": "An active Django superuser is required."}, status=status.HTTP_403_FORBIDDEN)
        return super().post(request, public_id)
