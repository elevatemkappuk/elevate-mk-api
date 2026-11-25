from django.db.models import Q
import hashlib

from audit.models import AuditEvent
from staff_access.models import StaffRole
from staff_access.permissions import get_active_staff_role_codes_for_user, user_has_any_active_staff_role
from staff_access.models import CapabilityDefinition
from staff_access.permissions import user_has_capability

PERSON_AUDIT_RESTRICTED_ENTITY_TYPES = frozenset({"InternalNote"})
PERSON_AUDIT_RESTRICTED_ACTIONS = frozenset(
    {
        AuditEvent.Action.NOTE_CREATED,
        AuditEvent.Action.NOTE_UPDATED,
        AuditEvent.Action.NOTE_ARCHIVED,
        AuditEvent.Action.NOTE_RESTORED,
    }
)
PERSON_AUDIT_SENSITIVE_DOMAIN_ROLE_CODES = frozenset(
    {
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    }
)
PERSON_AUDIT_DESCRIPTION_OVERRIDES = {
    AuditEvent.Action.PERSON_CREATED: "Person created",
    AuditEvent.Action.PERSON_UPDATED: "Person updated",
    AuditEvent.Action.PERSON_ARCHIVED: "Person archived",
    AuditEvent.Action.PERSON_RESTORED: "Person restored",
    AuditEvent.Action.MEMBERSHIP_CREATED: "Membership created",
    AuditEvent.Action.MEMBERSHIP_ENDED: "Membership ended",
    AuditEvent.Action.PROFESSIONAL_PROFILE_CREATED: "Professional profile created",
    AuditEvent.Action.PROFESSIONAL_PROFILE_UPDATED: "Professional profile updated",
    AuditEvent.Action.SKILL_ASSIGNED: "Skill assigned",
    AuditEvent.Action.SKILL_REMOVED: "Skill removed",
    AuditEvent.Action.INTEREST_ASSIGNED: "Interest assigned",
    AuditEvent.Action.INTEREST_REMOVED: "Interest removed",
    AuditEvent.Action.TAG_ASSIGNED: "Tag assigned",
    AuditEvent.Action.TAG_REACTIVATED: "Tag reactivated",
    AuditEvent.Action.TAG_REMOVED: "Tag removed",
    AuditEvent.Action.NOTE_CREATED: "Internal note created",
    AuditEvent.Action.NOTE_UPDATED: "Internal note updated",
    AuditEvent.Action.NOTE_ARCHIVED: "Internal note archived",
    AuditEvent.Action.NOTE_RESTORED: "Internal note restored",
    AuditEvent.Action.MARKETING_PREFERENCE_OPTED_IN: "Marketing preference opted in",
    AuditEvent.Action.MARKETING_PREFERENCE_OPTED_OUT: "Marketing preference opted out",
}
PERSON_AUDIT_ALLOWED_CHANGE_FIELDS_BY_ENTITY_TYPE = {
    "Membership": frozenset({"status", "joined_at", "ended_at", "membership_source"}),
    "ProfessionalProfile": frozenset({"job_title", "company", "industry_id", "career_stage", "linkedin_url"}),
    "PersonSkill": frozenset({"assigned"}),
    "PersonInterest": frozenset({"assigned"}),
    "PersonTag": frozenset({"is_active"}),
    "InternalNote": frozenset({"created", "archived"}),
    "Person": frozenset({"created", "archived", "first_name", "last_name", "primary_email", "mobile", "location", "age_range", "gender"}),
    "MarketingPreference": frozenset({"state", "source"}),
}


def build_person_audit_scope_q(person_id):
    normalized_person_id = str(person_id)
    return Q(subjects__subject_type="PERSON", subjects__person_id=normalized_person_id) | Q(
        metadata__person_id=normalized_person_id
    ) | Q(
        entity_type="Person",
        entity_id=normalized_person_id,
    )


