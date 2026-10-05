import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login, password_validation, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch
from rest_framework import status
from drf_spectacular.utils import OpenApiResponse, extend_schema
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import Http404

from memberships.models import Membership
from interests.models import PersonInterest
from people.models import Person
from professional_profiles.models import Industry
from skills.models import PersonSkill
from accounts.serializers import LoginSerializer, PasswordResetConfirmSerializer, PasswordResetRequestSerializer
from accounts.views import INVALID_RESET_TOKEN_DETAIL, build_password_reset_url, record_auth_audit_or_raise
from audit.models import AuditEvent

from community.activation import (
    ACCOUNT_SETUP_UNAVAILABLE_CODE,
    ACCOUNT_SETUP_UNAVAILABLE_DETAIL,
    INVALID_ACTIVATION_CODE,
    INVALID_ACTIVATION_DETAIL,
    PASSWORD_VALIDATION_CODE,
    CommunityAccountSetupUnavailable,
    CommunityPasswordValidationError,
    InvalidCommunityActivation,
    check_community_activation,
    redeem_community_activation,
)
from community.serializers import (
    CommunityActivationSerializer,
    CommunityCurrentUserSerializer,
    CommunityAccountSerializer,
    CommunityAccountMarketingPreferenceSerializer,
    CommunityPasswordChangeSerializer,
    CommunityMobileUpdateSerializer,
    CommunityProfileSerializer,
    CommunityProfileWriteSerializer,
    CommunityProfileOptionsSerializer,
    CommunityIndustrySerializer,
    CommunityJoinSerializer,
    CommunityDirectoryDetailSerializer,
    CommunityDirectoryListSerializer,
    CommunityDirectoryQuerySerializer,
    CommunityConnectionRelationshipSerializer,
    CommunityConnectionSerializer,
    CommunityConnectionRequestSerializer,
    CommunityConnectionRequestCreateSerializer,
    CommunityConnectionRequestQuerySerializer,
)
from community.photo_serializers import CommunityProfilePhotoUploadSerializer
from community.photos import ProfilePhotoValidationError
from community.services import (
    CommunityJoinIdempotencyConflict,
    CommunityJoinReviewRequired,
    submit_community_join,
    is_community_eligible_user,
    build_community_profile_projection,
    get_or_create_community_profile,
    update_community_profile,
    community_profile_options,
    acknowledge_community_profile_review,
    upload_community_profile_photo,
    remove_community_profile_photo,
    build_community_account_summary_projection,
    change_community_password,
    CommunityPasswordChangeError,
    CommunityMobileConflictError,
    update_community_mobile,
    update_community_email_marketing_preference,
)
from community.directory import (
    CommunityDirectoryPagination,
    build_directory_projection,
    community_directory_queryset,
    directory_filter_queryset,
    directory_search_queryset,
)
from community.connections import (
    CommunityConnectionError,
    CommunityConnectionPagination,
    build_connection_detail_projection,
    build_connection_projection,
    connection_relationship_projections,
    connection_aware_directory_person,
    list_connection_requests,
    list_connections,
    members_for_connections,
    member_for_connection,
    send_connection_request,
    _mutate_connection,
)
from community.models import CommunityConnection
from notifications.exceptions import TransactionalEmailError
from notifications.services import send_transactional_email


logger = logging.getLogger(__name__)
User = get_user_model()

COMMUNITY_LOGIN_INVALID_CODE = "INVALID_CREDENTIALS"
COMMUNITY_LOGIN_INVALID_DETAIL = "Email or password is incorrect."
COMMUNITY_ACCESS_UNAVAILABLE_CODE = "COMMUNITY_ACCESS_UNAVAILABLE"
COMMUNITY_ACCESS_UNAVAILABLE_DETAIL = "Community access is not available for this account."
COMMUNITY_PASSWORD_RESET_DETAIL = "If an eligible Elevate MK account exists for that email address, we've sent password reset instructions."


class CommunityIndustryListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        operation_id="community_industries_list",
        summary="List public Community Industries",
        responses={200: CommunityIndustrySerializer(many=True)},
        auth=[],
        tags=["Community"],
    )
    def get(self, request):
        industries = Industry.objects.filter(is_active=True)
        return Response(CommunityIndustrySerializer(industries, many=True).data)


