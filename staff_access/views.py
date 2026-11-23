from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import CRM_SESSION_AUTHENTICATION_CLASSES
from staff_access.models import StaffEmailChangeRequest, StaffInvitation, StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from staff_access.serializers import (
    StaffEmailChangeConfirmSerializer,
    StaffEmailChangeRequestSerializer,
    StaffInvitationCreateSerializer,
    StaffInvitationRedeemSerializer,
)
from staff_access.services import (
    cancel_staff_invitation,
    create_staff_invitation,
    redeem_staff_invitation,
    confirm_staff_email_change,
    request_staff_email_change,
)


class StaffAdminPermission(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN,)


def invitation_response(invitation, raw_token):
    return {
        "public_id": invitation.public_id,
        "intended_email": invitation.intended_email,
        "expires_at": invitation.expires_at,
        # The CRM admin receives the one-time secret so it can be handed to
        # the recipient through the configured verified delivery channel.
        "activation_token": raw_token,
    }


class StaffInvitationCreateView(generics.GenericAPIView):
    authentication_classes = CRM_SESSION_AUTHENTICATION_CLASSES
    permission_classes = [IsAuthenticated, StaffAdminPermission]
    serializer_class = StaffInvitationCreateSerializer

    def post(self, request):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            invitation, raw_token = create_staff_invitation(
                actor_user=request.user,
                **input_serializer.validated_data,
            )
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict if hasattr(error, "message_dict") else error.messages)
        return Response(invitation_response(invitation, raw_token), status=status.HTTP_201_CREATED)


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
            change, raw_token = request_staff_email_change(
                actor_user=request.user,
                **input_serializer.validated_data,
            )
        except (DjangoValidationError, StaffEmailChangeRequest.DoesNotExist) as error:
            raise serializers.ValidationError(getattr(error, "message_dict", error.messages if hasattr(error, "messages") else str(error)))
        return Response({"request_id": change.pk, "expires_at": change.expires_at, "verification_token": raw_token}, status=status.HTTP_201_CREATED)


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
