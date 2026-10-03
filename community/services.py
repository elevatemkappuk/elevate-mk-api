import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from memberships.models import Membership
from marketing_preferences.models import MarketingPreference
from marketing_preferences.services import get_effective_marketing_preference, record_opt_in
from people.models import Person
from people.services import normalize_email, normalize_mobile, normalize_phone_for_community, PhoneNormalizationStatus
from professional_profiles.models import Industry, ProfessionalProfile
from skills.models import PersonSkill, Skill
from interests.models import PersonInterest, Interest
from brevo_marketing.jobs import PERSON_PROFILE_SYNC
from brevo_marketing.routing import BREVO_PROVIDER
from external_references.services import enqueue_coalesced_person_sync_job

from community.models import CommunityAccountInvitation, CommunityProfile, JoinSubmissionReceipt
from community.locking import acquire_community_join_email_lock
from notifications.models import TransactionalEmailJob
from django.core.files.base import ContentFile

from community.photos import ProfilePhotoValidationError, normalize_profile_photo


PUBLIC_REVIEW_CODE = "SUBMISSION_REQUIRES_REVIEW"

logger = logging.getLogger(__name__)

COMMUNITY_ACCOUNT_ACTIVE = "ACTIVE"
COMMUNITY_ACCOUNT_SETUP_PENDING = "SETUP_PENDING"
COMMUNITY_ACCOUNT_NOT_SET_UP = "NOT_SET_UP"
COMMUNITY_ACCOUNT_ACCESS_UNAVAILABLE = "ACCESS_UNAVAILABLE"

COMMUNITY_INVITATION_SENT = "SENT"
COMMUNITY_INVITATION_NOT_SENT = "NOT_SENT"
COMMUNITY_INVITATION_DELIVERY_UNCERTAIN = "DELIVERY_UNCERTAIN"
COMMUNITY_INVITATION_FAILED = "FAILED"


def is_community_eligible_user(user):
    """Return whether an authenticated User currently has Community access."""
    person = getattr(user, "person", None)
    return bool(
        getattr(user, "is_authenticated", False)
        and person is not None
        and person.record_type == Person.RecordType.BUSINESS
        and person.archived_at is None
        and Membership.objects.filter(person=person, status=Membership.Status.ACTIVE).exists()
    )


def get_or_create_community_profile(*, person, person_preexisted_community=False):
    """Lazily create Community-owned state without rewriting established provenance."""
    profile, _ = CommunityProfile.objects.get_or_create(
        person=person,
        defaults={
            "person_preexisted_community": bool(person_preexisted_community),
        },
    )
    return profile


def build_community_profile_projection(*, person, community_profile=None):
    """Build the explicit member-facing Community profile projection."""
    community_profile = community_profile or get_or_create_community_profile(person=person)
    professional = getattr(person, "professional_profile", None)
    membership = person.membership
    skills = [
        {
            "id": assignment.skill.id,
            "name": assignment.skill.name,
            "slug": assignment.skill.slug,
        }
        for assignment in getattr(person, "community_profile_skills", [])
    ]
    interests = [
        {
            "id": assignment.interest.id,
            "name": assignment.interest.name,
            "slug": assignment.interest.slug,
        }
        for assignment in getattr(person, "community_profile_interests", [])
    ]

    professional_data = {
        "job_title": professional.job_title if professional else "",
        "company": professional.company if professional else "",
        "industry": (
            {
                "id": professional.industry.id,
                "slug": professional.industry.slug,
                "label": professional.industry.name,
            }
            if professional and professional.industry
            else None
        ),
        "career_stage": professional.career_stage if professional else None,
        "linkedin_url": professional.linkedin_url if professional else "",
    }
    try:
        photo_url = community_profile.photo.url if community_profile.photo else None
    except Exception:
        logger.exception("Unable to generate Community profile photo URL for profile %s", community_profile.pk)
        raise

    return {
        "person": {
            "first_name": person.first_name,
            "last_name": person.last_name,
            "location": person.location,
        },
        "community": {
            "bio": community_profile.bio,
            "review_required": community_profile.review_required,
            "photo_url": photo_url,
            "directory_id": community_profile.directory_id,
            "directory_visible": community_profile.directory_visible,
            "email_visible": community_profile.email_visible,
            "mobile_visible": community_profile.mobile_visible,
        },
        "professional": professional_data,
        "skills": skills,
        "interests": interests,
        "membership": {
            "status": membership.status,
            "joined_at": membership.joined_at,
        },
        "completion": {
            "name": bool(person.first_name.strip() and person.last_name.strip()),
            "professional_details": bool(
                professional
                and professional.job_title.strip()
                and professional.industry is not None
                and professional.industry.is_active
            ),
            "bio": bool(community_profile.bio.strip()),
            "skills": bool(skills),
            "interests": bool(interests),
        },
    }


