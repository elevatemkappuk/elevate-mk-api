from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination

from audit.models import AuditEvent
from audit.services import record_audit_event
from community.directory import build_directory_projection
from community.models import CommunityConnection
from community.services import is_community_eligible_user
from memberships.models import Membership
from people.models import Person
from interests.models import PersonInterest
from skills.models import PersonSkill


CONNECTION_STATE_NO_RELATIONSHIP = "NO_RELATIONSHIP"
CONNECTION_STATE_OUTGOING_PENDING = "OUTGOING_PENDING"
CONNECTION_STATE_INCOMING_PENDING = "INCOMING_PENDING"
CONNECTION_STATE_CONNECTED = "CONNECTED"


class CommunityConnectionPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class CommunityConnectionError(Exception):
    status_code = 400
    code = "CONNECTION_ERROR"
    detail = "We could not complete that connection action."

    def __init__(self, detail=None):
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail


class CommunityConnectionNotFound(CommunityConnectionError):
    status_code = 404
    code = "CONNECTION_NOT_FOUND"
    detail = "This Community connection is not available."


class CommunityConnectionTargetUnavailable(CommunityConnectionError):
    status_code = 404
    code = "CONNECTION_TARGET_UNAVAILABLE"
    detail = "This Community member is not available for connection."


class CommunityConnectionSelfRequest(CommunityConnectionError):
    code = "CONNECTION_SELF_REQUEST"
    detail = "You cannot send a connection request to yourself."


class CommunityConnectionInvalidState(CommunityConnectionError):
    status_code = 409
    code = "CONNECTION_INVALID_STATE"
    detail = "That connection action is no longer available."


def canonical_person_pair(first_person_id, second_person_id):
    first_person_id = int(first_person_id)
    second_person_id = int(second_person_id)
    if first_person_id == second_person_id:
        raise CommunityConnectionSelfRequest()
    return tuple(sorted((first_person_id, second_person_id)))


def _eligible_person_queryset(*, require_community_profile=False):
    queryset = Person.objects.filter(
        record_type=Person.RecordType.BUSINESS,
        archived_at__isnull=True,
        membership__status=Membership.Status.ACTIVE,
        user__is_active=True,
    )
    if require_community_profile:
        queryset = queryset.filter(community_profile__isnull=False)
    return queryset


def is_community_eligible_person(person_id, *, require_community_profile=False):
    return _eligible_person_queryset(
        require_community_profile=require_community_profile,
    ).filter(pk=person_id).exists()


def _audit_connection(*, action, connection, request, old_status, new_status):
    return record_audit_event(
        action=action,
        actor_user=request.user,
        entity_type="CommunityConnection",
        entity_id=connection.public_id,
        changes={"status": {"from": old_status, "to": new_status}},
        metadata={"source": "COMMUNITY_CONNECTIONS"},
        request_id=request.headers.get("X-Request-ID"),
        ip_address=request.META.get("REMOTE_ADDR"),
    )


def _connection_for_pair(first_person_id, second_person_id, *, for_update=False):
    person_low_id, person_high_id = canonical_person_pair(first_person_id, second_person_id)
    queryset = CommunityConnection.objects
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.filter(person_low_id=person_low_id, person_high_id=person_high_id).first()


def _lock_pair(first_person_id, second_person_id):
    person_low_id, person_high_id = canonical_person_pair(first_person_id, second_person_id)
    locked = list(
        Person.objects.select_for_update()
        .filter(pk__in=(person_low_id, person_high_id))
        .order_by("pk")
    )
    if len(locked) != 2:
        raise CommunityConnectionTargetUnavailable()
    return person_low_id, person_high_id


def _current_connection_between(first_person_id, second_person_id):
    if first_person_id == second_person_id:
        return None
    return _connection_for_pair(first_person_id, second_person_id)


def currently_connected(first_person_id, second_person_id):
    if first_person_id == second_person_id:
        return False
    if not is_community_eligible_person(first_person_id) or not is_community_eligible_person(second_person_id):
        return False
    return CommunityConnection.objects.filter(
        person_low_id=min(first_person_id, second_person_id),
        person_high_id=max(first_person_id, second_person_id),
        status=CommunityConnection.Status.ACCEPTED,
    ).exists()


