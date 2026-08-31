from interests.models import PersonInterest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditEvent
from audit.policies import build_person_audit_scope_q, filter_person_audit_visibility_for_user
from audit.services import record_audit_event
from audit.serializers import (
    PaginatedPersonAuditHistorySerializer,
    PersonAuditHistoryEventSerializer,
    PersonAuditHistoryQuerySerializer,
)
from people.models import Person
from people.serializers import (
    DuplicatePersonMatchSerializer,
    EmptyPersonLifecycleSerializer,
    PaginatedPersonListSerializer,
    PersonCreateSerializer,
    PersonListQuerySerializer,
    PersonListSerializer,
    PersonMemberCreateSerializer,
    PersonOverviewSerializer,
    PersonUpdateSerializer,
)
from people.services import find_business_duplicate_people
from people.querying import PeopleDirectoryQuery
from memberships.models import Membership
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from skills.models import PersonSkill
from tags.models import PersonTag


class PeoplePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_size(self, request):
        params = getattr(request, "_validated_people_query_params", {})
        return params.get("page_size", self.page_size)


class HasPeopleAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class HasPeopleWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class PersonLifecycleConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Person lifecycle action cannot be completed."
    default_code = "person_lifecycle_conflict"


class DuplicatePersonConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "duplicate_person"

    def __init__(self, matches):
        super().__init__(
            {
                "detail": "A possible existing Person was found.",
                "code": self.default_code,
                "matches": DuplicatePersonMatchSerializer(matches, many=True).data,
            }
        )


class BusinessPersonQuerysetMixin:
    def get_business_people_queryset(self):
        return Person.objects.business()

    def get_business_person_or_404(self):
        person = self.get_business_people_queryset().filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person


