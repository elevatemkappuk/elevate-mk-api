from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import CRM_SESSION_AUTHENTICATION_CLASSES
from staff_access.models import (
    CAPABILITY_POLICY_STATE_KEY,
    CapabilityPolicyState,
    CapabilityDefinition,
    CapabilityProposal,
    CapabilityProposalDecision,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffEmailChangeRequest,
    StaffInvitation,
    StaffRole,
)
from staff_access.permissions import (
    CRM_EFFECTIVE_ACCESS_DISCLOSURE_ALLOWLIST,
    HasActiveStaffRoleCodes,
    HasCrmOperationalAccess,
    get_effective_capability_codes_for_user,
    user_has_any_active_staff_role,
)
from staff_access.serializers import (
    StaffEmailChangeConfirmSerializer,
    StaffEmailChangeRequestSerializer,
    StaffInvitationCreateSerializer,
    StaffInvitationRedeemSerializer,
    StaffInvitationRoleUpdateSerializer,
    StaffLifecycleSerializer,
    EffectiveAccessSerializer,
    CapabilityGovernanceMutationSerializer,
    CapabilityProposalDecisionSerializer,
)
from staff_access.services import (
    cancel_staff_invitation,
    create_staff_invitation,
    redeem_staff_invitation,
    confirm_staff_email_change,
    request_staff_email_change,
    resend_staff_invitation,
    update_pending_staff_invitation_roles,
    offboard_staff_user,
    reactivate_staff_user,
)
from staff_access.governance import (
    apply_capability_proposal,
    apply_individual_capability_change,
    apply_superuser_capability_change,
    approve_capability_proposal,
    cancel_capability_proposal,
    create_capability_proposal,
    reject_capability_proposal,
)
from staff_access.permissions import user_has_capability
from people.models import Person
from accounts.models import User


class StaffAdminPermission(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN,)


class OrdinaryGovernancePermission(IsAuthenticated):
    message = "An active, non-superuser privacy administrator is required."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and not request.user.is_superuser
            and user_has_capability(request.user, CapabilityDefinition.STAFF_CAPABILITY_ADMIN, request=request)
        )


class SuperuserGovernancePermission(IsAuthenticated):
    message = "An active Django superuser with CRM access is required."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.is_active
            and request.user.is_superuser
            and user_has_any_active_staff_role(request.user, StaffRole.canonical_codes())
        )


class GovernanceReadPermission(IsAuthenticated):
    """Allow governed reads to ordinary privacy admins or CRM superusers."""

    message = "An authorized privacy administrator is required."

    def has_permission(self, request, view):
        if not super().has_permission(request, view) or not request.user.is_active:
            return False
        if request.user.is_superuser:
            return user_has_any_active_staff_role(request.user, StaffRole.canonical_codes())
        return user_has_capability(
            request.user,
            CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
            request=request,
        )


def _governance_error(error):
    if isinstance(error, DjangoValidationError):
        return serializers.ValidationError(
            error.message_dict if hasattr(error, "message_dict") else error.messages
        )
    if isinstance(error, PermissionError):
        from rest_framework.exceptions import PermissionDenied

        return PermissionDenied(str(error))
    if isinstance(error, (CapabilityDefinition.DoesNotExist, StaffRole.DoesNotExist, User.DoesNotExist)):
        return serializers.ValidationError("The requested governance target was not found.")
    return serializers.ValidationError(str(error))


def _pending_proposal_conflict(*, target_user=None, role=None, capability=None):
    filters = {"status": CapabilityProposal.Status.PENDING, "capability": capability}
    filters["target_user" if target_user is not None else "role"] = target_user or role
    proposal = CapabilityProposal.objects.filter(**filters).order_by("pk").first()
    payload = {
        "code": "PENDING_PROPOSAL_EXISTS",
        "detail": "A pending governance proposal already exists for this target and capability.",
    }
    if proposal is not None:
        payload["proposal_id"] = str(proposal.public_id)
    return Response(payload, status=status.HTTP_409_CONFLICT)


