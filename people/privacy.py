import re

from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.models import CapabilityDefinition
from staff_access.permissions import get_effective_capability_codes_for_user


CONTACT_FIELDS = frozenset(("primary_email", "mobile"))
DEMOGRAPHIC_FIELDS = frozenset(("age_range", "gender"))


def record_person_privacy_disclosure(*, request, person, capabilities, operation="PERSON_DETAIL"):
    """Persist a safe, deliberate protected-data disclosure audit before response serialization."""
    disclosed = frozenset(capabilities).intersection({
        CapabilityDefinition.PERSON_CONTACT_READ,
        CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
    })
    if not disclosed:
        return None
    categories = []
    if CapabilityDefinition.PERSON_CONTACT_READ in disclosed:
        categories.append("CONTACT")
    if CapabilityDefinition.PERSON_DEMOGRAPHIC_READ in disclosed:
        categories.append("DEMOGRAPHIC")
    return record_audit_event(
        action=AuditEvent.Action.PRIVACY_PERSON_DISCLOSED,
        actor_user=getattr(request, "user", None),
        entity_type="Person",
        entity_id=person.pk,
        metadata={
            "event_type": "PRIVACY_PERSON_DISCLOSED",
            "operation": str(operation),
            "disclosure_category": "_AND_".join(categories),
            "capability_codes": sorted(disclosed),
            "outcome": "SUCCESS",
            "superuser_authority": bool(getattr(getattr(request, "user", None), "is_superuser", False)),
        },
        audit_domain=AuditEvent.Domain.PRIVACY,
        subject_person=person,
        retention_class="PRIVACY_AUDIT",
    )


def person_privacy_capabilities(*, request):
    """Return the approved Person privacy capabilities for one request."""
    if request is None:
        return frozenset()
    cached = getattr(request, "_person_privacy_capabilities", None)
    if cached is None:
        cached = frozenset(get_effective_capability_codes_for_user(
            getattr(request, "user", None),
            requested_codes={
                CapabilityDefinition.PERSON_CONTACT_READ,
                CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
            },
            request=request,
        ))
        request._person_privacy_capabilities = cached
    return cached


def mask_email(value):
    if not value:
        return None
    value = str(value).strip()
    local, separator, domain = value.rpartition("@")
    if not separator or not local or not domain or any(char.isspace() for char in value):
        return "Restricted"
    return f"{local[0]}***@{domain}"


def mask_mobile(value):
    if not value:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 3:
        return "Restricted"
    return f"******{digits[-2:]}"


def project_person_privacy(*, person, capabilities):
    contact_allowed = CapabilityDefinition.PERSON_CONTACT_READ in capabilities
    demographic_allowed = CapabilityDefinition.PERSON_DEMOGRAPHIC_READ in capabilities
    return {
        "primary_email": person.primary_email if contact_allowed else mask_email(person.primary_email),
        "mobile": person.mobile if contact_allowed else mask_mobile(person.mobile),
        "age_range": person.age_range if demographic_allowed else "Restricted",
        "gender": person.gender if demographic_allowed else "Restricted",
        "privacy": {
            "contact_restricted": not contact_allowed,
            "demographic_restricted": not demographic_allowed,
        },
    }


def project_account_contact_fields(*, projection, capabilities):
    if CapabilityDefinition.PERSON_CONTACT_READ in capabilities:
        return projection
    result = dict(projection)
    result["account_email"] = mask_email(result.get("account_email"))
    result["setup_email"] = mask_email(result.get("setup_email"))
    return result


def validate_person_write_privacy(*, initial_data, capabilities):
    errors = {}
    if any(field in initial_data for field in CONTACT_FIELDS) and CapabilityDefinition.PERSON_CONTACT_READ not in capabilities:
        for field in CONTACT_FIELDS:
            if field in initial_data:
                errors[field] = "PERSON_CONTACT_READ is required to submit contact fields."
    if any(field in initial_data for field in DEMOGRAPHIC_FIELDS) and CapabilityDefinition.PERSON_DEMOGRAPHIC_READ not in capabilities:
        for field in DEMOGRAPHIC_FIELDS:
            if field in initial_data:
                errors[field] = "PERSON_DEMOGRAPHIC_READ is required to submit demographic fields."
    return errors