def connection_relationship_projection(*, viewer_person_id, target_person_id, target_directory_visible):
    connection = _current_connection_between(viewer_person_id, target_person_id)
    if connection is None or connection.status in {
        CommunityConnection.Status.DECLINED,
        CommunityConnection.Status.DISCONNECTED,
    }:
        return {
            "state": CONNECTION_STATE_NO_RELATIONSHIP,
            "connection_id": None,
            "can_connect": viewer_person_id != target_person_id and target_directory_visible,
            "can_accept": False,
            "can_decline": False,
            "can_remove": False,
        }

    if connection.status == CommunityConnection.Status.ACCEPTED:
        return {
            "state": CONNECTION_STATE_CONNECTED,
            "connection_id": str(connection.public_id),
            "can_connect": False,
            "can_accept": False,
            "can_decline": False,
            "can_remove": True,
        }

    viewer_is_requester = connection.requester_id == viewer_person_id
    return {
        "state": CONNECTION_STATE_OUTGOING_PENDING if viewer_is_requester else CONNECTION_STATE_INCOMING_PENDING,
        "connection_id": str(connection.public_id),
        "can_connect": False,
        "can_accept": not viewer_is_requester,
        "can_decline": not viewer_is_requester,
        "can_remove": False,
    }


def connection_aware_directory_person(*, viewer_person_id, directory_id):
    accepted_connection = CommunityConnection.objects.filter(
        status=CommunityConnection.Status.ACCEPTED,
    ).filter(
        Q(person_low_id=viewer_person_id, person_high_id=OuterRef("pk"))
        | Q(person_high_id=viewer_person_id, person_low_id=OuterRef("pk"))
    )
    return (
        _eligible_person_queryset(require_community_profile=True)
        .filter(community_profile__directory_id=directory_id)
        .annotate(_has_accepted_connection=Exists(accepted_connection))
        .filter(Q(community_profile__directory_visible=True) | Q(_has_accepted_connection=True))
        .select_related(
            "community_profile",
            "professional_profile",
            "professional_profile__industry",
            "membership",
        )
        .prefetch_related(
            Prefetch(
                "person_skills",
                queryset=PersonSkill.objects.filter(skill__is_active=True).select_related("skill"),
                to_attr="directory_skills",
            ),
            Prefetch(
                "person_interests",
                queryset=PersonInterest.objects.filter(interest__is_active=True).select_related("interest"),
                to_attr="directory_interests",
            ),
        )
        .first()
    )


def build_connection_projection(person, *, include_state_for=None):
    projection = build_directory_projection(person)
    if include_state_for is not None:
        projection["relationship"] = connection_relationship_projection(
            viewer_person_id=include_state_for,
            target_person_id=person.pk,
            target_directory_visible=person.community_profile.directory_visible,
        )
    return projection


def build_connection_detail_projection(*, viewer_person_id, person):
    projection = build_directory_projection(person, include_detail=True)
    connected = currently_connected(viewer_person_id, person.pk)
    if connected:
        projection["contact"] = {
            "email": person.primary_email,
            "mobile": person.mobile,
        }
    projection["relationship"] = connection_relationship_projection(
        viewer_person_id=viewer_person_id,
        target_person_id=person.pk,
        target_directory_visible=person.community_profile.directory_visible,
    )
    return projection


def _validate_current_actor(request):
    if not is_community_eligible_user(request.user):
        raise CommunityConnectionError("Community access is unavailable.")
    if not is_community_eligible_person(request.user.person_id):
        raise CommunityConnectionError("Community access is unavailable.")
    return request.user.person_id


def _refresh_connection_people(connection):
    return Person.objects.select_related("community_profile", "professional_profile", "professional_profile__industry").prefetch_related(
        Prefetch(
            "person_skills",
            queryset=PersonSkill.objects.filter(skill__is_active=True).select_related("skill"),
            to_attr="directory_skills",
        ),
        Prefetch(
            "person_interests",
            queryset=PersonInterest.objects.filter(interest__is_active=True).select_related("interest"),
            to_attr="directory_interests",
        ),
    ).get(pk=connection.person_high_id if connection.person_low_id == connection.requester_id else connection.person_low_id)


def send_connection_request(*, request, directory_id):
    actor_person_id = _validate_current_actor(request)
    target = (
        _eligible_person_queryset(require_community_profile=True)
        .filter(community_profile__directory_id=directory_id, community_profile__directory_visible=True)
        .select_related("community_profile")
        .first()
    )
    if target is None:
        raise CommunityConnectionTargetUnavailable()
    if target.pk == actor_person_id:
        raise CommunityConnectionSelfRequest()

    with transaction.atomic():
        person_low_id, person_high_id = _lock_pair(actor_person_id, target.pk)
        target = (
            _eligible_person_queryset(require_community_profile=True)
            .filter(pk=target.pk, community_profile__directory_visible=True)
            .select_related("community_profile")
            .first()
        )
        if target is None:
            raise CommunityConnectionTargetUnavailable()
        connection = _connection_for_pair(actor_person_id, target.pk, for_update=True)
        now = timezone.now()
        if connection is None:
            connection = CommunityConnection.objects.create(
                person_low_id=person_low_id,
                person_high_id=person_high_id,
                requester_id=actor_person_id,
                status=CommunityConnection.Status.PENDING,
                requested_at=now,
            )
            old_status = None
        elif connection.status in {CommunityConnection.Status.DECLINED, CommunityConnection.Status.DISCONNECTED}:
            old_status = connection.status
            connection.requester_id = actor_person_id
            connection.status = CommunityConnection.Status.PENDING
            connection.requested_at = now
            connection.accepted_at = None
            connection.declined_at = None
            connection.disconnected_at = None
            connection.save(update_fields=["requester", "status", "requested_at", "accepted_at", "declined_at", "disconnected_at", "updated_at"])
        else:
            return connection
        _audit_connection(
            action=AuditEvent.Action.CONNECTION_REQUESTED,
            connection=connection,
            request=request,
            old_status=old_status,
            new_status=CommunityConnection.Status.PENDING,
        )
        return connection


