import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from community.feed import build_community_post_author_projection, visible_community_posts
from community.models import (
    CommunityPost,
    CommunityPostReply,
    CommunityPostReplyIdempotencyReceipt,
)
from community.services import community_eligible_person_queryset, is_community_eligible_user
from people.models import Person


REPLY_AUTHOR_DELETED_PLACEHOLDER = "This reply was removed by the author."
REPLY_UNAVAILABLE_PLACEHOLDER = "This reply is no longer available."


class CommunityReplyUnavailable(Exception):
    pass


class CommunityReplyIdempotencyConflict(Exception):
    pass


def post_has_replies(post):
    """Return whether a post conversation has ever begun, including removed replies."""
    return CommunityPostReply.objects.filter(post_id=post.pk).exists()


def visible_replies_for_post(post):
    """Return the flat reply conversation with all safe placeholder records."""
    return CommunityPostReply.objects.filter(post_id=post.pk).select_related(
        "author",
        "author__community_profile",
        "author__professional_profile",
        "author__professional_profile__industry",
        "reply_to",
        "reply_to__author",
        "reply_to__author__community_profile",
        "reply_to__author__professional_profile",
        "reply_to__author__professional_profile__industry",
    ).order_by("created_at", "id")


def eligible_reply_author_ids():
    return set(community_eligible_person_queryset().values_list("pk", flat=True))


def _reply_author_projection(reply, eligible_author_ids):
    if reply.status != CommunityPostReply.Status.ACTIVE or reply.author_id not in eligible_author_ids:
        return None
    return build_community_post_author_projection(reply.author)


def _replying_to_projection(reply, eligible_author_ids):
    target = reply.reply_to
    if target is None:
        return None
    author = _reply_author_projection(target, eligible_author_ids)
    if author is not None:
        author = {
            "directory_id": author["directory_id"],
            "first_name": author["first_name"],
            "last_name": author["last_name"],
        }
    return {
        "reply_id": target.public_id,
        "author": author,
    }


def build_reply_projection(reply, *, viewer_person_id, eligible_author_ids=None):
    eligible_author_ids = (
        eligible_reply_author_ids() if eligible_author_ids is None else eligible_author_ids
    )
    author = _reply_author_projection(reply, eligible_author_ids)
    if reply.status == CommunityPostReply.Status.AUTHOR_DELETED:
        body = REPLY_AUTHOR_DELETED_PLACEHOLDER
    elif author is None:
        body = REPLY_UNAVAILABLE_PLACEHOLDER
    else:
        body = reply.body
    return {
        "public_id": reply.public_id,
        "body": body,
        "author": author,
        "created_at": reply.created_at,
        "updated_at": reply.updated_at,
        "edited_at": reply.edited_at if author is not None else None,
        "is_own_reply": reply.author_id == viewer_person_id,
        "replying_to": _replying_to_projection(reply, eligible_author_ids),
    }


