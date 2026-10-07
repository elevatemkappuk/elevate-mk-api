from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.models import AuditEvent
from audit.services import record_audit_event
from community.feed import visible_community_posts
from community.models import CommunityContentReport, CommunityModerationAction, CommunityPost, CommunityPostReply
from community.replies import eligible_reply_author_ids
from community.services import is_community_eligible_user


class ContentModerationError(Exception):
    pass


def _audit_kwargs(request):
    return {
        "request_id": getattr(request, "request_id", None),
        "ip_address": request.META.get("REMOTE_ADDR") if request else None,
    }


def _target_metadata(*, target_type, target_id, report_id=None, reason=None):
    data = {"target_type": target_type, "target_id": str(target_id)}
    if report_id:
        data["report_id"] = str(report_id)
    if reason:
        data["reason"] = reason
    return data


def _member_post(user, public_id):
    if not is_community_eligible_user(user):
        return None
    return visible_community_posts(viewer=user).filter(public_id=public_id, status=CommunityPost.Status.ACTIVE).first()


def _member_reply(user, post_id, reply_id):
    post = _member_post(user, post_id)
    if not post:
        return None
    return (
        CommunityPostReply.objects.select_related("post", "author")
        .filter(public_id=reply_id, post_id=post.pk, status=CommunityPostReply.Status.ACTIVE, author_id__in=eligible_reply_author_ids())
        .first()
    )


def create_content_report(*, user, target, reason, details, request):
    target_field = "post" if isinstance(target, CommunityPost) else "reply"
    target_type = "POST" if target_field == "post" else "REPLY"
    if target.author_id == user.person_id:
        return None, False
    with transaction.atomic():
        target = target.__class__.objects.select_for_update().get(pk=target.pk)
        if target.status != target.Status.ACTIVE:
            return None, False
        lookup = {"reporter_id": user.person_id, target_field: target}
        existing = CommunityContentReport.objects.select_for_update().filter(status=CommunityContentReport.Status.OPEN, **lookup).first()
        if existing:
            return existing, True
        try:
            with transaction.atomic():
                report = CommunityContentReport.objects.create(
                    reporter_id=user.person_id,
                    reason=reason,
                    details=(details or "").strip(),
                    **{target_field: target},
                )
        except IntegrityError:
            report = CommunityContentReport.objects.get(status=CommunityContentReport.Status.OPEN, **lookup)
            return report, True
        record_audit_event(
            action=AuditEvent.Action.COMMUNITY_CONTENT_REPORTED,
            entity_type="CommunityContentReport",
            entity_id=report.public_id,
            actor_user=user,
            metadata=_target_metadata(target_type=target_type, target_id=target.public_id, report_id=report.public_id, reason=reason),
            **_audit_kwargs(request),
        )
        return report, False


def _report_queryset():
    return CommunityContentReport.objects.select_related(
        "reporter", "reporter__community_profile", "reporter__professional_profile__industry",
        "post", "post__author", "post__author__community_profile", "post__author__professional_profile__industry",
        "reply", "reply__author", "reply__author__community_profile", "reply__author__professional_profile__industry",
        "reply__post", "reply__post__author", "moderator",
    )


def get_staff_report(public_id):
    return _report_queryset().filter(public_id=public_id).first()


def member_visible_reply(user, post_id, reply_id):
    return _member_reply(user, post_id, reply_id)


def _identity(person):
    profile = getattr(person, "community_profile", None)
    professional = getattr(person, "professional_profile", None)
    return {
        "directory_id": getattr(profile, "directory_id", None),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "location": person.location,
        "job_title": professional.job_title if professional else "",
    }