class PeopleListView(BusinessPersonQuerysetMixin, generics.ListAPIView):
    serializer_class = PersonListSerializer
    permission_classes = [IsAuthenticated, HasPeopleAccess]
    pagination_class = PeoplePagination

    @extend_schema(
        operation_id="people_list",
        summary="List CRM People",
        description=(
            "Returns BUSINESS Person records for the Staff CRM People directory. "
            "TECHNICAL persons are excluded for all record_state values. "
            "Supports repeated relationship, location, industry, career_stage, interest, skill, and tag filters: "
            "values are ORed within a category and categories combine with AND. "
            "Interest, Skill, and Tag filters use current active assignments only. "
            "Active CRM staff roles CRM_ADMIN, CRM_MANAGER, and CRM_VIEWER are allowed. "
            "Session authentication is required."
        ),
        parameters=[PersonListQuerySerializer],
        responses={
            200: PaginatedPersonListSerializer,
            400: OpenApiResponse(description="Invalid query parameter value."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["People"],
        examples=[
            OpenApiExample(
                "Active people list",
                value={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 1,
                            "first_name": "Amina",
                            "last_name": "Johnson",
                            "primary_email": "amina@example.com",
                            "mobile": "+265991234567",
                            "location": "Lilongwe",
                            "age_range": "",
                            "gender": "",
                            "archived_at": None,
                            "created_at": "2026-08-29T12:00:00Z",
                            "updated_at": "2026-08-29T12:00:00Z",
                        }
                    ],
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        self.validated_query_params = self.get_validated_query_params()
        request._validated_people_query_params = self.validated_query_params
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        params = getattr(self, "validated_query_params", self.get_validated_query_params())
        return PeopleDirectoryQuery(self.get_business_people_queryset(), params).apply()

    def get_validated_query_params(self):
        serializer = PersonListQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasPeopleAccess]
        if self.request.method == "POST":
            permission_classes = [IsAuthenticated, HasPeopleWriteAccess]
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="people_create",
        summary="Create CRM Contact",
        description=(
            "Creates an active BUSINESS Person with no Membership. Only CRM_ADMIN and CRM_MANAGER "
            "may create People. Potential duplicate BUSINESS identities, including archived records, return 409."
        ),
        request=PersonCreateSerializer,
        responses={
            201: PersonListSerializer,
            400: OpenApiResponse(description="Invalid or server-managed request field."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            409: OpenApiResponse(description="A possible existing BUSINESS Person was found."),
        },
        tags=["People"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = PersonCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            self.raise_if_duplicate(**input_serializer.validated_data)
            person = self.create_person(input_serializer.validated_data)
            self.record_person_audit(AuditEvent.Action.PERSON_CREATED, request.user, person, {
                "created": {"from": False, "to": True},
            })

        return Response(PersonListSerializer(person).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def create_person(validated_data):
        person = Person(record_type=Person.RecordType.BUSINESS, **validated_data)
        try:
            person.full_clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict)
        person.save()
        return person

    @staticmethod
    def raise_if_duplicate(*, primary_email="", mobile="", exclude_person_id=None, **_unused):
        matches = find_business_duplicate_people(
            primary_email=primary_email,
            mobile=mobile,
            exclude_person_id=exclude_person_id,
        )
        if matches:
            raise DuplicatePersonConflict(matches)

    @staticmethod
    def record_person_audit(action, actor_user, person, changes):
        record_audit_event(
            action=action,
            actor_user=actor_user,
            entity_type="Person",
            entity_id=person.id,
            changes=changes,
            metadata={"person_id": str(person.id)},
        )


class PersonMemberCreateView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasPeopleWriteAccess]

    @extend_schema(
        operation_id="people_members_create",
        summary="Create CRM Member",
        description=(
            "Creates an active BUSINESS Person and its first ACTIVE Membership in one transaction. "
            "Both PERSON_CREATED and MEMBERSHIP_CREATED audit events must persist or all new state rolls back."
        ),
        request=PersonMemberCreateSerializer,
        responses={
            201: PersonListSerializer,
            400: OpenApiResponse(description="Invalid or server-managed request field."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            409: OpenApiResponse(description="A possible existing BUSINESS Person was found."),
        },
        tags=["People", "Membership"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = PersonMemberCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        person_data = {
            field: value for field, value in input_serializer.validated_data.items()
            if field in PersonCreateSerializer().fields
        }

        with transaction.atomic():
            PeopleListView.raise_if_duplicate(**person_data)
            person = PeopleListView.create_person(person_data)
            membership = Membership(
                person=person,
                status=Membership.Status.ACTIVE,
                ended_at=None,
                joined_at=input_serializer.validated_data["joined_at"],
                membership_source=input_serializer.validated_data["membership_source"],
            )
            try:
                membership.full_clean()
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.message_dict)
            membership.save()
            PeopleListView.record_person_audit(
                AuditEvent.Action.PERSON_CREATED,
                request.user,
                person,
                {"created": {"from": False, "to": True}},
            )
            record_audit_event(
                action=AuditEvent.Action.MEMBERSHIP_CREATED,
                actor_user=request.user,
                entity_type="Membership",
                entity_id=membership.id,
                changes={
                    "status": {"from": None, "to": Membership.Status.ACTIVE},
                    "joined_at": {"from": None, "to": membership.joined_at.isoformat()},
                    "membership_source": {"from": None, "to": membership.membership_source},
                },
                metadata={"person_id": str(person.id)},
            )

        return Response(PersonListSerializer(person).data, status=status.HTTP_201_CREATED)


class PersonDetailView(BusinessPersonQuerysetMixin, generics.RetrieveAPIView):
    serializer_class = PersonListSerializer
    permission_classes = [IsAuthenticated, HasPeopleAccess]
    lookup_url_kwarg = "person_id"

    @extend_schema(
        operation_id="people_retrieve",
        summary="Retrieve CRM Person",
        description=(
            "Returns a single BUSINESS Person record for the Staff CRM People domain. "
            "Archived BUSINESS records remain retrievable by direct ID. "
            "TECHNICAL persons are outside the CRM People domain and return 404."
        ),
        responses={
            200: PersonListSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["People"],
        examples=[
            OpenApiExample(
                "Person detail",
                value={
                    "id": 14,
                    "first_name": "Amina",
                    "last_name": "Zulu",
                    "primary_email": "amina@example.com",
                    "mobile": "991000001",
                    "location": "Lilongwe",
                    "age_range": "",
                    "gender": "",
                    "archived_at": None,
                    "created_at": "2026-08-29T12:00:00Z",
                    "updated_at": "2026-08-29T12:00:00Z",
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return self.get_business_people_queryset()

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasPeopleAccess]
        if self.request.method == "PATCH":
            permission_classes = [IsAuthenticated, HasPeopleWriteAccess]
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="people_partial_update",
        summary="Edit CRM Person",
        description=(
            "Edits authoritative Person-owned fields for an active BUSINESS Person. "
            "Only changed fields are audited; a no-op PATCH succeeds without a PERSON_UPDATED event."
        ),
        request=PersonUpdateSerializer,
        responses={
            200: PersonListSerializer,
            400: OpenApiResponse(description="Invalid or server-managed request field."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(description="The person is archived or a duplicate candidate was found."),
        },
        tags=["People"],
    )
    def patch(self, request, *args, **kwargs):
        input_serializer = PersonUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_people_queryset().select_for_update().filter(
                pk=self.kwargs["person_id"]
            ).first()
            if person is None:
                raise NotFound("Not found.")
            if person.archived_at is not None:
                raise PersonLifecycleConflict("Archived people cannot be edited.")

            changes = {}
            for field, new_value in input_serializer.validated_data.items():
                old_value = getattr(person, field)
                if old_value != new_value:
                    changes[field] = {"from": old_value, "to": new_value}

            if changes:
                changed_identity_fields = {"primary_email", "mobile"}.intersection(changes)
                if changed_identity_fields:
                    PeopleListView.raise_if_duplicate(
                        primary_email=person.primary_email if "primary_email" not in changes else changes["primary_email"]["to"],
                        mobile=person.mobile if "mobile" not in changes else changes["mobile"]["to"],
                        exclude_person_id=person.id,
                    )
                for field, change in changes.items():
                    setattr(person, field, change["to"])
                try:
                    person.full_clean()
                except DjangoValidationError as error:
                    raise serializers.ValidationError(error.message_dict)
                person.save(update_fields=[*changes.keys(), "updated_at"])
                PeopleListView.record_person_audit(
                    AuditEvent.Action.PERSON_UPDATED, request.user, person, changes
                )

        return Response(PersonListSerializer(person).data)


class PersonArchiveView(BusinessPersonQuerysetMixin, generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasPeopleWriteAccess]

    @extend_schema(
        operation_id="people_archive",
        summary="Archive CRM Person",
        description=(
            "Archives an active BUSINESS Person. This does not alter Membership, User, Staff Access, "
            "or other related domain records, and emits PERSON_ARCHIVED."
        ),
        request=EmptyPersonLifecycleSerializer,
        responses={200: PersonListSerializer, 401: OpenApiResponse(description="Authentication credentials were not provided."), 403: OpenApiResponse(description="You do not have a permitted active staff role."), 404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."), 409: OpenApiResponse(description="The person is already archived.")},
        tags=["People"],
    )
    def post(self, request, *args, **kwargs):
        EmptyPersonLifecycleSerializer(data=request.data).is_valid(raise_exception=True)
        with transaction.atomic():
            person = self.get_locked_business_person()
            if person.archived_at is not None:
                raise PersonLifecycleConflict("This person is already archived.")
            person.archived_at = timezone.now()
            person.save(update_fields=["archived_at", "updated_at"])
            PeopleListView.record_person_audit(
                AuditEvent.Action.PERSON_ARCHIVED, request.user, person, {"archived": {"from": False, "to": True}}
            )
        return Response(PersonListSerializer(person).data)

    def get_locked_business_person(self):
        person = self.get_business_people_queryset().select_for_update().filter(
            pk=self.kwargs["person_id"]
        ).first()
        if person is None:
            raise NotFound("Not found.")
        return person


class PersonRestoreView(PersonArchiveView):
    @extend_schema(
        operation_id="people_restore",
        summary="Restore CRM Person",
        description=(
            "Restores an archived BUSINESS Person without changing Membership, User, Staff Access, "
            "or other related domain records, and emits PERSON_RESTORED."
        ),
        request=EmptyPersonLifecycleSerializer,
        responses={200: PersonListSerializer, 401: OpenApiResponse(description="Authentication credentials were not provided."), 403: OpenApiResponse(description="You do not have a permitted active staff role."), 404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."), 409: OpenApiResponse(description="The person is already active.")},
        tags=["People"],
    )
    def post(self, request, *args, **kwargs):
        EmptyPersonLifecycleSerializer(data=request.data).is_valid(raise_exception=True)
        with transaction.atomic():
            person = self.get_locked_business_person()
            if person.archived_at is None:
                raise PersonLifecycleConflict("This person is already active.")
            person.archived_at = None
            person.save(update_fields=["archived_at", "updated_at"])
            PeopleListView.record_person_audit(
                AuditEvent.Action.PERSON_RESTORED, request.user, person, {"archived": {"from": True, "to": False}}
            )
        return Response(PersonListSerializer(person).data)


class PersonOverviewDetailView(BusinessPersonQuerysetMixin, generics.RetrieveAPIView):
    serializer_class = PersonOverviewSerializer
    permission_classes = [IsAuthenticated, HasPeopleAccess]
    lookup_url_kwarg = "person_id"

    @extend_schema(
        operation_id="people_overview_retrieve",
        summary="Retrieve CRM Person overview projection",
        description=(
            "Returns a read-only aggregate CRM projection for a single BUSINESS Person. "
            "The response composes the authoritative Person resource plus optional Membership, ProfessionalProfile, Skill, Interest, and Tag data. "
            "Archived BUSINESS records remain retrievable by direct ID. "
            "TECHNICAL persons are outside the CRM People domain and return 404. "
            "When no Membership exists, membership is null and relationship is derived as Contact. "
            "When no ProfessionalProfile exists, professional_profile is null. "
            "Only active Skill, Interest, and Tag definitions appear in the overview collections. "
            "Tags are internal CRM classification data and do not expose lifecycle metadata in this projection."
        ),
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            )
        ],
        responses={
            200: PersonOverviewSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["People"],
        examples=[
            OpenApiExample(
                "Person overview active member",
                value={
                    "person": {
                        "id": 14,
                        "first_name": "Amina",
                        "last_name": "Zulu",
                        "primary_email": "amina@example.com",
                        "mobile": "991000001",
                        "location": "Lilongwe",
                        "age_range": "",
                        "gender": "",
                        "archived_at": None,
                        "created_at": "2026-08-29T12:00:00Z",
                        "updated_at": "2026-08-29T12:00:00Z",
                    },
                    "relationship": {
                        "type": "ACTIVE_MEMBER",
                        "label": "Active Member",
                    },
                    "membership": {
                        "id": 5,
                        "status": "ACTIVE",
                        "joined_at": "2024-04-12",
                        "ended_at": None,
                        "membership_source": "WEBSITE_FORM",
                        "created_at": "2026-08-30T11:00:00Z",
                        "updated_at": "2026-08-30T11:00:00Z",
                    },
                    "professional_profile": {
                        "id": 9,
                        "job_title": "Software Engineer",
                        "company": "Example Ltd",
                        "industry": {
                            "id": 3,
                            "name": "Technology",
                            "slug": "technology",
                        },
                        "career_stage": None,
                        "linkedin_url": "https://www.linkedin.com/in/example",
                        "created_at": "2026-08-30T11:30:00Z",
                        "updated_at": "2026-08-30T11:30:00Z",
                    },
                    "skills": [
                        {
                            "id": 21,
                            "name": "Software Development",
                            "slug": "software-development",
                        }
                    ],
                    "interests": [
                        {
                            "id": 5,
                            "name": "Technology",
                            "slug": "technology",
                        }
                    ],
                    "tags": [
                        {
                            "id": 8,
                            "name": "VIP",
                            "slug": "vip",
                        }
                    ],
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        active_person_skills = PersonSkill.objects.select_related("skill").filter(
            skill__is_active=True,
        ).order_by("skill__display_order", "skill__name", "skill__id")
        active_person_interests = PersonInterest.objects.select_related("interest").filter(
            interest__is_active=True,
        ).order_by("interest__display_order", "interest__name", "interest__id")
        active_person_tags = PersonTag.objects.select_related("tag").filter(
            is_active=True,
            tag__is_active=True,
        ).order_by("tag__display_order", "tag__name", "tag__id")
        return self.get_business_people_queryset().select_related(
            "membership",
            "professional_profile",
            "professional_profile__industry",
        ).prefetch_related(
            Prefetch("person_skills", queryset=active_person_skills, to_attr="active_person_skills"),
            Prefetch("person_interests", queryset=active_person_interests, to_attr="active_person_interests"),
            Prefetch("person_tags", queryset=active_person_tags, to_attr="active_person_tags"),
        )


class PersonAuditHistoryPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_size(self, request):
        params = getattr(request, "_validated_person_audit_history_query_params", {})
        return params.get("page_size", self.page_size)


class PersonAuditHistoryView(BusinessPersonQuerysetMixin, generics.GenericAPIView):
    serializer_class = PersonAuditHistoryEventSerializer
    permission_classes = [IsAuthenticated, HasPeopleAccess]
    pagination_class = PersonAuditHistoryPagination

    @extend_schema(
        operation_id="people_audit_history_list",
        summary="List CRM Person audit history",
        description=(
            "Returns a paginated, newest-first audit history projection for a single CRM-visible BUSINESS Person. "
            "The endpoint reads existing immutable AuditEvent rows already scoped to that Person through metadata.person_id "
            "or direct Person entity linkage. Archived BUSINESS people remain readable by direct ID. "
            "CRM_ADMIN, CRM_MANAGER, and CRM_VIEWER may access the endpoint, but CRM_VIEWER receives a permission-filtered queryset: "
            "Internal Note audit events are excluded before count and pagination so note activity cannot be inferred from totals or page gaps. "
            "The response is a safe operational projection, not a raw AuditEvent dump, and it never exposes metadata, request_id, ip_address, note body, or archive_reason."
        ),
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            ),
            PersonAuditHistoryQuerySerializer,
        ],
        responses={
            200: PaginatedPersonAuditHistorySerializer,
            400: OpenApiResponse(description="Invalid pagination query parameter value."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["People", "Audit History"],
        examples=[
            OpenApiExample(
                "Person audit history page",
                value={
                    "count": 2,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 19,
                            "action": "TAG_ASSIGNED",
                            "description": "Tag assigned",
                            "actor": {"id": 3, "email": "manager@example.com"},
                            "occurred_at": "2026-08-31T16:10:00Z",
                            "entity_type": "PersonTag",
                            "changes": {"is_active": {"from": None, "to": True}},
                        },
                        {
                            "id": 18,
                            "action": "MEMBERSHIP_CREATED",
                            "description": "Membership created",
                            "actor": {"id": 2, "email": "admin@example.com"},
                            "occurred_at": "2026-08-31T16:00:00Z",
                            "entity_type": "Membership",
                            "changes": {
                                "status": {"from": None, "to": "ACTIVE"},
                                "joined_at": {"from": None, "to": "2026-08-31"},
                                "membership_source": {"from": None, "to": "STAFF"},
                            },
                        },
                    ],
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        self.validated_query_params = self.get_validated_query_params()
        request._validated_person_audit_history_query_params = self.validated_query_params
        person = self.get_business_person_or_404()
        queryset = self.get_queryset_for_person(person.id)
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def get_queryset_for_person(self, person_id):
        queryset = AuditEvent.objects.select_related("actor_user").filter(build_person_audit_scope_q(person_id))
        queryset = filter_person_audit_visibility_for_user(queryset, self.request.user)
        return queryset.order_by("-occurred_at", "-id")

    def get_validated_query_params(self):
        serializer = PersonAuditHistoryQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data
