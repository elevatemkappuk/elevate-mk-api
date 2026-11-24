# Elevate MK Authorization
Status: Living Documentation
Last Updated: 2026-11-24

## Scope
This document describes the currently implemented staff authorization foundation for the Elevate MK Django API.

## Current Authorization Architecture
Authentication and staff authorization are separate concerns:

- `accounts.User` identifies the authenticated account
- `staff_access.StaffRole` defines canonical operational role codes
- `staff_access.StaffRoleAssignment` grants operational roles to users

Current backend rule:

- staff authorization is evaluated from active `StaffRoleAssignment` rows whose linked `StaffRole` is also active
- `Person.record_type` does not determine staff authorization

## Canonical V1 Roles
Currently implemented canonical roles:

- `CRM_ADMIN`
- `CRM_MANAGER`
- `CRM_VIEWER`

These are seeded deterministically by migration and are intended to be stable machine-readable backend role codes.

## Active Role Evaluation
A role code currently counts as active for a user only when all of the following are true:

- the user is authenticated
- the assignment row has `is_active=True`
- `revoked_at` is null
- the linked `StaffRole` has `is_active=True`

Returned role codes are sorted deterministically by `StaffRole.code`.

## Revocation and Reactivation
Current lifecycle behavior:

- revocation sets `is_active=False`
- revocation records `revoked_at`
- revocation may record `revoked_by`
- reactivation reuses the same assignment row
- reactivation clears revocation fields and restores `is_active=True`
- successful Django Admin grant, reactivation, and revocation also append immutable `AuditEvent` history rows

### CRM Administrator Continuity

At least one active operational `CRM_ADMIN` assignment must remain at all times.

- an operational CRM administrator has an active, unrevoked `StaffRoleAssignment` linked to the active `CRM_ADMIN` `StaffRole`
- Django `is_staff` and `is_superuser` do not satisfy this requirement
- revoking the final active `CRM_ADMIN` assignment is rejected
- self-revocation is permitted only when another active operational `CRM_ADMIN` assignment remains
- the canonical `CRM_ADMIN` role definition cannot be deactivated
- supported Staff Access revoke operations are serialized so concurrent revocations cannot remove every operational administrator
- `actor_user` in those audit rows is the administrator performing the mutation, not the target user receiving or losing access

No hard-delete lifecycle is implemented as the normal authorization path.

## 401 vs 403
Current permission semantics:

- anonymous user: `401 Unauthorized` when endpoint authentication is required
- authenticated user without a qualifying active staff role: `403 Forbidden`

## Permission Foundation
Currently implemented reusable backend helpers:

- `get_active_staff_role_codes_for_user(user)`
- `user_has_any_active_staff_role(user, allowed_role_codes=None)`
- `HasAnyActiveStaffRole`
- `HasActiveStaffRoleCodes`

These are intended to support future Staff CRM endpoints without introducing a large authorization framework.

Example future usage shape:

- any active staff role
- manager-or-admin role gate
- admin-only role gate

## /me/ Exposure
Current behavior:

- `GET /api/v1/auth/me/` includes `staff_roles`
- `staff_roles` contains only active operational staff role codes
- authenticated non-staff users receive `staff_roles: []`

## Backend Authoritativeness
Current rule:

- backend role evaluation is authoritative

Client-provided role claims are not part of the current architecture.

## Authorization and Person Classification
Current clarification:

- CRM role assignment does not determine `Person.record_type`
- a `CRM_ADMIN` user can be linked to either a `BUSINESS` or `TECHNICAL` person
- staff access remains evaluated from active `StaffRoleAssignment` data rather than person classification

## Person Write Lifecycle

- `CRM_ADMIN` and `CRM_MANAGER` may create Contacts, create Members, edit active BUSINESS Persons, archive Persons, and restore Persons
- `CRM_VIEWER` is read-only and receives `403 Forbidden` for all Person write endpoints
- authenticated users with no qualifying active CRM role receive `403 Forbidden`; anonymous callers receive `401 Unauthorized`
- Django `is_staff` and `is_superuser` alone do not grant operational Person write authority

## Currently Implemented vs Planned
Currently implemented:

- canonical staff role model
- user-role assignment model
- deterministic role seeding
- active-role evaluation helpers
- reusable DRF permission classes
- `/api/v1/auth/me/` role exposure
- `/api/v1/people/{person_id}/audit-history/` read access for `CRM_ADMIN`, `CRM_MANAGER`, and permission-filtered `CRM_VIEWER`
- Internal Notes are restricted to `CRM_ADMIN` and `CRM_MANAGER`
- `CRM_VIEWER` has no Internal Notes read or write access