def staff_report_projection(report):
    target, target_type = _target_for_report(report)
    if target_type == "POST":
        target_data = {
            "type": target_type, "public_id": target.public_id, "status": target.status,
            "headline": target.headline, "body": target.body, "author": _identity(target.author), "parent_post": None,
        }
    else:
        parent = target.post
        target_data = {
            "type": target_type, "public_id": target.public_id, "status": target.status,
            "headline": "", "body": target.body, "author": _identity(target.author),
            "parent_post": {"public_id": parent.public_id, "headline": parent.headline, "body": parent.body},
        }
    return {
        "report_id": report.public_id, "reason": report.reason, "details": report.details,
        "status": report.status, "created_at": report.created_at, "resolved_at": report.resolved_at,
        "reporter": _identity(report.reporter), "target": target_data,
    }


def _target_for_report(report):
    if report.post_id:
        return report.post, "POST"
    return report.reply, "REPLY"


def _save_action(*, report, target, action, moderator, resolution):
    return CommunityModerationAction.objects.create(
        action=action,
        moderator=moderator,
        report=report,
        post=target if isinstance(target, CommunityPost) else None,
        reply=target if isinstance(target, CommunityPostReply) else None,
        resolution=resolution,
    )


def moderate_report(*, report_id, action, moderator, resolution="", request):
    with transaction.atomic():
        report = CommunityContentReport.objects.select_for_update().filter(public_id=report_id).first()
        if not report:
            return None
        if report.post_id:
            target = CommunityPost.objects.select_for_update().get(pk=report.post_id)
            target_type = "POST"
        else:
            target = CommunityPostReply.objects.select_for_update().get(pk=report.reply_id)
            target_type = "REPLY"
        resolution = (resolution or "").strip()
        if action == "dismiss":
            if report.status != CommunityContentReport.Status.OPEN:
                raise ContentModerationError("This report has already been resolved.")
            report.status = CommunityContentReport.Status.DISMISSED
            report.moderator = moderator
            report.resolution = resolution
            report.resolved_at = timezone.now()
            report.save(update_fields=["status", "moderator", "resolution", "resolved_at", "updated_at"])
            _save_action(report=report, target=target, action=CommunityModerationAction.Action.DISMISSED, moderator=moderator, resolution=resolution)
            audit_action = AuditEvent.Action.COMMUNITY_MODERATION_DISMISSED
        elif action == "remove":
            if report.status != CommunityContentReport.Status.OPEN:
                raise ContentModerationError("This report has already been resolved.")
            if target.status != target.Status.ACTIVE:
                raise ContentModerationError("This content has already been removed.")
            target.status = target.Status.MODERATOR_REMOVED
            target.removed_at = timezone.now()
            target.save(update_fields=["status", "removed_at", "updated_at"])
            now = timezone.now()
            CommunityContentReport.objects.filter(status=CommunityContentReport.Status.OPEN, **({"post": target} if target_type == "POST" else {"reply": target})).update(
                status=CommunityContentReport.Status.RESOLVED, moderator=moderator, resolution=resolution, resolved_at=now, updated_at=now
            )
            _save_action(report=report, target=target, action=CommunityModerationAction.Action.CONTENT_REMOVED, moderator=moderator, resolution=resolution)
            audit_action = AuditEvent.Action.COMMUNITY_CONTENT_REMOVED
        elif action == "restore":
            if target.status != target.Status.MODERATOR_REMOVED:
                raise ContentModerationError("Only moderator-removed content can be restored.")
            target.status = target.Status.ACTIVE
            target.removed_at = None
            target.save(update_fields=["status", "removed_at", "updated_at"])
            _save_action(report=report, target=target, action=CommunityModerationAction.Action.CONTENT_RESTORED, moderator=moderator, resolution=resolution)
            audit_action = AuditEvent.Action.COMMUNITY_CONTENT_RESTORED
        else:
            raise ValidationError({"action": "Unsupported moderation action."})
        record_audit_event(
            action=audit_action,
            entity_type=target.__class__.__name__,
            entity_id=target.public_id,
            actor_user=moderator,
            metadata=_target_metadata(target_type=target_type, target_id=target.public_id, report_id=report.public_id),
            **_audit_kwargs(request),
        )
        return get_staff_report(report.public_id)
