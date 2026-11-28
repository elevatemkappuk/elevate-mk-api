from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from accounts.models import User
from people.models import Person
from people.privacy import mask_email, person_privacy_capabilities, project_person_privacy
from people.services import evaluate_create_new_identity
from staff_access.models import CapabilityDefinition, StaffInvitation, StaffRole, StaffRoleAssignment
from staff_access.permissions import user_has_capability
from audit.models import AuditEvent
from audit.services import record_audit_event


def _email_for_requester(email, request):
    if not email:
        return None
    if user_has_capability(request.user, CapabilityDefinition.PERSON_CONTACT_READ, request=request):
        return email
    return mask_email(email)


def _active_role(user):
    if user is None:
        return None
    return StaffRoleAssignment.objects.active().filter(
        user=user, role__family=StaffRole.CRM_ROLE_FAMILY,
    ).values_list("role__code", flat=True).first()


def _account_state(user):
    if user is None:
        return "NOT_PROVISIONED"
    if user.account_lifecycle_state == User.AccountLifecycleState.DISABLED:
        return "DISABLED"
    if user.account_lifecycle_state == User.AccountLifecycleState.PENDING_ACTIVATION:
        return "PENDING_ACTIVATION"
    return "ACTIVE" if user.is_active else "INACTIVE"


def person_candidate_projection(person, request):
    user = getattr(person, "user", None)
    active_role = _active_role(user)
    invitation = None
    if user is not None:
        invitation = StaffInvitation.objects.filter(
            intended_email=user.email,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            superseded_at__isnull=True,
        ).order_by("-created_at").first()
    if user is not None and active_role:
        eligibility = "ACTIVE_STAFF"
    elif user is not None and user.account_lifecycle_state == User.AccountLifecycleState.DISABLED:
        eligibility = "DISABLED_ACCOUNT"
    elif person.record_type == Person.RecordType.STAFF and user is not None:
        eligibility = "FORMER_STAFF" if not active_role else "ACTIVE_STAFF"
    elif invitation is not None:
        eligibility = "PENDING_INVITATION"
    else:
        eligibility = "ELIGIBLE_FOR_VALIDATION"
    projection = project_person_privacy(person=person, capabilities=person_privacy_capabilities(request=request))
    return {
        "person_id": person.pk,
        "display_name": f"{person.first_name} {person.last_name}".strip(),
        "primary_email": projection["primary_email"],
        "account_state": _account_state(user),
        "staff_role": active_role,
        "onboarding_eligibility": eligibility,
    }


def search_onboarding_people(*, query, request):
    contact_allowed = user_has_capability(
        request.user, CapabilityDefinition.PERSON_CONTACT_READ, request=request,
    )
    name_query = Q(first_name__icontains=query) | Q(last_name__icontains=query)
    if contact_allowed:
        name_query |= Q(primary_email__icontains=query)
    people = Person.objects.filter(
        name_query,
        archived_at__isnull=True,
        record_type__in=(Person.RecordType.COMMUNITY, Person.RecordType.STAFF),
    ).select_related("user").order_by("last_name", "first_name", "pk")[:11]
    rows = list(people)
    return {
        "results": [person_candidate_projection(person, request) for person in rows[:10]],
        "has_more": len(rows) > 10,
    }


def _conflict(code, detail, *, matches=None):
    payload = {"code": code, "detail": detail}
    if matches is not None:
        payload["matches"] = matches
    return payload