@method_decorator(csrf_protect, name="dispatch")
class CommunityPasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_password_reset"

    @extend_schema(
        operation_id="community_password_reset_request",
        summary="Request Community password-reset instructions",
        request=PasswordResetRequestSerializer,
        responses={200: OpenApiResponse(description="Generic reset-request response.")},
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.select_related("person").filter(email=serializer.validated_data["email"]).first()
        if user and user.is_active and user.has_usable_password() and is_community_eligible_user(user):
            try:
                send_transactional_email(
                    recipient_email=user.email,
                    recipient_name=user.get_full_name() or None,
                    template_id=settings.BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID,
                    template_params={"reset_url": build_password_reset_url(user=user, frontend_url=settings.COMMUNITY_FRONTEND_URL)},
                )
            except TransactionalEmailError:
                logger.warning("Community password reset email delivery failed.")
        return Response({"detail": COMMUNITY_PASSWORD_RESET_DETAIL}, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class CommunityPasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    @extend_schema(
        operation_id="community_password_reset_confirm",
        summary="Confirm a Community password reset",
        description="Public, CSRF-protected endpoint. The reset link must still belong to an eligible Community account. Successful reset does not log the user in.",
        request=PasswordResetConfirmSerializer,
        responses={200: OpenApiResponse(description="Password reset completed."), 400: OpenApiResponse(description="Invalid or unavailable reset link.")},
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(
            data=request.data,
            context={"defer_password_validation": True},
        )
        if not serializer.is_valid():
            data = serializer.errors
            if str(data.get("code", [""])[0]) == "invalid_password_reset_token":
                return self.invalid_reset_response()
            return Response(data, status=status.HTTP_400_BAD_REQUEST)

        submitted_user = serializer.validated_data["user"]
        with transaction.atomic():
            person = Person.objects.select_for_update().filter(pk=submitted_user.person_id).first()
            user = User.objects.select_for_update().select_related("person").filter(pk=submitted_user.pk).first()
            membership = (
                Membership.objects.select_for_update()
                .filter(person_id=submitted_user.person_id, status=Membership.Status.ACTIVE)
                .first()
            )
            if (
                person is None
                or user is None
                or membership is None
                or not is_community_eligible_user(user)
                or not user.is_active
                or not default_token_generator.check_token(user, serializer.validated_data["token"])
            ):
                return self.invalid_reset_response()

            try:
                password_validation.validate_password(serializer.validated_data["new_password"], user)
            except DjangoValidationError as error:
                return Response(
                    {"new_password": list(error.messages)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=["password"])
            record_auth_audit_or_raise(
                action=AuditEvent.Action.PASSWORD_RESET,
                actor_user=None,
                entity_type="User",
                entity_id=user.id,
                metadata={"user_id": str(user.id), "person_id": str(user.person_id)},
            )

        return Response({"detail": "Your password has been reset successfully."})

    @staticmethod
    def invalid_reset_response():
        return Response(
            {"code": "invalid_password_reset_token", "detail": INVALID_RESET_TOKEN_DETAIL},
            status=status.HTTP_400_BAD_REQUEST,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityJoinView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_join"

    @extend_schema(
        operation_id="community_join_create",
        summary="Submit a native Community membership join",
        request=CommunityJoinSerializer,
        responses={
            202: OpenApiResponse(description="Submission accepted."),
            400: OpenApiResponse(description="Invalid submission."),
            409: OpenApiResponse(description="Submission requires review."),
            429: OpenApiResponse(description="Too many submissions."),
        },
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = CommunityJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if len(idempotency_key) > 255:
            return Response(
                {"code": "VALIDATION_ERROR", "detail": "Invalid submission."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            submit_community_join(
                data=serializer.validated_data,
                request=request,
                idempotency_key=idempotency_key or None,
            )
        except (CommunityJoinReviewRequired, CommunityJoinIdempotencyConflict):
            return Response(
                {
                    "code": "SUBMISSION_REQUIRES_REVIEW",
                    "detail": "Your membership submission requires review.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "status": "accepted",
                "message": "Your membership submission has been received.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_login"

    @extend_schema(
        operation_id="community_login",
        summary="Sign in to the Community",
        request=LoginSerializer,
        responses={
            200: CommunityCurrentUserSerializer,
            400: OpenApiResponse(description="Invalid credentials."),
            403: OpenApiResponse(description="Community access unavailable."),
            429: OpenApiResponse(description="Too many login attempts."),
        },
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            record_auth_audit_or_raise(
                action=AuditEvent.Action.LOGIN_FAILED,
                actor_user=None,
                entity_type="Authentication",
                entity_id=None,
            )
            return Response(
                {"code": COMMUNITY_LOGIN_INVALID_CODE, "detail": COMMUNITY_LOGIN_INVALID_DETAIL},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.validated_data["user"]
        if not is_community_eligible_user(user):
            record_auth_audit_or_raise(
                action=AuditEvent.Action.LOGIN_FAILED,
                actor_user=user,
                entity_type="Authentication",
                entity_id=user.id,
                metadata={"authentication": "community", "outcome": "access_unavailable"},
            )
            return Response(
                {"code": COMMUNITY_ACCESS_UNAVAILABLE_CODE, "detail": COMMUNITY_ACCESS_UNAVAILABLE_DETAIL},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        record_auth_audit_or_raise(
            action=AuditEvent.Action.LOGIN_SUCCEEDED,
            actor_user=user,
            entity_type="Authentication",
            entity_id=user.id,
            metadata={"authentication": "community"},
        )
        person = user.person
        return Response(
            CommunityCurrentUserSerializer({
                "id": user.id,
                "first_name": person.first_name,
                "last_name": person.last_name,
            }).data,
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityActivationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_activation"

    @extend_schema(
        operation_id="community_activate_account",
        summary="Redeem a Community account activation invitation",
        request=None,
        responses={200: CommunityCurrentUserSerializer, 400: OpenApiResponse(description="Activation failed."), 409: OpenApiResponse(description="Account setup unavailable."), 429: OpenApiResponse(description="Too many activation attempts.")},
        auth=[],
        tags=["Community"],
    )
    def get(self, request, invitation_id, token):
        try:
            check_community_activation(invitation_id=invitation_id, raw_token=token)
        except InvalidCommunityActivation:
            return Response(
                {"code": INVALID_ACTIVATION_CODE, "detail": INVALID_ACTIVATION_DETAIL},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"usable": True}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="community_activate_account_submit",
        summary="Complete a Community account activation",
        request=CommunityActivationSerializer,
        responses={200: CommunityCurrentUserSerializer, 400: OpenApiResponse(description="Activation failed."), 409: OpenApiResponse(description="Account setup unavailable."), 429: OpenApiResponse(description="Too many activation attempts.")},
        auth=[],
        tags=["Community"],
    )
    def post(self, request, invitation_id, token):
        serializer = CommunityActivationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": PASSWORD_VALIDATION_CODE, "detail": "Please enter a valid password and confirmation.", "fields": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = redeem_community_activation(
                invitation_id=invitation_id,
                raw_token=token,
                password=serializer.validated_data["password"],
            )
        except InvalidCommunityActivation:
            return Response({"code": INVALID_ACTIVATION_CODE, "detail": INVALID_ACTIVATION_DETAIL}, status=status.HTTP_400_BAD_REQUEST)
        except CommunityPasswordValidationError as error:
            return Response({"code": PASSWORD_VALIDATION_CODE, "detail": "Password does not meet the configured requirements.", "fields": {"password": error.messages}}, status=status.HTTP_400_BAD_REQUEST)
        except CommunityAccountSetupUnavailable:
            return Response({"code": ACCOUNT_SETUP_UNAVAILABLE_CODE, "detail": ACCOUNT_SETUP_UNAVAILABLE_DETAIL}, status=status.HTTP_409_CONFLICT)

        login(request, result.user)
        return Response(
            CommunityCurrentUserSerializer({
                "id": result.user.id,
                "first_name": result.user.person.first_name,
                "last_name": result.user.person.last_name,
            }).data,
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_profile",
        summary="Get the authenticated member's Community profile",
        responses={
            200: CommunityProfileSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="Community access is unavailable."),
        },
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)

        person = (
            Person.objects.select_related(
                "professional_profile",
                "professional_profile__industry",
                "membership",
                "community_profile",
            )
            .prefetch_related(
                Prefetch(
                    "person_skills",
                    queryset=PersonSkill.objects.filter(skill__is_active=True).select_related("skill"),
                    to_attr="community_profile_skills",
                ),
                Prefetch(
                    "person_interests",
                    queryset=PersonInterest.objects.filter(interest__is_active=True).select_related("interest"),
                    to_attr="community_profile_interests",
                ),
            )
            .get(pk=request.user.person_id)
        )
        community_profile = getattr(person, "community_profile", None)
        if community_profile is None:
            community_profile = get_or_create_community_profile(person=person)
        projection = build_community_profile_projection(
            person=person,
            community_profile=community_profile,
        )
        return Response(CommunityProfileSerializer(projection).data, status=status.HTTP_200_OK)


    @extend_schema(
        operation_id="community_profile_update",
        summary="Update the authenticated member's Community profile",
        request=CommunityProfileWriteSerializer,
        responses={200: CommunityProfileSerializer, 400: OpenApiResponse(description="Invalid profile update."), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def patch(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CommunityProfileWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            projection = update_community_profile(person_id=request.user.person_id, data=serializer.validated_data, request=request)
        except DjangoValidationError as error:
            return Response(error.message_dict if hasattr(error, "message_dict") else {"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommunityProfileSerializer(projection).data, status=status.HTTP_200_OK)


class CommunityDirectoryListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_directory"

    @extend_schema(
        operation_id="community_directory_list",
        summary="List visible Community members",
        responses={200: CommunityDirectoryListSerializer(many=True), 400: OpenApiResponse(description="Invalid directory query."), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)

        query_serializer = CommunityDirectoryQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        values = query_serializer.validated_data
        queryset = directory_search_queryset(community_directory_queryset(), values.get("q"))
        queryset = directory_filter_queryset(
            queryset,
            industry=values.get("industry"),
            skill=values.get("skill"),
            interest=values.get("interest"),
        )
        paginator = CommunityDirectoryPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        relationships = connection_relationship_projections(
            viewer_person_id=request.user.person_id,
            target_person_ids=[person.pk for person in page],
        )
        data = [build_directory_projection(person, relationship=relationships[person.pk]) for person in page]
        return paginator.get_paginated_response(CommunityDirectoryListSerializer(data, many=True).data)


class CommunityDirectoryDetailView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_directory"

    @extend_schema(
        operation_id="community_directory_detail",
        summary="Read an available Community member profile",
        responses={200: CommunityDirectoryDetailSerializer, 403: OpenApiResponse(description="Community access is unavailable."), 404: OpenApiResponse(description="Directory profile not found.")},
        tags=["Community"],
    )
    def get(self, request, directory_id):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        person = connection_aware_directory_person(
            viewer_person_id=request.user.person_id,
            directory_id=directory_id,
        )
        if person is None:
            raise Http404
        return Response(
            CommunityDirectoryDetailSerializer(
                build_connection_detail_projection(
                    viewer_person_id=request.user.person_id,
                    person=person,
                )
            ).data
        )


class CommunityConnectionListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_connections"

    @extend_schema(
        operation_id="community_connections_list",
        summary="List established Community connections",
        responses={200: CommunityConnectionSerializer(many=True), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        actor_person_id, queryset = list_connections(request=request, status=CommunityConnection.Status.ACCEPTED)
        paginator = CommunityConnectionPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        members = members_for_connections(page, actor_person_id)
        serialized = [
            {
                "connection_id": connection.public_id,
                "member": build_connection_projection(members[connection.pk]),
            }
            for connection in page
        ]
        return paginator.get_paginated_response(CommunityConnectionSerializer(serialized, many=True).data)


class CommunityConnectionRequestListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_connection_requests"

    @extend_schema(
        operation_id="community_connection_requests_list",
        summary="List incoming or outgoing Community connection requests",
        parameters=[CommunityConnectionRequestQuerySerializer],
        responses={200: CommunityConnectionRequestSerializer(many=True), 400: OpenApiResponse(description="Invalid request direction."), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        query_serializer = CommunityConnectionRequestQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        direction = query_serializer.validated_data["direction"]
        actor_person_id, queryset = list_connection_requests(request=request, direction=direction)
        paginator = CommunityConnectionPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        members = members_for_connections(page, actor_person_id)
        serialized = []
        for connection in page:
            state = "INCOMING_PENDING" if direction == "incoming" else "OUTGOING_PENDING"
            serialized.append({
                "connection_id": connection.public_id,
                "state": state,
                "requested_at": connection.requested_at,
                "member": build_connection_projection(members[connection.pk]),
            })
        return paginator.get_paginated_response(CommunityConnectionRequestSerializer(serialized, many=True).data)

    @extend_schema(
        operation_id="community_connection_request_create_from_requests",
        request=CommunityConnectionRequestCreateSerializer,
        responses={200: CommunityConnectionSerializer, 400: OpenApiResponse(description="Invalid connection request."), 403: OpenApiResponse(description="Community access is unavailable."), 404: OpenApiResponse(description="Member unavailable."), 409: OpenApiResponse(description="Connection state conflict.")},
        tags=["Community"],
    )
    def post(self, request):
        return CommunityConnectionRequestCreateView().post(request)


class CommunityConnectionRequestCreateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_connection_create"

    @extend_schema(
        operation_id="community_connection_request_create",
        summary="Send a Community connection request",
        request=CommunityConnectionRequestCreateSerializer,
        responses={200: CommunityConnectionSerializer, 201: CommunityConnectionSerializer, 400: OpenApiResponse(description="Invalid connection request."), 403: OpenApiResponse(description="Community access is unavailable."), 404: OpenApiResponse(description="Member unavailable."), 409: OpenApiResponse(description="Connection state conflict.")},
        tags=["Community"],
    )
    def post(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CommunityConnectionRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            connection = send_connection_request(
                request=request,
                directory_id=serializer.validated_data["directory_id"],
            )
            member = member_for_connection(connection, request.user.person_id)
        except CommunityConnectionError as error:
            return Response({"code": error.code, "detail": error.detail}, status=error.status_code)
        payload = {
            "connection_id": connection.public_id,
            "member": build_connection_projection(member),
        }
        return Response(CommunityConnectionSerializer(payload).data, status=status.HTTP_200_OK)


class CommunityConnectionActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_connection_mutations"

    @extend_schema(
        summary="Accept or decline a Community connection",
        request=None,
        responses={200: CommunityConnectionSerializer, 204: None, 403: OpenApiResponse(description="Community access is unavailable."), 404: OpenApiResponse(description="Connection unavailable."), 409: OpenApiResponse(description="Connection state conflict.")},
        tags=["Community"],
    )
    def post(self, request, public_id, action):
        if action not in {"accept", "decline"}:
            return Response({"detail": "Unsupported connection action."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)
        return self._mutate(request, public_id, action, remove=False)

    @extend_schema(
        summary="Remove an accepted Community connection",
        request=None,
        responses={204: None, 403: OpenApiResponse(description="Community access is unavailable."), 404: OpenApiResponse(description="Connection unavailable."), 409: OpenApiResponse(description="Connection state conflict.")},
        tags=["Community"],
    )
    def delete(self, request, public_id):
        return self._mutate(request, public_id, "remove", remove=True)

    def _mutate(self, request, public_id, action, *, remove):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        try:
            connection = _mutate_connection(request=request, public_id=public_id, action=action)
            if remove:
                return Response(status=status.HTTP_204_NO_CONTENT)
            member = member_for_connection(connection, request.user.person_id)
        except CommunityConnectionError as error:
            return Response({"code": error.code, "detail": error.detail}, status=error.status_code)
        payload = {
            "connection_id": connection.public_id,
            "member": build_connection_projection(member),
        }
        return Response(CommunityConnectionSerializer(payload).data, status=status.HTTP_200_OK)

class CommunityProfileOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_profile_options",
        summary="List options for the authenticated Community profile editor",
        responses={200: CommunityProfileOptionsSerializer, 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        return Response(CommunityProfileOptionsSerializer(community_profile_options()).data, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class CommunityProfilePhotoView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_profile_photo_upload",
        summary="Upload or replace the authenticated member's Community profile photo",
        request=CommunityProfilePhotoUploadSerializer,
        responses={200: CommunityProfileSerializer, 400: OpenApiResponse(description="Invalid profile photo.")},
        tags=["Community"],
    )
    def post(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CommunityProfilePhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            projection = upload_community_profile_photo(
                person_id=request.user.person_id,
                uploaded_file=serializer.validated_data["photo"],
                request=request,
            )
        except ProfilePhotoValidationError as error:
            return Response({"photo": [str(error)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommunityProfileSerializer(projection).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="community_profile_photo_delete",
        summary="Remove the authenticated member's Community profile photo",
        responses={200: CommunityProfileSerializer, 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def delete(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        projection = remove_community_profile_photo(person_id=request.user.person_id, request=request)
        return Response(CommunityProfileSerializer(projection).data, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class CommunityProfileReviewAcknowledgementView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_profile_review_acknowledgement",
        summary="Acknowledge the authenticated member's Community profile review",
        request=None,
        responses={200: OpenApiResponse(description="Review acknowledgement recorded."), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def post(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        profile = acknowledge_community_profile_review(person_id=request.user.person_id, request=request)
        return Response({"review_required": profile.review_required}, status=status.HTTP_200_OK)


class CommunityMeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_current_user",
        summary="Get the current Community account",
        responses={200: CommunityCurrentUserSerializer, 401: OpenApiResponse(description="Authentication credentials were not provided."), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        person = request.user.person
        return Response(CommunityCurrentUserSerializer({"id": request.user.id, "first_name": person.first_name, "last_name": person.last_name}).data)


class CommunityAccountView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_account_summary",
        summary="Get the authenticated member's Community account summary",
        responses={200: CommunityAccountSerializer, 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            CommunityAccountSerializer(build_community_account_summary_projection(user=request.user)).data,
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityAccountPasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_account_password"

    @extend_schema(
        operation_id="community_account_password_change",
        summary="Change the authenticated Community member's password",
        request=CommunityPasswordChangeSerializer,
        responses={200: OpenApiResponse(description="Password changed successfully."), 400: OpenApiResponse(description="Password change validation failed."), 403: OpenApiResponse(description="Community access is unavailable."), 429: OpenApiResponse(description="Too many password changes.")},
        tags=["Community"],
    )
    def post(self, request):
        if not is_community_eligible_user(request.user):
            return Response(
                {"code": COMMUNITY_ACCESS_UNAVAILABLE_CODE, "detail": COMMUNITY_ACCESS_UNAVAILABLE_DETAIL},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CommunityPasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "PASSWORD_VALIDATION_ERROR", "detail": "Please enter a valid password and confirmation.", "fields": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            changed_user = change_community_password(
                user=request.user,
                current_password=serializer.validated_data["current_password"],
                new_password=serializer.validated_data["new_password"],
                request=request,
            )
        except CommunityPasswordChangeError as error:
            if error.code == "COMMUNITY_ACCESS_UNAVAILABLE":
                return Response(
                    {"code": COMMUNITY_ACCESS_UNAVAILABLE_CODE, "detail": COMMUNITY_ACCESS_UNAVAILABLE_DETAIL},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                {"code": error.code, "detail": error.messages[0], "fields": {error.field: error.messages}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        update_session_auth_hash(request, changed_user)
        return Response({"detail": "Your password has been changed successfully."}, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class CommunityAccountMobileView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_account_mobile"

    @extend_schema(
        operation_id="community_account_mobile_update",
        summary="Add, change, or remove the authenticated Community member's mobile",
        request=CommunityMobileUpdateSerializer,
        responses={200: CommunityAccountSerializer, 400: OpenApiResponse(description="Mobile validation failed."), 403: OpenApiResponse(description="Community access is unavailable."), 429: OpenApiResponse(description="Too many mobile changes.")},
        tags=["Community"],
    )
    def patch(self, request):
        if not is_community_eligible_user(request.user):
            return Response(
                {"code": COMMUNITY_ACCESS_UNAVAILABLE_CODE, "detail": COMMUNITY_ACCESS_UNAVAILABLE_DETAIL},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CommunityMobileUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "MOBILE_VALIDATION_ERROR", "detail": "Please enter a valid mobile number.", "fields": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            person = update_community_mobile(
                person_id=request.user.person_id,
                mobile=serializer.validated_data["mobile"],
                phone_region=serializer.validated_data.get("phone_region", ""),
                request=request,
            )
        except CommunityMobileConflictError:
            return Response(
                {"code": "MOBILE_UPDATE_UNAVAILABLE", "detail": "We couldn't update this mobile number. Please check the number or contact Elevate MK for help."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            CommunityAccountSerializer(build_community_account_summary_projection(user=person.user)).data,
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityAccountMarketingPreferenceView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_account_marketing"

    @extend_schema(
        operation_id="community_account_marketing_preference_update",
        summary="Update the authenticated Community member's email-marketing preference",
        request=CommunityAccountMarketingPreferenceSerializer,
        responses={200: CommunityAccountSerializer, 400: OpenApiResponse(description="Marketing preference validation failed."), 403: OpenApiResponse(description="Community access is unavailable."), 429: OpenApiResponse(description="Too many preference changes.")},
        tags=["Community"],
    )
    def patch(self, request):
        if not is_community_eligible_user(request.user):
            return Response(
                {"code": COMMUNITY_ACCESS_UNAVAILABLE_CODE, "detail": COMMUNITY_ACCESS_UNAVAILABLE_DETAIL},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CommunityAccountMarketingPreferenceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "MARKETING_PREFERENCE_VALIDATION_ERROR", "detail": "Please choose an email marketing preference.", "fields": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        update_community_email_marketing_preference(
            user=request.user,
            email_marketing=serializer.validated_data["email_marketing"],
        )
        return Response(
            CommunityAccountSerializer(build_community_account_summary_projection(user=request.user)).data,
            status=status.HTTP_200_OK,
        )