def filter_person_audit_visibility_for_user(queryset, user):
    if user_can_view_restricted_person_audit_domains(user):
        return queryset
    return queryset.exclude(build_restricted_person_audit_q())


def user_can_view_restricted_person_audit_domains(user):
    role_codes = set(get_active_staff_role_codes_for_user(user))
    return bool(role_codes.intersection(PERSON_AUDIT_SENSITIVE_DOMAIN_ROLE_CODES))


def build_restricted_person_audit_q():
    return Q(entity_type__in=PERSON_AUDIT_RESTRICTED_ENTITY_TYPES) | Q(
        action__in=PERSON_AUDIT_RESTRICTED_ACTIONS
    )


def get_person_audit_description(action):
    if action in PERSON_AUDIT_DESCRIPTION_OVERRIDES:
        return PERSON_AUDIT_DESCRIPTION_OVERRIDES[action]

    try:
        return AuditEvent.Action(action).label
    except ValueError:
        return action.replace("_", " ").strip().capitalize()


def project_person_audit_changes(event):
    if not isinstance(event.changes, dict):
        return {}

    allowed_fields = PERSON_AUDIT_ALLOWED_CHANGE_FIELDS_BY_ENTITY_TYPE.get(event.entity_type, frozenset())
    projected_changes = {}

    for key, value in event.changes.items():
        if key not in allowed_fields:
            continue

        projected_value = project_safe_change_value(value)
        if projected_value is not None:
            projected_changes[key] = projected_value

    return projected_changes


def project_safe_change_value(value):
    if not isinstance(value, dict):
        return None

    keys = set(value.keys())
    if keys.issubset({"from", "to"}):
        projected = {}
        if "from" in value and is_safe_change_scalar(value["from"]):
            projected["from"] = value["from"]
        if "to" in value and is_safe_change_scalar(value["to"]):
            projected["to"] = value["to"]
        return projected or None

    if keys == {"changed"} and isinstance(value.get("changed"), bool):
        return {"changed": value["changed"]}

    return None


def get_privacy_audit_queryset_for_user(user, *, person):
    """Return a privacy projection after capability and Person-scope checks."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return AuditEvent.objects.none()
    if not user_has_capability(user, CapabilityDefinition.PRIVACY_AUDIT_READ):
        return AuditEvent.objects.none()
    if person is None or getattr(person, "record_type", None) not in {"COMMUNITY", "STAFF"}:
        return AuditEvent.objects.none()
    if getattr(person, "record_type", None) != "COMMUNITY" and not user_has_any_active_staff_role(
        user, (StaffRole.CRM_ADMIN,)
    ):
        return AuditEvent.objects.none()
    return AuditEvent.objects.privacy().select_related("actor_user").only(
        "id",
        "action",
        "entity_type",
        "entity_id",
        "actor_user",
        "actor_user__email",
        "occurred_at",
        "changes",
    ).filter(build_person_audit_scope_q(person.pk)).order_by("-occurred_at", "-id")


def is_safe_change_scalar(value):
    return value is None or isinstance(value, (bool, int, float, str))


def record_privacy_authorization_failure(*, request, denial_code, person_id=None, operation="PRIVACY_READ"):
    """Persist a safe privacy denial without recording request data or identifiers."""
    from audit.services import record_audit_event

    actor = getattr(request, "user", None)
    if not getattr(actor, "is_authenticated", False):
        actor = None
    target = "privacy"
    if person_id is not None:
        target = hashlib.sha256(f"privacy-person:{person_id}".encode()).hexdigest()[:24]
    try:
        record_audit_event(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            actor_user=actor,
            entity_type="PrivacyAuthorization",
            entity_id=target,
            metadata={
                "event_type": "PRIVACY_AUTHORIZATION_DENIED",
                "denial_code": str(denial_code),
                "operation": str(operation),
                "outcome": "DENIED",
            },
            audit_domain=AuditEvent.Domain.PRIVACY,
        )
    except Exception:
        return False
    return True