def _proposal_payload(proposal, *, actor=None):
    decision = getattr(proposal, "decision", None)
    permitted_actions = []
    if proposal.status == CapabilityProposal.Status.PENDING and actor is not None:
        if not actor.is_superuser:
            permitted_actions = ["cancel"] if proposal.proposed_by_id == actor.pk else ["approve", "reject"]
    return {
        "public_id": str(proposal.public_id),
        "operation": proposal.operation,
        "status": proposal.status,
        "role_id": proposal.role_id,
        "role_code": proposal.role.code if proposal.role_id else None,
        "target_user_id": proposal.target_user_id,
        "capability": proposal.capability.code,
        "proposed_by_id": proposal.proposed_by_id,
        "reason": proposal.reason,
        "policy_version": proposal.policy_version,
        "expected_target_state": proposal.expected_target_state,
        "expires_at": proposal.expires_at,
        "created_at": proposal.created_at,
        "resolved_at": proposal.resolved_at,
        "resolution_reason": proposal.resolution_reason,
        "decision": None if decision is None else {
            "outcome": decision.outcome,
            "decided_by_id": decision.decided_by_id,
            "reason": decision.reason,
            "policy_version": decision.policy_version,
            "created_at": decision.created_at,
        },
        "permitted_actions": permitted_actions,
    }


def _governance_objects(data):
    capability = CapabilityDefinition.objects.get(code=data["capability"], is_active=True)
    role = None
    target = None
    if data["operation"] in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
        role = StaffRole.objects.get(pk=data["role_id"])
    else:
        target = User.objects.select_related("person").get(pk=data["target_user_id"])
    return capability, role, target


class StaffEffectiveAccessView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, HasCrmOperationalAccess]

    @extend_schema(
        operation_id="staff_effective_access_me",
        summary="Get the current CRM access projection",
        description=(
            "Returns the backend-evaluated CRM access projection for the authenticated Staff CRM session. "
            "Only the approved STAFF_CAPABILITY_ADMIN capability is disclosed to CRM."
        ),
        responses={
            200: EffectiveAccessSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have an active operational CRM role."),
        },
        tags=["Staff Access"],
    )
    def get(self, request):
        with transaction.atomic():
            policy_state = CapabilityPolicyState.objects.select_for_update().get(
                singleton_key=1,
                key=CAPABILITY_POLICY_STATE_KEY,
            )
            capability_codes = get_effective_capability_codes_for_user(
                request.user,
                requested_codes=CRM_EFFECTIVE_ACCESS_DISCLOSURE_ALLOWLIST,
                request=request,
            )
            data = {
                "capability_codes": sorted(capability_codes),
                "policy_version": policy_state.version,
            }

        response = Response(EffectiveAccessSerializer(data).data)
        response["Cache-Control"] = "private, no-store"
        return response


class GovernanceTargetDetailView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [GovernanceReadPermission]

    def get(self, request, user_id):
        try:
            target = User.objects.select_related("person").get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if target.person.record_type != Person.RecordType.STAFF:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        assignments = StaffCapabilityAssignment.objects.filter(
            user=target,
            state__in=StaffCapabilityAssignment.current_states(),
        ).select_related("capability")
        restrictions = StaffCapabilityRestriction.objects.active().filter(user=target).select_related("capability")
        role_assignments = target.staff_role_assignments.select_related("role").order_by("role__code")
        target_is_eligible = (
            (request.user.is_superuser or target.pk != request.user.pk)
            and target.is_active
            and target.person.record_type == Person.RecordType.STAFF
            and any(item.is_active and item.role.is_active and item.role.code in StaffRole.canonical_codes() for item in role_assignments)
        )
        capability_state = {}
        for capability in CapabilityDefinition.objects.filter(is_active=True).order_by("code"):
            assignment = next((item for item in assignments if item.capability_id == capability.pk), None)
            restriction = next((item for item in restrictions if item.capability_id == capability.pk and item.is_active), None)
            actions = []
            if not target_is_eligible:
                capability_state[capability.code] = {
                    "assignment_state": None if assignment is None else assignment.state,
                    "restriction_active": restriction is not None,
                    "permitted_actions": [],
                }
                continue
            if assignment is None and restriction is None:
                actions.append(CapabilityProposal.Operation.INDIVIDUAL_GRANT)
            elif assignment is not None and assignment.state == StaffCapabilityAssignment.State.SUSPENDED and restriction is None:
                actions.append(CapabilityProposal.Operation.INDIVIDUAL_RESTORE)
            if assignment is not None and assignment.state == StaffCapabilityAssignment.State.ACTIVE:
                if capability.code == CapabilityDefinition.STAFF_CAPABILITY_ADMIN:
                    actions.append(CapabilityProposal.Operation.INDIVIDUAL_SUSPEND)
                actions.append(CapabilityProposal.Operation.INDIVIDUAL_REVOKE)
            if restriction is None:
                actions.append(CapabilityProposal.Operation.INDIVIDUAL_RESTRICT)
            else:
                actions.append(CapabilityProposal.Operation.INDIVIDUAL_UNRESTRICT)
            capability_state[capability.code] = {
                "assignment_state": None if assignment is None else assignment.state,
                "restriction_active": restriction is not None,
                "permitted_actions": sorted(set(actions)),
            }

        return Response({
            "target_user_id": target.pk,
            "person_id": target.person_id,
            "is_active": target.is_active,
            "record_type": target.person.record_type,
            "staff_roles": [
                {"id": item.role_id, "code": item.role.code, "is_active": item.is_active and item.role.is_active}
                for item in role_assignments
            ],
            "capabilities": capability_state,
        })