def _reply_request_digest(*, body, reply_to_id):
    encoded = json.dumps(
        {"body": body, "reply_to_id": str(reply_to_id) if reply_to_id else None},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reply_key_hash(idempotency_key):
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _locked_visible_post(*, user, post_id):
    post = CommunityPost.objects.select_for_update().filter(pk=post_id).first()
    if post is None or post.status != CommunityPost.Status.ACTIVE:
        raise CommunityReplyUnavailable
    if not visible_community_posts(viewer=user).filter(pk=post.pk).exists():
        raise CommunityReplyUnavailable
    return post


def create_community_reply(*, user, post_id, body, reply_to_id=None, idempotency_key=None, request=None):
    """Create a reply and its audit event atomically, with optional replay safety."""
    request_digest = _reply_request_digest(body=body, reply_to_id=reply_to_id)
    key_hash = _reply_key_hash(idempotency_key) if idempotency_key else None

    with transaction.atomic():
        author = Person.objects.select_for_update().get(pk=user.person_id)
        post = _locked_visible_post(user=user, post_id=post_id)
        if not is_community_eligible_user(user):
            raise CommunityReplyUnavailable

        target = None
        if reply_to_id:
            target = (
                CommunityPostReply.objects.select_for_update()
                .select_related("author")
                .filter(public_id=reply_to_id)
                .first()
            )
            eligible_author_ids = eligible_reply_author_ids()
            if (
                target is None
                or target.post_id != post.pk
                or target.status != CommunityPostReply.Status.ACTIVE
                or target.author_id not in eligible_author_ids
            ):
                raise CommunityReplyUnavailable

        receipt = None
        if key_hash:
            now = timezone.now()
            CommunityPostReplyIdempotencyReceipt.objects.filter(expires_at__lte=now).delete()
            receipt = (
                CommunityPostReplyIdempotencyReceipt.objects.select_for_update()
                .filter(author=author, post=post, key_hash=key_hash)
                .select_related("reply")
                .first()
            )
            if receipt:
                if receipt.request_digest != request_digest:
                    raise CommunityReplyIdempotencyConflict
                return receipt.reply, True

        reply = CommunityPostReply.objects.create(
            post=post,
            author=author,
            reply_to=target,
            body=body,
        )
        if key_hash:
            CommunityPostReplyIdempotencyReceipt.objects.create(
                author=author,
                post=post,
                key_hash=key_hash,
                request_digest=request_digest,
                reply=reply,
                expires_at=timezone.now() + timedelta(hours=settings.COMMUNITY_POST_IDEMPOTENCY_RETENTION_HOURS),
            )
        record_audit_event(
            action=AuditEvent.Action.COMMUNITY_REPLY_CREATED,
            actor_user=user,
            entity_type="CommunityPostReply",
            entity_id=reply.public_id,
            metadata={
                "reply_public_id": str(reply.public_id),
                "post_public_id": str(post.public_id),
                "reply_to_public_id": str(target.public_id) if target else None,
            },
            request_id=getattr(request, "request_id", None),
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
        )
        return reply, False


def edit_community_reply(*, user, post_id, reply_id, body, request=None):
    with transaction.atomic():
        post = _locked_visible_post(user=user, post_id=post_id)
        reply = (
            CommunityPostReply.objects.select_for_update()
            .select_related("author")
            .filter(post_id=post.pk, public_id=reply_id, author_id=user.person_id, status=CommunityPostReply.Status.ACTIVE)
            .first()
        )
        if reply is None:
            raise CommunityReplyUnavailable
        if reply.body == body:
            return reply, False
        reply.body = body
        reply.edited_at = timezone.now()
        reply.save(update_fields=["body", "edited_at", "updated_at"])
        record_audit_event(
            action=AuditEvent.Action.COMMUNITY_REPLY_EDITED,
            actor_user=user,
            entity_type="CommunityPostReply",
            entity_id=reply.public_id,
            metadata={
                "reply_public_id": str(reply.public_id),
                "post_public_id": str(post.public_id),
                "reply_to_public_id": str(reply.reply_to.public_id) if reply.reply_to else None,
            },
            request_id=getattr(request, "request_id", None),
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
        )
        return reply, True


def delete_community_reply(*, user, post_id, reply_id, request=None):
    with transaction.atomic():
        post = _locked_visible_post(user=user, post_id=post_id)
        reply = (
            CommunityPostReply.objects.select_for_update()
            .filter(post_id=post.pk, public_id=reply_id, author_id=user.person_id, status=CommunityPostReply.Status.ACTIVE)
            .first()
        )
        if reply is None:
            raise CommunityReplyUnavailable
        reply.status = CommunityPostReply.Status.AUTHOR_DELETED
        reply.deleted_at = timezone.now()
        reply.save(update_fields=["status", "deleted_at", "updated_at"])
        record_audit_event(
            action=AuditEvent.Action.COMMUNITY_REPLY_DELETED,
            actor_user=user,
            entity_type="CommunityPostReply",
            entity_id=reply.public_id,
            metadata={
                "reply_public_id": str(reply.public_id),
                "post_public_id": str(post.public_id),
            },
            request_id=getattr(request, "request_id", None),
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
        )
        return reply
