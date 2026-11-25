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
- `/api/v1/staff/access/me/` effective-access projection for authenticated CRM sessions with an active operational CRM role
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

## Capability Foundation

Status: Implemented foundation; corrected capability governance models and
services are source-inspected, with focused PostgreSQL migration and runtime
validation verified in isolation. Endpoint activation and field enforcement
remain unverified or deferred.

The original Phase 1A.2 baseline contained six capability codes:

- `PERSON_CONTACT_READ`
- `PERSON_DEMOGRAPHIC_READ`
- `PERSON_EXPORT`
- `PERSON_RESTRICTED_ACCESS`
- `PRIVACY_AUDIT_READ`
- `STAFF_CAPABILITY_ADMIN`

The current Phase 1A.3 catalogue contains those six baseline capabilities plus
`LEGAL_HOLD_ADMIN`. Migration `staff_access.0008_legal_hold_admin` advances the
existing policy version and records one audited `CAPABILITY_CATALOG_EXTENDED`
event with explicit migration provenance. It does not alter the original
`CAPABILITY_POLICY_BASELINED` event, role defaults, assignments, restrictions,
or historical records, and it does not grant `LEGAL_HOLD_ADMIN` by default.

The approved initial role-default matrix is:

| Role | Default capabilities |
| --- | --- |
| `CRM_VIEWER` | none |
| `CRM_MANAGER` | `PERSON_CONTACT_READ` |
| `CRM_ADMIN` | `PERSON_CONTACT_READ`, `PERSON_DEMOGRAPHIC_READ`, `PERSON_EXPORT`, `STAFF_CAPABILITY_ADMIN` |

`PERSON_RESTRICTED_ACCESS` and `PRIVACY_AUDIT_READ` are not ordinary CRM-role
defaults. `PERSON_RESTRICTED_ACCESS` remains an additional gate for future
explicitly approved exceptional workflows; no such workflow is exposed in
the current implementation.

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
role-based permission class; capability endpoint enforcement remains deferred.

### Capability governance activation boundary

Status: **Historical intermediate status; superseded below.** Governance model
and service semantics are present in the working tree. The focused validation
passed the capability foundation and CRM-admin concurrency subsets, but the
governance suite has one baseline-fixture failure; revised superuser runtime
acceptance remains unverified. No public API or Admin activation is evidenced.

This document remains authoritative for evaluator semantics: active role
defaults and active individual assignments are additive, suspended and revoked
assignments are ineffective, active restrictions subtract capabilities, and the
approved superuser bypass is audited transactionally.

The ordinary governance service requires an active, non-superuser STAFF actor
with an unrestricted `STAFF_CAPABILITY_ADMIN` capability. Ordinary targets must
be active STAFF users with an active canonical CRM role and cannot be the
acting user. Reasons are required and limited to 4,000 characters. Proposal
expiry is 30 days from creation.

Role-default operations and individual `STAFF_CAPABILITY_ADMIN` grant, restore,
or `INDIVIDUAL_UNRESTRICT` operations require a distinct second approver. Other
supported operations use the single-admin path. Approval revalidates proposer authority,
target eligibility, policy version, expected target state, expiry, and active
restrictions before mutation. The service uses transactional locking, records
proposal and mutation audit events, bumps policy version after mutation, and
stores an immutable decision. Existing pending proposals remain subject to this
ordinary workflow and are never silently converted into superuser operations.

Active Django superusers use the explicit `apply_superuser_capability_change`
service path. It permits self-administration, inactive/non-STAFF targets,
restriction bypass, direct role-default changes, and direct capability changes
without two-person approval. It still requires active authentication, a
justification, transactional before/after audit events, database constraints,
and the existing lock order. Audit failure rolls back the mutation. Inactive
superusers and ordinary administrators cannot invoke this path.

The service also provides direct individual-change and urgent
`STAFF_CAPABILITY_ADMIN` suspend/restrict/revoke wrappers, role-loss
reconciliation that suspends active individual grants, and expiry processing.
These are source behaviors only; execution and caller coverage remain
unverified.