## Person Audit History Visibility
Current backend rule:

- `GET /api/v1/people/{person_id}/audit-history/` is read-only operational CRM history for one `BUSINESS` Person
- `CRM_ADMIN` may view full permitted Person Audit History, including Internal Note lifecycle events
- `CRM_MANAGER` may view full permitted Person Audit History, including Internal Note lifecycle events
- `CRM_VIEWER` may view Person Audit History, but cannot see Internal Notes and therefore cannot see Internal Note `AuditEvent` rows
- audit visibility cannot bypass the access rules of the underlying domain

Current filtering rule:

- Person-scoped audit rows are filtered at the queryset level before pagination and count
- forbidden Internal Note audit rows are excluded for `CRM_VIEWER` before totals and pages are calculated
- Viewer responses therefore do not leak hidden note activity through `count`, `next`, `previous`, or page gaps

## Django Admin and Audit Inspection
Current clarification:

- Django Admin technical privileges remain separate from Elevate CRM operational authorization
- read-only inspection of `audit.AuditEvent` in Django Admin is a technical administration capability, not a CRM role grant
- future CRM Audit History access should be implemented separately with explicit operational authorization rules
- Django `is_staff` and `is_superuser` still do not become Elevate CRM roles
- recording Staff Access audit history does not make Django `is_staff` or `is_superuser` an operational CRM role

Planned, not yet implemented:

- Staff CRM resource endpoints
- finer-grained operational permission policies beyond role-code checks
- additional non-staff authorization domains

## Phase 1A.1 Capability Foundation

Status: Implemented foundation; endpoint and field enforcement remain planned.

The six canonical capability codes are:

- `PERSON_CONTACT_READ`
- `PERSON_DEMOGRAPHIC_READ`
- `PERSON_EXPORT`
- `PERSON_RESTRICTED_ACCESS`
- `PRIVACY_AUDIT_READ`
- `STAFF_CAPABILITY_ADMIN`

The approved initial role-default matrix is:

| Role | Default capabilities |
| --- | --- |
| `CRM_VIEWER` | none |
| `CRM_MANAGER` | `PERSON_CONTACT_READ` |
| `CRM_ADMIN` | `PERSON_CONTACT_READ`, `PERSON_DEMOGRAPHIC_READ`, `PERSON_EXPORT`, `STAFF_CAPABILITY_ADMIN` |

`PERSON_RESTRICTED_ACCESS` and `PRIVACY_AUDIT_READ` are not ordinary CRM-role
defaults. `PERSON_RESTRICTED_ACCESS` remains an additional gate for future
explicitly approved exceptional workflows; no such workflow is exposed in
Phase 1A.1.

The backend evaluator combines active role defaults with active individual
assignments, excludes suspended and revoked assignments, and subtracts active
explicit restrictions. Removing a restriction does not reactivate a suspended
or revoked assignment. For ordinary, non-superuser evaluation, individual
capability evaluation also requires an active staff role, preserving the
existing Person/User and operational staff boundary. The approved Django
superuser exception is evaluated before this ordinary-user rule and is
separately audited.

The reusable `HasCapability` DRF permission class delegates to this evaluator
for future endpoint adoption. No existing endpoint has been switched from its
role-based permission class in Phase 1A.1.

Active Django superusers receive the approved full privacy-capability bypass.
Each requested bypass is recorded transactionally as
`PRIVACY_SUPERUSER_BYPASS`; if the audit write fails, the bypass fails. This
exception applies to capability evaluation only and does not make a Django
superuser an operational CRM role or change existing endpoint permission
classes.

Phase 1A.1 deliberately does not provide capability administration endpoints,
governance proposals, two-person approval, masking, Person projections,
search/export enforcement, CRM UI enforcement, automatic disposal, or an
exceptional restricted-access workflow. These remain later approved phases.

Source and validation evidence: the catalog and defaults are defined in
`staff_access/models.py` and seeded by
`staff_access/migrations/0006_capability_foundation.py`; evaluation is in
`staff_access/permissions.py`; the audit action is in `audit/models.py` and
`audit/migrations/0020_alter_auditevent_action.py`; focused tests are in
`staff_access/tests.py`. Local Django checks, migration-drift checks, Python
compilation, and the 9-test focused capability suite completed successfully.
Production migration, endpoint integration, and live compatibility remain
unverified.

The implemented anonymous Community join endpoint is an explicit public
submission boundary, not a CRM authorization exception. It uses its own DTO,
service, response projection, CSRF protection, and anonymous throttle. It does not
grant access to People, Membership, ProfessionalProfile, audit, or other CRM APIs.