def _existing_person_result(person, *, role_code, login_email, request):
    user = getattr(person, "user", None)
    active_role = _active_role(user)
    if user is not None and active_role:
        return _conflict("ACTIVE_STAFF", "This Person already has active CRM staff access.")
    if user is not None and user.account_lifecycle_state == User.AccountLifecycleState.DISABLED:
        return _conflict("DISABLED_ACCOUNT", "This account is disabled and cannot be silently enabled through onboarding.")
    if person.record_type != Person.RecordType.STAFF:
        return _conflict("PERSON_CLASSIFICATION_REQUIRED", "This Person is not classified as Staff; onboarding cannot change Person classification automatically.")
    if user is not None and not user.is_active:
        return _conflict("FORMER_STAFF", "This Person has a former staff relationship and must use reactivation.")
    if user is not None and user.account_lifecycle_state == User.AccountLifecycleState.PENDING_ACTIVATION:
        account_resolution = "PENDING_ACTIVATION"
    elif user is not None:
        account_resolution = "EXISTING_ACTIVE_ACCOUNT"
    else:
        if not str(login_email or "").strip():
            return _conflict("LOGIN_EMAIL_REQUIRED", "A login email is required when preparing a new account.")
        account_resolution = "NEW_PENDING_ACCOUNT"
    return {
        "eligible": True,
        "person": person_candidate_projection(person, request),
        "user": {
            "user_id": user.pk if user else None,
            "account_resolution": account_resolution,
            "login_email": _email_for_requester(user.email, request) if user else None,
            "credentials_preserved": bool(user),
        },
        "requested_role": role_code,
        "administrator_approval_required": role_code == StaffRole.CRM_ADMIN,
        "submission_available": False,
        "unavailable_reason": "Secure invitation issuance is a separate authorized lifecycle operation.",
    }


def validate_onboarding_request(*, attrs, request):
    role_code = attrs["role_code"]
    role = StaffRole.objects.filter(
        code=role_code, family=StaffRole.CRM_ROLE_FAMILY, is_active=True,
    ).first()
    if role is None:
        return {"eligible": False, "conflict": _conflict("ROLE_UNAVAILABLE", "The requested CRM role is unavailable.")}

    person = None
    if attrs.get("person_id"):
        person = Person.objects.select_related("user").filter(
            pk=attrs["person_id"], archived_at__isnull=True,
        ).first()
        if person is None:
            return {"eligible": False, "conflict": _conflict("PERSON_NOT_FOUND", "The selected Person is not available for onboarding.")}
        result = _existing_person_result(person, role_code=role_code, login_email=attrs.get("login_email"), request=request)
        if result.get("eligible") is not True:
            return {"eligible": False, "conflict": result}
        return result

    login_email = str(attrs.get("login_email") or "").strip().lower()
    if not login_email:
        return {"eligible": False, "conflict": _conflict("LOGIN_EMAIL_REQUIRED", "A login email is required when preparing a new account.")}
    existing_user = User.objects.select_related("person").filter(email=login_email).first()
    if existing_user is not None:
        return {"eligible": False, "conflict": _conflict("LOGIN_EMAIL_CONFLICT", "This login email is already associated with an account.")}
    identity_policy = evaluate_create_new_identity(
        primary_email=attrs.get("primary_email") or "",
        mobile=attrs.get("mobile") or "",
    )
    if identity_policy.requires_review:
        matches = [
            {"person_id": person_id, "match_reasons": list(identity_policy.match_reasons)}
            for person_id in identity_policy.matched_person_ids
        ]
        return {"eligible": False, "conflict": _conflict("IDENTITY_COLLISION", "A possible existing Person matches the supplied identity details.", matches=matches)}
    return {
        "eligible": True,
        "person": {
            "person_id": None,
            "display_name": f"{attrs['first_name']} {attrs['last_name']}".strip(),
            "primary_email": _email_for_requester(attrs.get("primary_email"), request),
            "account_state": "NOT_PROVISIONED",
            "staff_role": None,
            "onboarding_eligibility": "ELIGIBLE_FOR_VALIDATION",
        },
        "user": {
            "user_id": None,
            "account_resolution": "NEW_PENDING_ACCOUNT",
            "login_email": _email_for_requester(login_email, request),
            "credentials_preserved": False,
        },
        "requested_role": role_code,
        "administrator_approval_required": role_code == StaffRole.CRM_ADMIN,
        "submission_available": False,
        "unavailable_reason": "Secure invitation issuance is a separate authorized lifecycle operation; no Person or User is created during validation.",
    }