def _mutate_connection(*, request, public_id, action):
    actor_person_id = _validate_current_actor(request)
    with transaction.atomic():
        connection = CommunityConnection.objects.filter(public_id=public_id).first()
        if connection is None:
            raise CommunityConnectionNotFound()
        _lock_pair(connection.person_low_id, connection.person_high_id)
        connection = CommunityConnection.objects.select_for_update().get(pk=connection.pk)
        if not is_community_eligible_person(connection.person_low_id) or not is_community_eligible_person(connection.person_high_id):
            raise CommunityConnectionNotFound()
        if actor_person_id not in {connection.person_low_id, connection.person_high_id}:
            raise CommunityConnectionNotFound()

        old_status = connection.status
        now = timezone.now()
        if action in {"accept", "decline"}:
            if connection.status != CommunityConnection.Status.PENDING or connection.recipient.pk != actor_person_id:
                raise CommunityConnectionInvalidState()
            if action == "accept":
                connection.status = CommunityConnection.Status.ACCEPTED
                connection.accepted_at = now
                connection.declined_at = None
                connection.disconnected_at = None
                audit_action = AuditEvent.Action.CONNECTION_ACCEPTED
            else:
                connection.status = CommunityConnection.Status.DECLINED
                connection.declined_at = now
                connection.accepted_at = None
                connection.disconnected_at = None
                audit_action = AuditEvent.Action.CONNECTION_DECLINED
            connection.save(update_fields=["status", "accepted_at", "declined_at", "disconnected_at", "updated_at"])
        else:
            if connection.status != CommunityConnection.Status.ACCEPTED:
                raise CommunityConnectionInvalidState()
            connection.status = CommunityConnection.Status.DISCONNECTED
            connection.disconnected_at = now
            connection.save(update_fields=["status", "disconnected_at", "updated_at"])
            audit_action = AuditEvent.Action.CONNECTION_REMOVED
        _audit_connection(
            action=audit_action,
            connection=connection,
            request=request,
            old_status=old_status,
            new_status=connection.status,
        )
        return connection


def member_for_connection(connection, viewer_person_id):
    target_id = connection.person_high_id if connection.person_low_id == viewer_person_id else connection.person_low_id
    if target_id == viewer_person_id:
        raise CommunityConnectionNotFound()
    target = (
        _eligible_person_queryset(require_community_profile=True)
        .filter(pk=target_id)
        .select_related("community_profile", "professional_profile", "professional_profile__industry")
        .prefetch_related(
            Prefetch(
                "person_skills",
                queryset=PersonSkill.objects.filter(skill__is_active=True).select_related("skill"),
                to_attr="directory_skills",
            ),
            Prefetch(
                "person_interests",
                queryset=PersonInterest.objects.filter(interest__is_active=True).select_related("interest"),
                to_attr="directory_interests",
            ),
        )
        .first()
    )
    if target is None:
        raise CommunityConnectionNotFound()
    return target


def list_connections(*, request, status):
    actor_person_id = _validate_current_actor(request)
    eligible_people = _eligible_person_queryset().values("pk")
    queryset = CommunityConnection.objects.filter(status=status).filter(
        Q(person_low_id=actor_person_id) | Q(person_high_id=actor_person_id),
        person_low_id__in=eligible_people,
        person_high_id__in=eligible_people,
    ).order_by("-updated_at", "-id")
    return actor_person_id, queryset


def list_connection_requests(*, request, direction):
    actor_person_id = _validate_current_actor(request)
    eligible_people = _eligible_person_queryset().values("pk")
    queryset = CommunityConnection.objects.filter(status=CommunityConnection.Status.PENDING)
    if direction == "incoming":
        queryset = queryset.filter(
            Q(person_low_id=actor_person_id, requester_id=F("person_high_id"))
            | Q(person_high_id=actor_person_id, requester_id=F("person_low_id"))
        )
    else:
        queryset = queryset.filter(requester_id=actor_person_id)
    queryset = queryset.filter(
        person_low_id__in=eligible_people,
        person_high_id__in=eligible_people,
    )
    return actor_person_id, queryset.order_by("-requested_at", "-id")
