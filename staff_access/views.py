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
    StaffInvitationRoleUpdateSerializer,
    StaffLifecycleSerializer,
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
from accounts.models import User


class StaffAdminPermission(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN,)


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
