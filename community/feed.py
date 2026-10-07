import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from community.models import CommunityConnection, CommunityPost, CommunityPostReply
from community.models import CommunityPostIdempotencyReceipt
from community.services import community_eligible_person_queryset, is_community_eligible_user
from people.models import Person


class CommunityPostIdempotencyConflict(Exception):
    pass


def visible_community_posts(*, viewer, purpose=None):
    """Return active posts visible to an eligible Community viewer.

    Visibility is evaluated from current author eligibility and current accepted
    connection state. This is a query primitive, not an API endpoint.
    """
    if not is_community_eligible_user(viewer):
        return CommunityPost.objects.none()

    viewer_person_id = viewer.person_id
    eligible_author_ids = community_eligible_person_queryset().values("pk")
    accepted_connection = CommunityConnection.objects.filter(
        status=CommunityConnection.Status.ACCEPTED,
    ).filter(
        Q(person_low_id=viewer_person_id, person_high_id=OuterRef("author_id"))
        | Q(person_high_id=viewer_person_id, person_low_id=OuterRef("author_id"))
    )

    queryset = CommunityPost.objects.filter(
        status=CommunityPost.Status.ACTIVE,
        author_id__in=eligible_author_ids,
    ).filter(
        Q(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)
        | (
            Q(audience=CommunityPost.Audience.CONNECTIONS)
            & (Q(author_id=viewer_person_id) | Exists(accepted_connection))
        )
    ).select_related(
        "author",
        "author__community_profile",
        "author__professional_profile",
        "author__professional_profile__industry",
    ).annotate(
        active_reply_count=Count(
            "replies",
            filter=Q(replies__status=CommunityPostReply.Status.ACTIVE),
        )
    ).order_by("-created_at", "-id")

    if purpose is not None:
        queryset = queryset.filter(purpose=purpose)

    return queryset


def build_community_post_author_projection(person):
    profile = getattr(person, "community_profile", None)
    professional = getattr(person, "professional_profile", None)
    industry = getattr(professional, "industry", None) if professional else None
    return {
        "directory_id": profile.directory_id if profile else None,
        "first_name": person.first_name,
        "last_name": person.last_name,
        "photo_url": profile.photo.url if profile and profile.photo else None,
        "professional": {
            "job_title": professional.job_title if professional else "",
            "industry": {"slug": industry.slug, "label": industry.name} if industry else None,
        },
        "location": person.location,
    }


def build_community_post_projection(post, *, viewer_person_id):
    return {
        "public_id": post.public_id,
        "purpose": post.purpose,
        "headline": post.headline,
        "body": post.body,
        "audience": post.audience,
        "author": build_community_post_author_projection(post.author),
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "edited_at": post.edited_at,
        "reply_count": getattr(post, "active_reply_count", 0),
        "is_own_post": post.author_id == viewer_person_id,
    }


def _post_request_digest(data):
    encoded = json.dumps(
        {
            "purpose": data["purpose"],
            "headline": data["headline"],
            "body": data["body"],
            "audience": data["audience"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _post_key_hash(idempotency_key):
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def create_community_post(*, user, data, idempotency_key=None, request=None):
    """Create a post and its audit event atomically, with optional replay safety."""
    request_digest = _post_request_digest(data)
    key_hash = _post_key_hash(idempotency_key) if idempotency_key else None

    with transaction.atomic():
        author = Person.objects.select_for_update().get(pk=user.person_id)
        receipt = None
        if key_hash:
            now = timezone.now()
            CommunityPostIdempotencyReceipt.objects.filter(expires_at__lte=now).delete()
            receipt = (
                CommunityPostIdempotencyReceipt.objects.select_for_update()
                .filter(author=author, key_hash=key_hash)
                .select_related("post")
                .first()
            )
            if receipt:
                if receipt.request_digest != request_digest:
                    raise CommunityPostIdempotencyConflict
                return receipt.post, True

        post = CommunityPost.objects.create(
            author=author,
            purpose=data["purpose"],
            headline=data["headline"],
            body=data["body"],
            audience=data["audience"],
        )

        if key_hash:
            CommunityPostIdempotencyReceipt.objects.create(
                author=author,
                key_hash=key_hash,
                request_digest=request_digest,
                post=post,
                expires_at=timezone.now() + timedelta(hours=settings.COMMUNITY_POST_IDEMPOTENCY_RETENTION_HOURS),
            )

        record_audit_event(
            action=AuditEvent.Action.COMMUNITY_POST_CREATED,
            actor_user=user,
            entity_type="CommunityPost",
            entity_id=post.public_id,
            metadata={
                "public_id": str(post.public_id),
                "purpose": post.purpose,
                "audience": post.audience,
            },
            request_id=getattr(request, "request_id", None),
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
        )
        return post, False