def submit_onboarding_request(*, attrs, actor_user, request=None):
    """Persist a Manager/Viewer onboarding request and its delivery obligation."""
    from django.conf import settings
    from staff_access.authorization import StaffOperation, authorize_staff_operation
    from staff_access.services import _queue_staff_invitation_email, _normalize_invitation_email

    authorize_staff_operation(actor=actor_user, operation=StaffOperation.ACCESS, request=request)
    role_code = attrs["role_code"]
    if role_code == StaffRole.CRM_ADMIN:
        raise ValidationError({"role_code": "Administrator onboarding requires the separately approved governance workflow."})
    reason = str(attrs.get("reason") or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for staff onboarding."})
    login_email = _normalize_invitation_email(attrs.get("login_email") or attrs.get("primary_email"))
    if not login_email:
        raise ValidationError({"login_email": "A login email is required."})
    with transaction.atomic():
        role = StaffRole.objects.select_for_update().filter(code=role_code, family=StaffRole.CRM_ROLE_FAMILY, is_active=True).first()
        if role is None:
            raise ValidationError({"role_code": "The requested role is unavailable."})
        person = Person.objects.select_for_update().filter(pk=attrs.get("person_id"), archived_at__isnull=True).first() if attrs.get("person_id") else None
        if attrs.get("person_id") and person is None:
            raise ValidationError({"person_id": "The selected Person is not available."})
        if person is None:
            existing_identity = evaluate_create_new_identity(primary_email=attrs.get("primary_email") or login_email, mobile=attrs.get("mobile") or "")
            if existing_identity.requires_review:
                raise ValidationError({"identity": "A possible duplicate Person requires review before onboarding."})
            person = Person.objects.create(
                record_type=Person.RecordType.STAFF,
                first_name=attrs["first_name"].strip(),
                last_name=attrs["last_name"].strip(),
                primary_email=attrs.get("primary_email") or login_email,
                mobile=attrs.get("mobile") or "",
                location=attrs.get("location") or "",
            )
        elif person.record_type != Person.RecordType.STAFF:
            raise ValidationError({"person_id": "The selected Person is not classified as Staff."})
        user = User.objects.select_for_update().filter(person=person).first()
        email_user = User.objects.select_for_update().filter(email=login_email).first()
        if email_user is not None and (user is None or email_user.pk != user.pk):
            raise ValidationError({"login_email": "This login email is already associated with another account."})
        if user is not None and _active_role(user):
            raise ValidationError({"person_id": "This Person already has active CRM staff access."})
        if user is not None and user.account_lifecycle_state == User.AccountLifecycleState.DISABLED:
            raise ValidationError({"person_id": "A disabled account cannot be silently enabled."})
        if user is None:
            user = User.objects.create_user(
                email=login_email, password=None, person=person, is_active=False,
                account_lifecycle_state=User.AccountLifecycleState.PENDING_ACTIVATION,
            )
            user.set_unusable_password()
            user.save(update_fields=["password", "is_active", "account_lifecycle_state"])
        current = StaffInvitation.objects.select_for_update().filter(
            target_user=user, accepted_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True,
        ).first()
        if current and current.expires_at > timezone.now():
            raise ValidationError({"invitation": "A current invitation already exists for this account."})
        StaffInvitation.objects.filter(target_user=user, accepted_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True).update(superseded_at=timezone.now())
        invitation = StaffInvitation.objects.create(
            intended_email=user.email,
            first_name=person.first_name,
            last_name=person.last_name,
            pending_role_codes=[role.code],
            target_user=user,
            operation="ONBOARD",
            reason=reason,
            expires_at=timezone.now() + timedelta(hours=settings.STAFF_INVITATION_EXPIRY_HOURS),
            created_by=actor_user,
        )
        _queue_staff_invitation_email(invitation)
        record_audit_event(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            actor_user=actor_user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"event_type": "STAFF_ONBOARDING_SUBMITTED", "invitation_id": str(invitation.public_id), "requested_role": role.code, "reason": reason},
            request_id=(request.headers.get("X-Request-ID") if request else None),
        )
    return invitation