def upload_community_profile_photo(*, person_id, uploaded_file, request):
    """Validate, normalize and replace one member-owned Community photo."""
    normalized = normalize_profile_photo(uploaded_file)
    new_name = None
    storage = None
    try:
        with transaction.atomic():
            person = Person.objects.select_for_update().get(pk=person_id)
            profile, _ = CommunityProfile.objects.select_for_update().get_or_create(person=person)
            old_name = profile.photo.name if profile.photo else None
            storage = profile.photo.storage
            profile.photo.save(normalized.filename, ContentFile(normalized.content), save=False)
            new_name = profile.photo.name
            profile.save(update_fields=["photo", "updated_at"])
            _audit_self_service(
                action=AuditEvent.Action.PERSON_UPDATED,
                entity_type="CommunityProfile",
                entity_id=profile.id,
                changed_fields=["photo_replaced" if old_name else "photo_uploaded"],
                request=request,
            )
            if old_name:
                transaction.on_commit(lambda: _delete_stored_object(storage, old_name))
    except Exception:
        if new_name and storage:
            _delete_stored_object(storage, new_name)
        raise
    return build_community_profile_projection(person=person, community_profile=profile)


def remove_community_profile_photo(*, person_id, request):
    """Remove the current member-owned Community photo, if present."""
    with transaction.atomic():
        person = Person.objects.select_for_update().get(pk=person_id)
        profile, _ = CommunityProfile.objects.select_for_update().get_or_create(person=person)
        old_name = profile.photo.name if profile.photo else None
        storage = profile.photo.storage
        if old_name:
            profile.photo = None
            profile.save(update_fields=["photo", "updated_at"])
            _audit_self_service(
                action=AuditEvent.Action.PERSON_UPDATED,
                entity_type="CommunityProfile",
                entity_id=profile.id,
                changed_fields=["photo_removed"],
                request=request,
            )
            transaction.on_commit(lambda: _delete_stored_object(storage, old_name))
    return build_community_profile_projection(person=person, community_profile=profile)