### Historical intermediate governance gate status (superseded)

The following gate record preserves the intermediate review state. The GOV-004
controlled-recovery and replacement-path status below is superseded by the
authoritative final validation section; the remaining findings are restated
there as current blockers.

#### Historical intermediate open governance activation gates

The following findings remain open and unresolved from the current read-only
security review; the source implementation and migration definitions do not
resolve them:

- **GOV-003 — Direct ORM bypass:** direct ORM writes or privileged management
  scripts may bypass the governance service boundary until all callers are
  constrained and reviewed. The three governance persistence models are not registered in
  Django Admin; inspected application-level privacy writers are in
  `staff_access/governance.py`. Trusted migration, maintenance, and
  database-administrator paths remain explicit trust boundaries rather than
  operational interfaces.
- **GOV-004 — Recovery disabled:** controlled recovery remains disabled pending
  predicate is superseded by the approved full Django superuser authority
  decision. The replacement direct path is implemented but remains
  runtime-unverified. The historical recovery-disabled label above is
  superseded by this direct-superuser authority decision; it is retained only
  for architectural history.
- **GOV-005 — Actorless reconciliation:** the current service requires an
  attributable actor for reconciliation and rejects actorless invocation.
  Caller coverage and operational scheduling remain unverified before
  activation.
- **GOV-006 — Cross-path lock order:** lock ordering across proposal,
  authorization, capability, and reconciliation paths remains open for
  concurrency review.

GOV-003, GOV-004, the uncovered privileged-writer portion of GOV-006, and
operational coverage for GOV-005 block operational governance activation.
`AUTH-001` and `AUTH-002` also remain open.

Active Django superusers receive the approved full privacy-capability evaluation
bypass. Each requested bypass is recorded transactionally as
`PRIVACY_SUPERUSER_BYPASS`; if the audit write fails, the bypass fails. This
evaluation exception and the direct mutation path do not make a Django
superuser an operational CRM role or change existing endpoint permission
classes. The direct mutation path additionally records actual before/after
state, actor, target, operation, justification, and superuser authority usage.

The current documentation does not claim capability administration endpoints or
a Django Admin governance surface. Masking, Person projections, search/export
enforcement, CRM UI enforcement, automatic disposal, and any exceptional
restricted-access workflow remain deferred. Source service behavior does not
establish public activation.

### Final validation status (authoritative)

The final isolated Phase 1A.2 validation passed 41/41 tests, including revised
superuser authority, governance, capability foundation, and concurrency
coverage. Synthetic pre-`0007` migration verification also passed. Earlier
intermediate validation counts are historical and superseded. Operational
activation, production/staging behavior, and deployment remain unverified.

The former controlled-recovery finding is superseded by the approved direct
superuser authority decision; runtime validation of the replacement path is
complete in isolation. Mandatory transactional audit, before/after recording,
and rollback on audit failure remain required. GOV-003, GOV-005, uncovered GOV-006 writer paths,
AUTH-001, and AUTH-002 remain open.

Source and validation evidence: the catalog and defaults are defined in
`staff_access/models.py` and seeded by
`staff_access/migrations/0006_capability_foundation.py`; evaluation is in
`staff_access/permissions.py`; the audit action is in `audit/models.py` and
`audit/migrations/0020_alter_auditevent_action.py`; focused tests are in
`staff_access/tests.py`. Foundation and governance validation evidence is
retained in `DATABASE.md` and `GDPR.md`; isolated PostgreSQL migration and
focused test execution are verified, while production migration, governance
endpoint integration, and live compatibility remain unverified. Governance source is in
`staff_access/governance.py`, `staff_access/migrations/0007_capability_governance.py`,
and `audit/migrations/0021_alter_auditevent_action.py`.

The implemented anonymous Community join endpoint is an explicit public
submission boundary, not a CRM authorization exception. It uses its own DTO,
service, response projection, CSRF protection, and anonymous throttle. It does not
grant access to People, Membership, ProfessionalProfile, audit, or other CRM APIs.

## Phase 1A.4A effective Staff Access projection