class GovernanceStaffDirectoryView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [GovernanceReadPermission]

    def get(self, request):
        query = str(request.query_params.get("q", "")).strip()
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(50, max(1, int(request.query_params.get("page_size", 25))))
        except (TypeError, ValueError):
            raise serializers.ValidationError("Invalid pagination parameters.")
        users = User.objects.select_related("person").filter(
            person__record_type=Person.RecordType.STAFF,
        ).order_by("person__last_name", "person__first_name", "pk")
        if query:
            users = users.filter(
                Q(person__first_name__icontains=query)
                | Q(person__last_name__icontains=query)
            )
        total = users.count()
        rows = []
        for target in users[(page - 1) * page_size : page * page_size]:
            role_rows = list(target.staff_role_assignments.select_related("role").order_by("role__code"))
            active_roles = [
                row.role.code for row in role_rows
                if row.is_active and row.role.is_active and row.role.code in StaffRole.canonical_codes()
            ]
            rows.append({
                "user_id": target.pk,
                "display_name": f"{target.person.first_name} {target.person.last_name}".strip(),
                "record_type": target.person.record_type,
                "is_active": target.is_active,
                "role_codes": active_roles,
                "eligible": bool(target.is_active and active_roles),
                "can_manage_privacy_permissions": (request.user.is_superuser or target.pk != request.user.pk) and bool(target.is_active and active_roles),
            })
        return Response({"count": total, "page": page, "page_size": page_size, "results": rows})


class GovernanceRoleDefaultsView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [GovernanceReadPermission]

    def get(self, request):
        defaults = {
            (row.role_id, row.capability_id): row
            for row in RoleCapabilityDefault.objects.select_related("role", "capability")
        }
        pending_keys = set(
            CapabilityProposal.objects.filter(
                status=CapabilityProposal.Status.PENDING,
                role__isnull=False,
            ).values_list("role_id", "capability_id")
        )
        roles = StaffRole.objects.filter(
            code__in=StaffRole.canonical_codes(),
            is_active=True,
        ).order_by("code")
        capabilities = CapabilityDefinition.objects.filter(is_active=True).order_by("code")
        rows = []
        for role in roles:
            for capability in capabilities:
                row = defaults.get((role.pk, capability.pk))
                is_enabled = False if row is None else row.is_enabled
                rows.append({
                    "id": None if row is None else row.pk,
                    "role_id": role.pk,
                    "role_code": role.code,
                    "capability": capability.code,
                    "is_enabled": is_enabled,
                    "permitted_actions": [] if (role.pk, capability.pk) in pending_keys and not request.user.is_superuser else [
                        CapabilityProposal.Operation.ROLE_DEFAULT_DISABLE
                        if is_enabled
                        else CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE
                    ],
                })
        return Response({
            "policy_version": CapabilityPolicyState.objects.get(
                singleton_key=1, key=CAPABILITY_POLICY_STATE_KEY
            ).version,
            "role_defaults": rows,
        })


