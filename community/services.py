import hashlib
import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from memberships.models import Membership
from marketing_preferences.models import MarketingPreference
from marketing_preferences.services import get_effective_marketing_preference, record_opt_in
from people.models import Person
from people.services import normalize_email, normalize_mobile, normalize_phone_for_community, PhoneNormalizationStatus
from professional_profiles.models import Industry, ProfessionalProfile

from community.models import JoinSubmissionReceipt


PUBLIC_REVIEW_CODE = "SUBMISSION_REQUIRES_REVIEW"


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

        person = _matching_people(
            email=normalize_email(data["email"]),
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

    return JoinResult(replayed=False)