def _delete_stored_object(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.exception("Unable to clean up Community profile photo object")


def _community_request_context(request):
    return {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": request.META.get("REMOTE_ADDR"),
    }


def _audit_self_service(*, action, entity_type, entity_id, changed_fields, request, created=False):
    return record_audit_event(
        action=action,
        actor_user=request.user,
        entity_type=entity_type,
        entity_id=entity_id,
        changes={"created": {"from": False, "to": True}} if created else {
            field: {"changed": True} for field in changed_fields
        },
        metadata={"source": "COMMUNITY_SELF_SERVICE", "person_id": str(request.user.person_id)},
        **_community_request_context(request),
    )


def update_community_profile(*, person_id, data, request):
    """Apply an authenticated member-owned profile update atomically."""
    with transaction.atomic():
        person = Person.objects.select_for_update().get(pk=person_id)
        profile = CommunityProfile.objects.select_for_update().filter(person=person).first()
        changed_person = []
        person_audit_event = None
        person_values = data.get("person", {})
        for field in ("first_name", "last_name", "location"):
            if field not in person_values:
                continue
            value = person_values[field]
            if field in ("first_name", "last_name"):
                from community.normalization import normalize_community_name
                value = normalize_community_name(value)
                if not value:
                    raise DjangoValidationError({field: ["This field may not be blank."]})
            else:
                value = " ".join(value.strip().split())
            if getattr(person, field) != value:
                setattr(person, field, value)
                changed_person.append(field)
        if changed_person:
            _save_validated(person, [*changed_person, "updated_at"])
            person_audit_event = _audit_self_service(
                action=AuditEvent.Action.PERSON_UPDATED,
                entity_type="Person",
                entity_id=person.id,
                changed_fields=changed_person,
                request=request,
            )

        community_values = data.get("community", {})
        if community_values:
            if profile is None:
                profile = get_or_create_community_profile(person=person)
                _audit_self_service(action=AuditEvent.Action.PERSON_UPDATED, entity_type="CommunityProfile", entity_id=profile.id, changed_fields=[], request=request, created=True)
            changed_community = []
            if "bio" in community_values:
                bio = community_values["bio"].strip()
                if profile.bio != bio:
                    profile.bio = bio
                    changed_community.append("bio")
            for field in ("directory_visible", "email_visible", "mobile_visible"):
                if field in community_values and getattr(profile, field) != community_values[field]:
                    setattr(profile, field, community_values[field])
                    changed_community.append(field)
            if changed_community:
                _save_validated(profile, [*changed_community, "updated_at"])
                _audit_self_service(action=AuditEvent.Action.PERSON_UPDATED, entity_type="CommunityProfile", entity_id=profile.id, changed_fields=changed_community, request=request)

        professional_values = data.get("professional", {})
        if professional_values:
            professional = ProfessionalProfile.objects.select_for_update().filter(person=person).first()
            if professional is None:
                professional = ProfessionalProfile(person=person)
                for field, value in professional_values.items():
                    setattr(professional, field, value.strip() if isinstance(value, str) else value)
                _save_validated(professional)
                _audit_self_service(action=AuditEvent.Action.PROFESSIONAL_PROFILE_CREATED, entity_type="ProfessionalProfile", entity_id=professional.id, changed_fields=[], request=request, created=True)
            else:
                changed_professional = []
                for field, value in professional_values.items():
                    value = value.strip() if isinstance(value, str) else value
                    if getattr(professional, field) != value:
                        setattr(professional, field, value)
                        changed_professional.append(field)
                if changed_professional:
                    _save_validated(professional, [*changed_professional, "updated_at"])
                    _audit_self_service(action=AuditEvent.Action.PROFESSIONAL_PROFILE_UPDATED, entity_type="ProfessionalProfile", entity_id=professional.id, changed_fields=changed_professional, request=request)

        for field, model, relation in (("skills", Skill, PersonSkill), ("interests", Interest, PersonInterest)):
            if field not in data:
                continue
            existing_ids = set(relation.objects.filter(person=person).values_list(f"{field[:-1]}_id", flat=True))
            submitted_ids = {item.id for item in data[field]}
            relation.objects.filter(person=person).delete()
            for item in data[field]:
                relation.objects.create(person=person, **{field[:-1]: item})
            assigned_action = AuditEvent.Action.SKILL_ASSIGNED if field == "skills" else AuditEvent.Action.INTEREST_ASSIGNED
            removed_action = AuditEvent.Action.SKILL_REMOVED if field == "skills" else AuditEvent.Action.INTEREST_REMOVED
            for item_id in sorted(existing_ids - submitted_ids):
                _audit_self_service(action=removed_action, entity_type=model.__name__, entity_id=item_id, changed_fields=["person_id"], request=request)
            for item_id in sorted(submitted_ids - existing_ids):
                _audit_self_service(action=assigned_action, entity_type=model.__name__, entity_id=item_id, changed_fields=["person_id"], request=request)

        if person_audit_event is not None and set(changed_person).intersection({"first_name", "last_name", "mobile"}):
            enqueue_coalesced_person_sync_job(
                person=person,
                provider=BREVO_PROVIDER,
                job_type=PERSON_PROFILE_SYNC,
                source_event_id=person_audit_event.id,
            )

        person = Person.objects.select_related("professional_profile", "professional_profile__industry", "membership", "community_profile").prefetch_related(
            Prefetch("person_skills", queryset=PersonSkill.objects.filter(skill__is_active=True).select_related("skill"), to_attr="community_profile_skills"),
            Prefetch("person_interests", queryset=PersonInterest.objects.filter(interest__is_active=True).select_related("interest"), to_attr="community_profile_interests"),
        ).get(pk=person.pk)
        return build_community_profile_projection(
            person=person,
            community_profile=CommunityProfile.objects.filter(person=person).first(),
        )


def community_profile_options():
    return {
        "industries": [{"slug": item.slug, "label": item.name} for item in Industry.objects.filter(is_active=True)],
        "career_stages": [{"slug": value, "label": label} for value, label in ProfessionalProfile.CareerStage.choices],
        "skills": [{"slug": item.slug, "label": item.name} for item in Skill.objects.filter(is_active=True)],
        "interests": [{"slug": item.slug, "label": item.name} for item in Interest.objects.filter(is_active=True)],
    }


def acknowledge_community_profile_review(*, person_id, request):
    with transaction.atomic():
        profile = CommunityProfile.objects.select_for_update().filter(person_id=person_id).first()
        if profile is None:
            profile = get_or_create_community_profile(person=Person.objects.get(pk=person_id))
        if profile.review_acknowledged_at is None:
            profile.review_acknowledged_at = timezone.now()
            profile.save(update_fields=["review_acknowledged_at", "updated_at"])
            _audit_self_service(action=AuditEvent.Action.PERSON_UPDATED, entity_type="CommunityProfile", entity_id=profile.id, changed_fields=["review_acknowledged_at"], request=request)
        return profile


def build_community_account_projection(*, person):
    """Return the safe CRM projection for one Person's Community account lifecycle."""
    user = _related_user(person)
    status = get_community_account_status(person=person)
    if user is not None:
        activation_event = AuditEvent.objects.filter(
            action=AuditEvent.Action.COMMUNITY_ACCOUNT_ACTIVATED,
            entity_type="User",
            entity_id=str(user.pk),
            metadata__person_id=str(person.pk),
        ).order_by("occurred_at", "id").first()
        return {
            "status": status,
            "account_email": user.email,
            "setup_email": None,
            "account_created_at": activation_event.occurred_at if activation_event else user.date_joined,
            "last_login_at": user.last_login,
            "invitation_sent_at": None,
            "invitation_expires_at": None,
            "invitation_delivery_status": None,
        }

    invitation = _current_relevant_invitation(person)
    if invitation is None:
        return {
            "status": status,
            "account_email": None,
            "setup_email": None,
            "account_created_at": None,
            "last_login_at": None,
            "invitation_sent_at": None,
            "invitation_expires_at": None,
            "invitation_delivery_status": None,
        }

    job = getattr(invitation, "transactional_email_job", None)
    delivery_status, sent_at = _community_invitation_delivery(job)
    return {
        "status": status,
        "account_email": None,
        "setup_email": invitation.intended_email,
        "account_created_at": None,
        "last_login_at": None,
        "invitation_sent_at": sent_at,
        "invitation_expires_at": invitation.expires_at,
        "invitation_delivery_status": delivery_status,
    }


def get_community_account_status(*, person):
    """Return the authoritative Community lifecycle state for a Person."""
    user = _related_user(person)
    if user is not None:
        membership = _related_membership(person)
        is_eligible = bool(
            user.is_active
            and person.record_type == Person.RecordType.BUSINESS
            and person.archived_at is None
            and membership is not None
            and membership.status == Membership.Status.ACTIVE
        )
        return COMMUNITY_ACCOUNT_ACTIVE if is_eligible else COMMUNITY_ACCOUNT_ACCESS_UNAVAILABLE

    return (
        COMMUNITY_ACCOUNT_SETUP_PENDING
        if _current_relevant_invitation(person) is not None
        else COMMUNITY_ACCOUNT_NOT_SET_UP
    )


def _related_user(person):
    try:
        return person.user
    except get_user_model().DoesNotExist:
        return None


def _related_membership(person):
    try:
        return person.membership
    except Membership.DoesNotExist:
        return None


def _current_relevant_invitation(person):
    if hasattr(person, "current_community_invitations"):
        invitations = person.current_community_invitations
    else:
        invitations = list(
            CommunityAccountInvitation.objects.filter(
                person=person,
                used_at__isnull=True,
                revoked_at__isnull=True,
                superseded_at__isnull=True,
            ).select_related("transactional_email_job")[:1]
        )
    invitation = invitations[0] if invitations else None
    if invitation is None or invitation.expires_at <= timezone.now():
        return None
    if getattr(invitation, "transactional_email_job", None) is not None and invitation.transactional_email_job.status == TransactionalEmailJob.Status.CANCELLED:
        return None
    return invitation


def _community_invitation_delivery(job):
    if job is None:
        return COMMUNITY_INVITATION_NOT_SENT, None
    if job.status == TransactionalEmailJob.Status.SENT and job.sent_at is not None:
        return COMMUNITY_INVITATION_SENT, job.sent_at
    if job.status == TransactionalEmailJob.Status.DELIVERY_UNCERTAIN:
        return COMMUNITY_INVITATION_DELIVERY_UNCERTAIN, None
    if job.status == TransactionalEmailJob.Status.FAILED:
        return COMMUNITY_INVITATION_FAILED, None
    return COMMUNITY_INVITATION_NOT_SENT, None


class CommunityJoinReviewRequired(Exception):
    pass


class CommunityJoinIdempotencyConflict(Exception):
    pass


@dataclass(frozen=True)
class JoinResult:
    replayed: bool = False


def _digest_payload(payload):
    canonical_payload = dict(payload)
    canonical_payload["email"] = normalize_email(canonical_payload["email"])
    canonical_payload["email_marketing_opt_in"] = bool(canonical_payload.get("email_marketing_opt_in", False))
    if "mobile" in canonical_payload:
        canonical_payload["mobile"] = normalize_mobile(canonical_payload["mobile"])
    canonical_payload.pop("phone_region", None)
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _key_hash(idempotency_key):
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def build_community_activation_url(*, invitation, token):
    """Build a URL transiently; neither this URL nor token is persisted here."""
    return f"{settings.COMMUNITY_FRONTEND_URL.rstrip('/')}/activate/{invitation.public_id}/{token}"


def _schedule_account_activation(*, person):
    """Create the durable invitation/job pair without creating a User or calling a provider."""
    user_model = get_user_model()
    user = user_model.objects.select_for_update().filter(person_id=person.pk).first()
    if user is not None:
        # Existing accounts, including inactive and unusable-password accounts,
        # require their own controlled lifecycle and are not mutated by J2.1.
        return None

    now = timezone.now()
    invitation = (
        CommunityAccountInvitation.objects.select_for_update()
        .filter(person=person, used_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True)
        .first()
    )
    if invitation is not None and invitation.expires_at > now:
        job = (
            TransactionalEmailJob.objects.select_for_update()
            .filter(invitation=invitation)
            .first()
        )
        if job is not None and job.status != TransactionalEmailJob.Status.CANCELLED:
            return invitation
        # A cancelled or unexpectedly missing job cannot deliver this
        # invitation. Preserve both records and create a fresh lifecycle pair.
    if invitation is not None:
        invitation.superseded_at = now
        invitation.save(update_fields=["superseded_at", "updated_at"])

    invitation = CommunityAccountInvitation.objects.create(
        person=person,
        intended_email=normalize_email(person.primary_email),
        # The delivery worker will mint and hash a token immediately before
        # sending. J2.1 never creates or persists a raw activation secret.
        token_hash=None,
        expires_at=now + timedelta(hours=settings.COMMUNITY_ACTIVATION_EXPIRY_HOURS),
    )

    TransactionalEmailJob.objects.create(
        invitation=invitation,
        template_id=settings.BREVO_COMMUNITY_ACTIVATION_TEMPLATE_ID,
        recipient_email=invitation.intended_email,
        recipient_name=f"{person.first_name} {person.last_name}".strip(),
        first_name=person.first_name,
        expires_in_hours=settings.COMMUNITY_ACTIVATION_EXPIRY_HOURS,
    )
    return invitation


def _missing(value):
    return value is None or value == ""


def _matching_people(*, email, mobile, phone_region):
    email_candidates = list(
        Person.objects.business().filter(primary_email__iexact=email).select_for_update()
    )
    normalized_mobile = mobile
    mobile_candidates = []
    if normalized_mobile:
        for person in Person.objects.business().exclude(mobile="").exclude(mobile__isnull=True):
            legacy = normalize_phone_for_community(person.mobile, region=phone_region)
            if legacy.status == PhoneNormalizationStatus.NORMALIZED and legacy.e164 == normalized_mobile:
                mobile_candidates.append(person)
        mobile_ids = {person.id for person in mobile_candidates}
        if len(mobile_ids - {person.id for person in email_candidates}) > 0:
            raise CommunityJoinReviewRequired

    if any(person.archived_at is not None for person in email_candidates):
        raise CommunityJoinReviewRequired
    if len(email_candidates) > 1:
        raise CommunityJoinReviewRequired
    if not email_candidates:
        if mobile_candidates:
            raise CommunityJoinReviewRequired
        return None

    person = email_candidates[0]
    if normalized_mobile and person.mobile:
        existing = normalize_phone_for_community(person.mobile, region=phone_region)
        if existing.status == PhoneNormalizationStatus.NORMALIZED and existing.e164 != normalized_mobile:
            raise CommunityJoinReviewRequired
    return person


def _lock_receipt(*, idempotency_key, request_digest):
    if not idempotency_key:
        return None, False
    key_hash = _key_hash(idempotency_key)
    try:
        receipt = JoinSubmissionReceipt.objects.select_for_update().get(key_hash=key_hash)
        if receipt.request_digest != request_digest:
            raise CommunityJoinIdempotencyConflict
        return receipt, True
    except JoinSubmissionReceipt.DoesNotExist:
        try:
            with transaction.atomic():
                receipt = JoinSubmissionReceipt.objects.create(
                    key_hash=key_hash,
                    request_digest=request_digest,
                    status=JoinSubmissionReceipt.Status.ACCEPTED,
                )
        except IntegrityError:
            receipt = JoinSubmissionReceipt.objects.select_for_update().get(key_hash=key_hash)
            if receipt.request_digest != request_digest:
                raise CommunityJoinIdempotencyConflict
            return receipt, True
        return receipt, False


def _save_validated(instance, update_fields=None):
    try:
        instance.full_clean()
    except DjangoValidationError as error:
        raise CommunityJoinReviewRequired from error
    if update_fields:
        instance.save(update_fields=update_fields)
    else:
        instance.save()


def _audit_person(person, *, created, changed_fields, request):
    record_audit_event(
        action=AuditEvent.Action.PERSON_CREATED if created else AuditEvent.Action.PERSON_UPDATED,
        actor_user=None,
        entity_type="Person",
        entity_id=person.id,
        changes={
            "created": {"from": False, "to": True}
        } if created else {field: {"from": None, "to": "submitted"} for field in changed_fields},
        metadata={"source": "COMMUNITY_JOIN", "person_id": str(person.id)},
        request_id=request.headers.get("X-Request-ID"),
        ip_address=request.META.get("REMOTE_ADDR"),
    )


def _audit_profile(profile, *, created, changed_fields, request):
    record_audit_event(
        action=AuditEvent.Action.PROFESSIONAL_PROFILE_CREATED if created else AuditEvent.Action.PROFESSIONAL_PROFILE_UPDATED,
        actor_user=None,
        entity_type="ProfessionalProfile",
        entity_id=profile.id,
        changes={
            "created": {"from": False, "to": True}
        } if created else {field: {"from": None, "to": "submitted"} for field in changed_fields},
        metadata={"source": "COMMUNITY_JOIN", "person_id": str(profile.person_id)},
        request_id=request.headers.get("X-Request-ID"),
        ip_address=request.META.get("REMOTE_ADDR"),
    )


def _audit_membership(membership, request):
    record_audit_event(
        action=AuditEvent.Action.MEMBERSHIP_CREATED,
        actor_user=None,
        entity_type="Membership",
        entity_id=membership.id,
        changes={
            "status": {"from": None, "to": Membership.Status.ACTIVE},
            "joined_at": {"from": None, "to": membership.joined_at.isoformat()},
            "membership_source": {"from": None, "to": Membership.Source.COMMUNITY_PLATFORM},
        },
        metadata={"source": "COMMUNITY_JOIN", "person_id": str(membership.person_id)},
        request_id=request.headers.get("X-Request-ID"),
        ip_address=request.META.get("REMOTE_ADDR"),
    )


def _apply_community_email_opt_in(*, person, requested):
    if not requested:
        return
    preference = get_effective_marketing_preference(person=person)
    if preference.state != MarketingPreference.State.UNKNOWN:
        return
    record_opt_in(
        person=person,
        source=MarketingPreference.Source.COMMUNITY_JOIN,
        actor_user=None,
    )


def submit_community_join(*, data, request, idempotency_key=None):
    request_digest = _digest_payload(data)
    with transaction.atomic():
        receipt, replayed = _lock_receipt(
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replayed:
            return JoinResult(replayed=True)

        industry = Industry.objects.filter(slug=data["industry"], is_active=True).first()
        if industry is None:
            raise CommunityJoinReviewRequired

        canonical_email = normalize_email(data["email"])
        acquire_community_join_email_lock(canonical_email)
        person = _matching_people(
            email=canonical_email,
            mobile=data.get("mobile", ""),
            phone_region=data.get("phone_region", ""),
        )
        person_created = person is None
        changed_person_fields = []
        if person_created:
            person = Person(
                record_type=Person.RecordType.BUSINESS,
                first_name=data["first_name"],
                last_name=data["last_name"],
                primary_email=normalize_email(data["email"]),
                mobile=data.get("mobile", ""),
                location=data["location"],
                age_range=data["age_range"],
                gender=data["gender"],
            )
            _save_validated(person)
        else:
            person = Person.objects.select_for_update().get(pk=person.pk)
            if person.archived_at is not None:
                raise CommunityJoinReviewRequired
            person_values = {
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "mobile": data.get("mobile", ""),
                "location": data["location"],
                "age_range": data["age_range"],
                "gender": data["gender"],
            }
            for field, submitted in person_values.items():
                if _missing(getattr(person, field)) and not _missing(submitted):
                    setattr(person, field, submitted)
                    changed_person_fields.append(field)
            if changed_person_fields:
                _save_validated(person, [*changed_person_fields, "updated_at"])

        if person_created or changed_person_fields:
            _audit_person(person, created=person_created, changed_fields=changed_person_fields, request=request)

        get_or_create_community_profile(
            person=person,
            person_preexisted_community=not person_created,
        )

        membership = Membership.objects.select_for_update().filter(person=person).first()
        if membership is not None and membership.status == Membership.Status.FORMER:
            raise CommunityJoinReviewRequired
        if membership is None:
            membership = Membership(
                person=person,
                status=Membership.Status.ACTIVE,
                joined_at=timezone.localdate(),
                membership_source=Membership.Source.COMMUNITY_PLATFORM,
            )
            _save_validated(membership)
            _audit_membership(membership, request)

        profile = ProfessionalProfile.objects.select_for_update().filter(person=person).first()
        profile_values = {
            "job_title": data["job_title"],
            "industry": industry,
            "linkedin_url": data.get("linkedin_url", ""),
        }
        profile_created = profile is None
        changed_profile_fields = []
        if profile_created:
            profile = ProfessionalProfile(person=person, **profile_values)
            _save_validated(profile)
        else:
            for field, submitted in profile_values.items():
                if _missing(getattr(profile, field)) and not _missing(submitted):
                    setattr(profile, field, submitted)
                    changed_profile_fields.append(field)
            if changed_profile_fields:
                _save_validated(profile, [*changed_profile_fields, "updated_at"])
        if profile_created or changed_profile_fields:
            _audit_profile(profile, created=profile_created, changed_fields=changed_profile_fields, request=request)

        _apply_community_email_opt_in(
            person=person,
            requested=data.get("email_marketing_opt_in", False),
        )
        _schedule_account_activation(person=person)

    return JoinResult(replayed=False)