class GovernanceProposalCreateView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [OrdinaryGovernancePermission]

    def post(self, request):
        serializer = CapabilityGovernanceMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            capability, role, target = _governance_objects(serializer.validated_data)
            proposal = create_capability_proposal(
                actor_user=request.user,
                operation=serializer.validated_data["operation"],
                capability=capability,
                reason=serializer.validated_data["reason"],
                role=role,
                target_user=target,
                request=request,
            )
        except IntegrityError:
            return _pending_proposal_conflict(target_user=target, role=role, capability=capability)
        except (DjangoValidationError, PermissionError, CapabilityDefinition.DoesNotExist, StaffRole.DoesNotExist, User.DoesNotExist) as error:
            raise _governance_error(error)
        return Response(_proposal_payload(proposal, actor=request.user), status=status.HTTP_201_CREATED)


class GovernanceIndividualChangeView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [OrdinaryGovernancePermission]

    def post(self, request):
        serializer = CapabilityGovernanceMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["operation"] in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
            raise serializers.ValidationError("Role-default changes must use the proposal workflow.")
        try:
            capability, _role, target = _governance_objects(data)
            proposal = apply_individual_capability_change(
                actor_user=request.user,
                target_user=target,
                capability=capability,
                operation=data["operation"],
                reason=data["reason"],
                request=request,
            )
        except IntegrityError:
            return _pending_proposal_conflict(target_user=target, capability=capability)
        except (DjangoValidationError, PermissionError, CapabilityDefinition.DoesNotExist, User.DoesNotExist) as error:
            raise _governance_error(error)
        return Response(_proposal_payload(proposal, actor=request.user))


class GovernanceProposalActionView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [OrdinaryGovernancePermission]

    def post(self, request, proposal_id, action):
        serializer = CapabilityProposalDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            proposal = CapabilityProposal.objects.get(public_id=proposal_id)
        except CapabilityProposal.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            if action == "approve":
                result = approve_capability_proposal(proposal=proposal, actor_user=request.user, request=request)
            elif action == "reject":
                if not serializer.validated_data.get("reason"):
                    raise serializers.ValidationError({"reason": "A rejection reason is required."})
                result = reject_capability_proposal(proposal=proposal, actor_user=request.user, reason=serializer.validated_data["reason"], request=request)
            elif action == "cancel":
                if not serializer.validated_data.get("reason"):
                    raise serializers.ValidationError({"reason": "A cancellation reason is required."})
                result = cancel_capability_proposal(proposal=proposal, actor_user=request.user, reason=serializer.validated_data["reason"], request=request)
            else:
                return Response({"detail": "Unknown proposal action."}, status=status.HTTP_404_NOT_FOUND)
        except (DjangoValidationError, PermissionError) as error:
            raise _governance_error(error)
        return Response(_proposal_payload(result, actor=request.user))


class GovernanceHistoryView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [GovernanceReadPermission]

    def get(self, request, history_type):
        proposals = CapabilityProposal.objects.select_related("role", "target_user", "capability", "proposed_by").prefetch_related("decision")
        if history_type == "individual":
            proposals = proposals.filter(target_user__isnull=False)
        elif history_type == "role-defaults":
            proposals = proposals.filter(role__isnull=False)
        elif history_type == "decisions":
            proposals = proposals.filter(decision__isnull=False)
        else:
            return Response({"detail": "Unknown history type."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"results": [_proposal_payload(item, actor=request.user) for item in proposals[:200]]})