Status: **Implemented / source-inspected; runtime verification not claimed.**

`GET /api/v1/staff/access/me/` is a read-only Staff CRM projection with exactly
two response fields: `capability_codes` and `policy_version`.

Admission is session-only through `CRM_SESSION_AUTHENTICATION_CLASSES`, followed
by authentication and an active operational `CRM_ADMIN`, `CRM_MANAGER`, or
`CRM_VIEWER` role. Anonymous callers receive `401`; authenticated users without
one of those roles receive `403`. A mobile JWT, Django `is_staff`, or Django
`is_superuser` alone does not satisfy endpoint admission.

The CRM capability disclosure allowlist is intentionally empty at this stage.
The endpoint therefore returns `capability_codes: []` for every admitted caller;
this prevents premature exposure of capability decisions before a later approved
CRM consumer and disclosure policy exist. Phase 1A.4B Staff Access lifecycle
integration is implemented; capability-consuming administration and disclosure
expansion remain deferred.

`policy_version` is the current capability-policy version observed for the
evaluation. It is informational only: it is not a grant, token, client-controlled
precondition, or authorization decision. The view locks the singleton policy
state and performs the allowlisted evaluator call in one transaction, returning
the policy version associated with that consistent read without mutating policy
state.

The response sets `Cache-Control: private, no-store`. An admitted Django
superuser still follows the existing audited superuser evaluator path and emits
the transactional `PRIVACY_SUPERUSER_BYPASS` audit event, but does not bypass the
CRM-role admission boundary or expand the empty response allowlist.

Evidence classification: **Implemented / Source-inspected** from
`staff_access/urls.py`, `views.py`, `serializers.py`, and `permissions.py`.
The focused API suite passed 34/34 against isolated PostgreSQL database
`test_elevate_gdpr_phase1a4a_20261124`; `manage.py check` passed;
`makemigrations --check --dry-run` reported `No changes detected`;
`migrate --plan` reported no planned migration operations; and `git diff --check`
was clean. No schema or migration files changed or were applied. Broader
runtime, deployment, CRM tests/build/runtime, and cross-repository consumption
remain unverified.

## Phase 1A.3 privacy-audit authorization

Implemented and locally verified in isolated PostgreSQL validation.
`PRIVACY_AUDIT_READ` gates privacy-audit projections and
`LEGAL_HOLD_ADMIN` governs retention-policy, legal-hold, and dry-run
operations. Raw `AuditEvent` Django Admin visibility is restricted to active
Django superusers and remains read-only. The approved full superuser privacy
authority remains audited and transactional. No public governance endpoint,
CRM permission screen, or writable governance Admin surface is introduced.
The non-destructive retention evaluator also requires an active authorized
`LEGAL_HOLD_ADMIN` actor; unresolved privacy subjects fail closed from
eligibility.

Current targeted evidence: the isolated PostgreSQL focused authorization and
privacy regression set passed 69/69, including the 9/9 remediation subset.
This verifies the local implementation only; operational activation and
cross-repository runtime behavior remain unverified.
## Phase 1A.4D privacy and legal-hold API authorization

The privacy-audit history endpoint requires CRM session authentication, an
active operational CRM role, `PRIVACY_AUDIT_READ`, and an authorized Community,
Business-compatible, or Staff Person scope. Technical Persons are denied.
`LEGAL_HOLD_ADMIN` independently authorizes hold administration and does not
require `PRIVACY_AUDIT_READ`. Django superuser operations remain the approved
audited exception and still require CRM admission.

Authorization and Person scope are enforced before filtering, counts, and
pagination. Denials use safe opaque references and reason codes. Mobile JWT
authentication and `/auth/me/` remain unchanged. Capability disclosure remains
the existing narrow projection until the CRM parser is updated in the
coordinated frontend rollout.

The backend continues to disclose only the existing `STAFF_CAPABILITY_ADMIN`
allowlist until the CRM parser is coordinated. `PRIVACY_AUDIT_READ` and
`LEGAL_HOLD_ADMIN` are enforced by the new backend endpoints but are not yet
advertised to the unchanged CRM client.
