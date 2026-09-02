from dataclasses import dataclass
from enum import Enum

from django.db.models import Q

from people.models import Person


class CreateNewIdentityCollision(str, Enum):
    NO_BLOCKING_COLLISION = "NO_BLOCKING_COLLISION"
    MOBILE_COLLISION = "MOBILE_COLLISION"
    EMAIL_COLLISION = "EMAIL_COLLISION"
    EMAIL_AND_MOBILE_COLLISION = "EMAIL_AND_MOBILE_COLLISION"


@dataclass(frozen=True)
class CreateNewIdentityPolicy:
    collision: CreateNewIdentityCollision
    matched_person_ids: tuple[int, ...]
    requires_review: bool
    requires_strong_confirmation: bool
    is_safe_to_create: bool

    def review_evidence(self) -> dict:
        return {
            "collision": self.collision.value,
            "matched_person_ids": list(self.matched_person_ids),
        }

    def matches_reviewed_evidence(self, evidence) -> bool:
        if not isinstance(evidence, dict):
            return False
        person_ids = evidence.get("matched_person_ids")
        return (
            evidence.get("collision") == self.collision.value
            and isinstance(person_ids, list)
            and tuple(sorted(person_id for person_id in person_ids if isinstance(person_id, int))) == self.matched_person_ids
            and len(person_ids) == len(self.matched_person_ids)
        )


def normalize_email(value):
    return value.strip().lower() if value else ""


def normalize_mobile(value):
    """Conservatively remove common visual separators without assuming a phone plan."""
    if not value:
        return ""
    return "".join(character for character in value.strip() if character not in " -()")


def find_business_duplicate_people(*, primary_email="", mobile="", exclude_person_id=None):
    """Return BUSINESS candidates matching the CRM's exact normalized identity signals."""
    normalized_email = normalize_email(primary_email)
    normalized_mobile = normalize_mobile(mobile)
    if not normalized_email and not normalized_mobile:
        return []

    queryset = Person.objects.business()
    if exclude_person_id is not None:
        queryset = queryset.exclude(pk=exclude_person_id)

    email_query = Q()
    if normalized_email:
        email_query = Q(primary_email__iexact=normalized_email)

    # Mobile formatting is not canonical in V1. Compare conservative normalized
    # values in application code so historical formatting remains supported.
    candidates = list(queryset.filter(email_query) if normalized_email else [])
    if normalized_mobile:
        mobile_candidates = queryset.exclude(mobile="").exclude(mobile__isnull=True)
        candidates.extend(
            person for person in mobile_candidates if normalize_mobile(person.mobile) == normalized_mobile
        )

    return list({person.id: person for person in candidates}.values())


def evaluate_create_new_identity(
    *,
    primary_email="",
    mobile="",
    staff_confirmed_different=False,
    confirm_identity_override=False,
):
    """Apply the CRM identity policy for a proposed separate BUSINESS Person."""
    normalized_email = normalize_email(primary_email)
    normalized_mobile = normalize_mobile(mobile)
    queryset = Person.objects.business()

    email_matches = list(queryset.filter(primary_email__iexact=normalized_email)) if normalized_email else []
    mobile_matches = []
    if normalized_mobile:
        mobile_matches = [
            person
            for person in queryset.exclude(mobile="").exclude(mobile__isnull=True)
            if normalize_mobile(person.mobile) == normalized_mobile
        ]

    if email_matches and mobile_matches:
        collision = CreateNewIdentityCollision.EMAIL_AND_MOBILE_COLLISION
    elif email_matches:
        collision = CreateNewIdentityCollision.EMAIL_COLLISION
    elif mobile_matches:
        collision = CreateNewIdentityCollision.MOBILE_COLLISION
    else:
        collision = CreateNewIdentityCollision.NO_BLOCKING_COLLISION

    requires_review = collision != CreateNewIdentityCollision.NO_BLOCKING_COLLISION
    requires_strong_confirmation = collision in (
        CreateNewIdentityCollision.EMAIL_COLLISION,
        CreateNewIdentityCollision.EMAIL_AND_MOBILE_COLLISION,
    )
    return CreateNewIdentityPolicy(
        collision=collision,
        matched_person_ids=tuple(sorted({person.id for person in [*email_matches, *mobile_matches]})),
        requires_review=requires_review,
        requires_strong_confirmation=requires_strong_confirmation,
        is_safe_to_create=(not requires_review) or (
            staff_confirmed_different and (not requires_strong_confirmation or confirm_identity_override)
        ),
    )


def reviewed_create_new_identity_evidence(match_candidates) -> dict | None:
    """Derive safe legacy review evidence from analyzer snapshots when needed."""
    email_ids = set()
    mobile_ids = set()
    for candidate in match_candidates or []:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("person_id"), int):
            continue
        if "email" in candidate.get("matched_on", []):
            email_ids.add(candidate["person_id"])
        if "mobile" in candidate.get("matched_on", []):
            mobile_ids.add(candidate["person_id"])
    if email_ids and mobile_ids:
        collision = CreateNewIdentityCollision.EMAIL_AND_MOBILE_COLLISION
    elif email_ids:
        collision = CreateNewIdentityCollision.EMAIL_COLLISION
    elif mobile_ids:
        collision = CreateNewIdentityCollision.MOBILE_COLLISION
    else:
        return None
    return {"collision": collision.value, "matched_person_ids": sorted(email_ids | mobile_ids)}