class GovernanceSuperuserActionView(APIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [SuperuserGovernancePermission]

    def post(self, request):
        serializer = CapabilityGovernanceMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            capability, role, target = _governance_objects(data)
            result = apply_superuser_capability_change(
                actor_user=request.user,
                capability=capability,
                operation=data["operation"],
                reason=data["reason"],
                role=role,
                target_user=target,
                request=request,
            )
        except (DjangoValidationError, PermissionError, CapabilityDefinition.DoesNotExist, StaffRole.DoesNotExist, User.DoesNotExist) as error:
            raise _governance_error(error)
        return Response({
            "status": "APPLIED",
            "operation": data["operation"],
            "capability": capability.code,
            "role_id": None if role is None else role.pk,
            "target_user_id": None if target is None else target.pk,
            "state": result,
        })


def invitation_response(invitation):
    return {
        "public_id": invitation.public_id,
        "intended_email": invitation.intended_email,
        "expires_at": invitation.expires_at,
        "delivery": "queued",
    }


class StaffInvitationCreateView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]
    serializer_class = StaffInvitationCreateSerializer

    def post(self, request):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            invitation = create_staff_invitation(
                actor_user=request.user,
                **input_serializer.validated_data,
            )
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict if hasattr(error, "message_dict") else error.messages)
        return Response(invitation_response(invitation), status=status.HTTP_201_CREATED)


class StaffInvitationResendView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]

    def post(self, request, invitation_id):
        try:
            invitation = StaffInvitation.objects.get(public_id=invitation_id)
            replacement = resend_staff_invitation(invitation=invitation, actor_user=request.user)
        except StaffInvitation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict if hasattr(error, "message_dict") else error.messages)
        return Response(invitation_response(replacement), status=status.HTTP_201_CREATED)


class StaffInvitationRoleUpdateView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]
    serializer_class = StaffInvitationRoleUpdateSerializer

    def post(self, request, invitation_id):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            invitation = StaffInvitation.objects.get(public_id=invitation_id)
            update_pending_staff_invitation_roles(invitation=invitation, actor_user=request.user, **input_serializer.validated_data)
        except StaffInvitation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict if hasattr(error, "message_dict") else error.messages)
        return Response(invitation_response(invitation))


class StaffInvitationCancelView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]

    def post(self, request, invitation_id):
        try:
            invitation = StaffInvitation.objects.get(public_id=invitation_id)
            cancel_staff_invitation(invitation=invitation, actor_user=request.user)
        except StaffInvitation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"detail": "Invitation cancelled."})


class StaffInvitationRedeemView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = StaffInvitationRedeemSerializer

    def post(self, request, invitation_id, token):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            user = redeem_staff_invitation(
                public_id=invitation_id,
                raw_token=token,
                password=input_serializer.validated_data["password"],
            )
        except (StaffInvitation.DoesNotExist, DjangoValidationError):
            raise serializers.ValidationError({"detail": "This invitation is invalid or has expired."})
        return Response({"detail": "Staff account activated.", "user_id": user.pk})


class StaffEmailChangeRequestView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]
    serializer_class = StaffEmailChangeRequestSerializer

    def post(self, request):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            change = request_staff_email_change(
                actor_user=request.user,
                **input_serializer.validated_data,
            )
        except (DjangoValidationError, StaffEmailChangeRequest.DoesNotExist) as error:
            raise serializers.ValidationError(getattr(error, "message_dict", error.messages if hasattr(error, "messages") else str(error)))
        return Response({"request_id": change.pk, "expires_at": change.expires_at, "delivery": "queued"}, status=status.HTTP_201_CREATED)


class StaffEmailChangeConfirmView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = StaffEmailChangeConfirmSerializer

    def post(self, request, request_id):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            user = confirm_staff_email_change(
                request_id=request_id,
                raw_token=input_serializer.validated_data["token"],
            )
        except (StaffEmailChangeRequest.DoesNotExist, DjangoValidationError):
            raise serializers.ValidationError({"detail": "This email change request is invalid or has expired."})
        return Response({"detail": "Staff email changed and credentials revoked.", "user_id": user.pk})


class StaffOffboardView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]
    serializer_class = StaffLifecycleSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            target = User.objects.select_related("person").get(pk=serializer.validated_data["user_id"])
            offboard_staff_user(target_user=target, actor_user=request.user, request=request)
        except User.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict if hasattr(error, "message_dict") else error.messages)
        return Response({"detail": "Staff account offboarded."})


class StaffReactivateView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]
    serializer_class = StaffLifecycleSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            target = User.objects.select_related("person").get(pk=serializer.validated_data["user_id"])
            invitation = reactivate_staff_user(target_user=target, actor_user=request.user)
        except User.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict if hasattr(error, "message_dict") else error.messages)
        return Response(invitation_response(invitation), status=status.HTTP_201_CREATED)
