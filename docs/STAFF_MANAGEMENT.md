# Staff Management V1

Status: **Directory, invitation, staff-access and global account lifecycle contracts implemented; runtime verification pending**

This document is the API repository's authoritative living guide for Staff
Management V1. It records approved requirements and architecture, existing
reusable foundations, planned changes, evidence limitations, and deferred
work. Planned behavior is not presented as implemented or verified.

The backend remains authoritative for identity, staff authorization, privacy
capabilities, lifecycle transitions, audit persistence, and session
invalidation. See [Authentication](AUTHENTICATION.md),
[Authorization](AUTHORIZATION.md), [GDPR](GDPR.md), [API](API.md), and
[Database](DATABASE.md) for existing contracts.

## Status vocabulary

- **Approved** — accepted requirement or architecture decision; not proof of implementation.
- **Implemented** — supported by current source inspection.
- **Verified** — supported by recorded execution evidence.
- **Planned** — approved future behavior not yet implemented.
- **Unverified** — source or compatibility evidence is incomplete.
- **Deferred** — intentionally outside V1.

The historical GDPR baseline of 1,474/1,474 API tests is not new Staff
Management verification.

## Staff directory and detail

Implementation status: **Implemented**. Verification status: **Pending
Verification**; automated tests and runtime compatibility checks remain deferred
until final hardening.

The backend-authoritative authenticated-user contract now includes
`access.staff_management`. It is calculated by the centralized Staff Management
policy and does not grant general CRM, People, Marketing, Moderation or Privacy
Administration access.

The read-only contracts are:

- `GET /api/v1/staff/directory/`
- `GET /api/v1/staff/directory/{user_id}/`
- `GET /api/v1/staff/directory/{user_id}/activity/`

They are restricted to active operational CRM Administrators and eligible active
Django Superusers. Directory search, role/state filters, deterministic ordering
and server pagination are backend-owned. Email values are projected through the
existing contact capability boundary. Raw credentials, tokens, session
generations and unrestricted audit metadata are not returned.

Directory state is derived from authoritative records: active role assignment,
account lifecycle state, invitation state and Administrator proposal state. A
disabled account may retain an active CRM role while its displayed access state
is unavailable. Staff deactivation remains distinct from global account
disablement.

## Staff onboarding identity resolution

Implementation status: **Implemented** for source-reviewed lookup and
validation boundaries. Verification status: **Pending Verification**.

The onboarding boundary is restricted to the same active CRM Administrator and
eligible active Django Superuser policy as Staff Management. Managers, Viewers
and ordinary People users cannot use it, and the narrow Superuser exception
does not grant the general People directory.

The implemented contracts are:

- `GET /api/v1/staff/onboarding/roles/` — active canonical CRM role options;
- `GET /api/v1/staff/onboarding/people/?q=<name-or-permitted-contact>` — a
  bounded privacy-safe Person candidate lookup without a general People count;
- `POST /api/v1/staff/onboarding/validate/` — validates an existing Person or
  new identity, login-email collision, account state, staff relationship,
  duplicate identity signals, requested role and mandatory reason.

Validation is intentionally non-persistent. It reports whether an existing
User can be reused or a new `PENDING_ACTIVATION` account would be appropriate,
but `submission_available` is false. No Person, User, role assignment,
invitation, email job or Administrator proposal is created by this stage.
The authoritative submission is `POST /api/v1/staff/onboarding/`. It independently
revalidates identity, account state, duplicate signals, role and reason, then
atomically reuses or creates the Person/User, creates a pending invitation and
durable `STAFF_INVITATION` email job, and records attribution. Manager and Viewer
roles create invitations; CRM Administrator submission creates a separately
approved governance proposal.

## Invitation lifecycle

Implementation status: **Implemented**. Verification status: **Pending
Verification**.

`StaffInvitation` stores the stable identifier, target User where available,
canonical requested role, operation, reason, expiry and SHA-256 token digest.
Plaintext tokens are materialized only while the notification worker prepares a
provider request. The default validity is 72 hours through
`STAFF_INVITATION_EXPIRY_HOURS`. Inspection is non-mutating at
`GET /api/v1/staff/invitations/inspect/`; acceptance is
`POST /api/v1/staff/invitations/accept/` and is single-use, expiry-enforced and
atomic. Active existing Users must authenticate as the exact target; new users
select their own password and remain pending until acceptance. Disabled accounts
cannot be silently enabled.

Resend supersedes the prior invitation and cancels compatible queued work;
cancellation revokes the invitation without deleting Person/User identity.
Both require a reason and preserve historical records. Delivery status is kept
separate from acceptance and is exposed only as safe queue/provider state.

The Brevo template setting is `BREVO_STAFF_INVITATION_TEMPLATE_ID` (ID `36`).
The worker maps exactly `first_name`, `role_name`, `invitation_url` and
`expires_at`; the URL is generated from configured `CRM_FRONTEND_URL` at the
public route `/staff/invitation/accept` and never from the request Host header.
Public inspection and acceptance use explicit DRF scopes
`staff_invitation_inspect` and `staff_invitation_accept`; issuance and resend
use `staff_invitation_issue` and `staff_invitation_resend`.

Migration note: the original invitation model is `staff_access.0003_staffinvitation`;
the existing email-change model is `0004_staffemailchangerequest`, followed by
the role-integrity and Administrator-proposal foundation migrations
`0009_role_family_integrity` and `0010_staff_administrator_proposals`. The
invitation lifecycle fields are added by `0011_staffinvitation_target_and_reason`,
which depends on `0010` and therefore preserves the complete prior graph. No
earlier migration was overwritten, removed or disconnected.

## Administrator governance

Implementation status: **Implemented**. Verification status: **Pending
Verification**.

Administrator onboarding through `POST /api/v1/staff/onboarding/` creates an
`ONBOARD` proposal when `CRM_ADMIN` is selected. It creates or reuses the
Person/User transactionally but does not grant the role or issue an invitation.
Existing active staff promotion is submitted through
`POST /api/v1/staff/administrator-proposals/create/` with `PROMOTE_EXISTING`;
the current Manager/Viewer role remains effective while pending.

Operational governance routes are:

- `GET /api/v1/staff/administrator-proposals/` — permission-aware paginated queue;
- `GET /api/v1/staff/administrator-proposals/{id}/` — safe proposal detail;
- `POST /api/v1/staff/administrator-proposals/{id}/approve/`;
- `POST /api/v1/staff/administrator-proposals/{id}/reject/`;
- `POST /api/v1/staff/administrator-proposals/{id}/cancel/`.

Approval independently revalidates proposer, approver, target, operation,
expiry, expected state and protected-account rules. PROMOTE atomically replaces
the existing CRM role and invalidates sessions. ONBOARD and REACTIVATE remain approved but
not effective, creates a CRM_ADMIN invitation whose expiration is the earlier
of 72 hours and the proposal's 30-day expiration, and queues the existing
transactional email job. Invitation acceptance consumes the exact approved
ONBOARD or REACTIVATE proposal once before Administrator authority becomes effective.
Rejection, cancellation, stale-state invalidation and approval replay are
preserved in immutable decision history. GDPR capability governance remains a
separate domain.

## Invitation delivery integration boundary

The existing transactional email infrastructure already supports
`STAFF_INVITATION` jobs and uses the single configuration setting
`BREVO_STAFF_INVITATION_TEMPLATE_ID`. The approved Brevo template is ID `36`
(`Elevate MK — Staff Invitation`) with parameters `first_name`, `role_name`,
`invitation_url` and `expires_at`; the default invitation validity remains 72
hours and configurable. This stage does not create invitation jobs, render the
template, send email or call Brevo. Those actions belong to the secure
invitation lifecycle implementation.

Account enablement follows the approved retained-authority rule: retained active
roles and active privacy grants become effective again after fresh
authentication; revoked assignments, suspended grants, invalidated credentials,
cancelled invitations and invalidated proposals are not restored.

The centralized authorization foundation is implemented in
`staff_access.authorization`. Active CRM Administrators and eligible active
Django Superusers with a non-archived linked Person can access the Staff
Management boundary. The Superuser exception is scoped to Staff Management and
does not create a role or grant general CRM or privacy authority. Current User
`is_staff`/`is_superuser` flags identify protected technical targets, while
Person classification does not. Self-targeted administrative operations are
denied, including for Superusers. Denials at the policy boundary are recorded
with safe actor, target, operation and reason metadata through the centralized
audit service.

Historical authorization-foundation evidence: focused Staff Management authorization, staff lifecycle and
invitation tests passed **79/79**; affected authentication, session-revocation,
GDPR privacy-governance and audit tests passed **39/39**. This is local
isolated verification, not full-suite or deployment evidence.

The policy is a reusable foundation. Final-administrator continuity integration,
complete lifecycle audit coverage, and mutation workflows remain planned.
`STAFF-001`, `STAFF-002` and `STAFF-003` remain unresolved according to their
documented scope.

## CRM role integrity and Administrator continuity

The canonical CRM roles have explicit `StaffRole.family = "CRM"` metadata.
`StaffRoleAssignment.role_family` mirrors the assigned role family and is
validated against it. Migration `staff_access.0009_role_family_integrity`
backfills canonical roles and adds the conditional database constraint
`staff_access_one_active_role_family`, preserving revoked historical rows and
leaving null-family future roles available for later complementary families.

`assign_staff_role` remains the authoritative initial/reactivation service and
rejects a second active CRM-family role. `replace_staff_role` performs an
atomic, reason-required CRM role replacement, preserves the old assignment,
increments the target CRM session generation, and records both role events.
Invitation issuance, pending-role updates and redemption now require exactly
one canonical CRM role.

Administrator continuity counts only an active, non-archived linked Person,
active User, active canonical CRM_ADMIN role and active unrevoked assignment.
Superuser status alone does not count. CRM_ADMIN role locks serialize supported
revocation and replacement continuity decisions; affected User rows and role
assignments are locked within the same transaction. Mandatory audit failures
roll back the mutation.

Historical role-integrity verification passed **83/83** role-integrity, authorization,
staff lifecycle and invitation tests, including PostgreSQL row-locking
concurrency coverage. The directly affected authentication, session, privacy
and audit groups passed **79/79**. The combined focused command passed
**163/163** tests in **11.010 seconds** with no skips. Django checks and
migration-drift checks passed. Full API/CRM regression remains deferred until
final hardening.

The Django Admin assignment add, reactivation and revocation paths now invoke
the centralized target policy before the role services. Direct database
administration remains an explicit trusted boundary; no application-level
supported writer identified in this implementation is intended to bypass the service
or Admin safeguards.

Remaining limitations: direct trusted database administration remains outside
application controls; invitation-backed Administrator effecting remains in
future feature work; account
disablement, deactivation privacy suspension and complete lifecycle audit
coverage remain later stages. `STAFF-001` is materially reduced for supported
role mutation paths but is not globally resolved.

## Administrator approval governance

`StaffAdministratorProposal` and `StaffAdministratorProposalDecision` provide
a dedicated operational governance boundary separate from GDPR capability
proposals. Supported operations are `PROMOTE_EXISTING`, `ONBOARD` and
`REACTIVATE`. Submission fields are immutable and append-only; the records
retain durable User/role relationships, proposer and approver identities, a
mandatory reason, policy-version and expected-state snapshot, bounded expiry,
lifecycle timestamps and resulting references. Raw tokens, credentials and
identity snapshots are not stored.

Proposals expire after 30 days, matching the existing privacy-governance
proposal convention. Expiry is enforced at approval time. Only an active
operational CRM Administrator or eligible active Django Superuser may propose
or approve; proposer and approver must be different Users, and the approver
cannot be the target. Existing active Manager/Viewer promotion is approved and
effected atomically, preserving the prior assignment, invalidating the target
CRM session generation and recording mandatory audit events. New onboarding
and former-staff reactivation can be approved but are not effected until their
onboarding and reactivation invitation workflows consume the approval.

Target state and policy-version checks invalidate stale proposals. Approval,
effecting, rejection, cancellation and invalidation are transactionally
audited; audit persistence failure rolls back the associated transition.
Migration `staff_access.0010_staff_administrator_proposals` is additive,
validated in isolated test databases and not applied to the persistent
database.

Historical administrator-governance verification passed **169/169** tests in **10.281 seconds**
with no skips. This included the six dedicated administrator-governance tests,
authorization and role-integrity coverage, invitation/lifecycle
coverage, session-revocation coverage and affected privacy/audit groups. A
dedicated rerun passed **6/6** in **0.449 seconds**. Django system checks,
migration-drift checks and `git diff --check` passed. Full API, CRM, Community
and Mobile regression remains deferred until final hardening.

## Staff lifecycle and privacy foundation

`deactivate_staff_access` is the authoritative staff-access operation. It
requires a reason and centralized Staff Management authorization, revokes only
active CRM-family assignments, suspends active individual privacy grants,
preserves grant history and explicit restrictions, cancels compatible pending
invitations, invalidates pending or approved-but-unused Administrator
proposals, increments `crm_session_version`, and records transactional audit
evidence. It does not change `User.is_active`, passwords, Person, Membership
or Community eligibility. Future complementary role families are outside this
CRM-family operation.

`disable_user_account` and `enable_user_account` are the authoritative global
account lifecycle services exposed through the operational API. Disablement retains role-assignment history, sets
`User.account_lifecycle_state` to `DISABLED`, revokes global and CRM
credentials, cancels incompatible lifecycle state and preserves the final
Administrator invariant. Enablement accepts only an explicitly disabled
account, requires a reason and resumes retained active CRM roles and active
privacy grants. It does not restore revoked roles, suspended grants,
restrictions, sessions, tokens, invitations or proposals. Fresh authentication
is required.

The new account lifecycle state distinguishes `ACTIVE`, `PENDING_ACTIVATION`
and `DISABLED`; existing inactive accounts are classified as disabled because
`is_active` alone cannot distinguish those states. Reactivation reuses the
existing Person and User records. Manager and Viewer requests issue a
role-bound `REACTIVATE` invitation; Administrator requests create a
`REACTIVATE` proposal and issue an invitation only after independent approval.
Disabled global accounts require separate account enablement and are never
silently enabled.

The global account lifecycle endpoints are:

```text
POST /api/v1/staff/account/disable/
POST /api/v1/staff/account/enable/
POST /api/v1/staff/account/password-reset/
```

Both require a target User identifier and mandatory reason. Backend
authorization independently enforces administrator access, self-target and
protected-account restrictions, target lifecycle validity and final operational
Administrator continuity. Disablement invalidates global and CRM sessions,
Mobile sessions, password-reset eligibility, incompatible invitations,
administrator proposals and pending login-email changes. Enablement requires
fresh authentication and never resurrects invalidated credentials or revoked
and suspended authority.

Administrative password-reset initiation requires an active Staff account,
uses only the registered `User.email`, and returns a queued status plus opaque
request reference. It never accepts a password, recipient override, token,
reset URL or redirect. The request is persisted as a `TransactionalEmailJob`
using job type `ADMIN_PASSWORD_RESET`; the worker rechecks account state and
generates the existing Django reset token only in memory immediately before
delivery. The configured template is `BREVO_PASSWORD_RESET_TEMPLATE_ID` (local
configuration currently identifies template `10`) with the existing
`reset_url` parameter. No provider call is made by the API endpoint.

The endpoint is throttled at `5/hour` per existing DRF scoped-throttle
configuration, and a second pending request for the same target within one
hour is rejected. A successful request does not change the password or revoke
credentials; successful reset completion continues to use the shared public
confirmation service and credential-revocation path.

Authentication compatibility was source-reviewed across the API's CRM and
Community session boundaries, Django login/password-reset paths, and Mobile
JWT refresh and credential-revocation paths. This confirms the intended
enforcement contract, but is not runtime verification; full API, CRM,
Community, and Mobile execution remains deferred until final hardening.

Lifecycle tests were added but not executed during implementation, per the
final-hardening-only testing policy. Django checks, migration-drift checks and
diff checks passed. Full verification remains outstanding.

## Scope and boundaries

Staff Management administers operational CRM staff. It does not replace
Django Admin, change Django privilege flags, administer Membership, or create a
parallel privacy permission system.

The operational CRM roles are `CRM_ADMIN`, `CRM_MANAGER`, and `CRM_VIEWER`.
V1 requires one active role within this CRM role family while preserving the
possibility of future complementary role families.

`Person` remains the human identity record. `User` remains the authentication
account. `User.email` is the login identifier and remains independent from
`Person.primary_email`.

## Approved requirements

Staff Management V1 preserves these approved requirements:

1. CRM Administrators and eligible Django Superusers may manage staff.
2. New staff receive secure invitations and choose their own passwords.
3. Onboarding supports an existing Person or a new Person.
4. Existing Users accept staff access without being forced to reset credentials.
5. Invitation validity is configurable, with a 72-hour default.
6. Each staff member has one active CRM-family role.
7. Staff access and global User-account lifecycles are independent.
8. Reactivation requires a new invitation and current permission review.
9. CRM Administrator authority requires two distinct authorized administrators.
10. Administrators cannot use Staff Management for prohibited self-management.
11. Login-email changes require verification and preserve Person-email independence.
12. Authorized administrators may initiate password recovery without seeing credentials.
13. Django technical accounts are protected from ordinary CRM mutations.
14. The directory covers active, former, pending, expired, cancelled and disabled states.
15. Delivery failure preserves onboarding records and supports safe resend.
16. Staff lifecycle operations use immutable, privacy-safe audit history.
17. Privacy capability management remains within Privacy Administration.
18. Rollout preserves identity, roles, privacy state, Membership and audit history.

## Architecture decisions

### Narrow Superuser authority

An authenticated, active Django Superuser with a valid linked Person may access
Staff Management without a CRM Admin assignment. This exception is limited to
Staff Management. It does not grant general CRM access, assign a CRM role,
bypass self-management restrictions, bypass two-person approval, or modify
Django privilege flags. Superuser-authorized mutations require enhanced audit
metadata.

`is_staff` alone is insufficient.

### Suspended privacy grants

Staff-access deactivation must atomically revoke operational roles, suspend
active individual privacy grants, preserve explicit restrictions, invalidate
CRM authorization sessions, and persist mandatory audit evidence.

The existing `StaffCapabilityAssignment` suspended state is the intended
foundation. Reactivation does not restore suspended grants. Explicit
restoration remains a Privacy Administration operation with its existing
reason, authorization and audit rules.

### Exceptional recovery

Exceptional recovery is outside the normal Staff Management UI. Ordinary
password recovery remains the default when the registered email is accessible.
Verified email change remains the normal route when the account holder can
authenticate.

Loss of both credentials and registered email requires a future internal,
operator-only recovery service. It must verify the operator, independently
verify the account holder, record a reason and minimal structured evidence,
verify the replacement email, obtain required independent approval, invalidate
sessions and outstanding tokens, and require the account holder to choose a new
password. No general account-takeover interface is planned.

### Test-data transition

The current transition is test-data preparation, not a permanent reconciliation
product. Compatible accounts remain where practical. Incompatible test roles,
invitations and lifecycle states may be corrected or recreated only through a
separately approved operation. Production-grade safeguards remain mandatory.

## Domain model and lifecycle

Existing authoritative models:

- `people.Person` — human identity and contact information.
- `accounts.User` — authentication identity, account state and auth/session generations.
- `staff_access.StaffRole` — role definitions.
- `staff_access.StaffRoleAssignment` — historical role grants and revocations.
- `staff_access.StaffInvitation` — durable invitation and token-digest state.
- `staff_access.StaffEmailChangeRequest` — verified login-email change state.
- `notifications.TransactionalEmailJob` — delivery processing state.
- `staff_access.StaffCapabilityAssignment` — individual privacy grants.
- `staff_access.StaffCapabilityRestriction` — explicit restrictions.
- `audit.AuditEvent` — immutable audit evidence.

Implemented durable addition: `StaffAdministratorProposal` and
`StaffAdministratorProposalDecision`, separate from privacy capability
proposals. Submission fields are authoritative and immutable; status,
approver, decision/effect timestamps and resulting references are lifecycle
fields managed only by the administrator-governance service.

The existing invitation should be extended rather than replaced. Planned
extensions include explicit Person/User linkage, one requested CRM role, and
approval dependency where required. Delivery state remains in
`TransactionalEmailJob`; acceptance state remains in `StaffInvitation`.

Derived directory state must come from authoritative User, role, invitation,
proposal and delivery records. Do not add a competing global staff-status flag.

Lifecycle summary:

```text
Invitation: PENDING -> SENT/DELIVERY_UNCERTAIN -> ACCEPTED
                      \-> CANCELLED or EXPIRED

Staff access: NO_ACCESS -> PENDING_INVITATION -> ACTIVE
              ACTIVE -> DEACTIVATED -> PENDING_REACTIVATION -> ACTIVE

User account: ACTIVE <-> DISABLED

Admin proposal: PENDING -> APPROVED -> EFFECTED
                PENDING -> REJECTED/CANCELLED/EXPIRED/INVALIDATED
```

CRM Admin authority becomes effective only after all required approval,
invitation, target-eligibility and continuity conditions are satisfied.

## Transaction and concurrency rules

Lifecycle services must use a consistent lock order over proposal or invitation,
User, Person, role definitions, role assignments, privacy assignments, delivery
jobs and audit state. Staff deactivation and privacy-grant suspension must be
one transaction. Mandatory audit persistence failures fail the associated
mutation closed.

The final operational CRM Administrator must remain an active User with a
valid active `CRM_ADMIN` assignment. Django Superuser status alone does not
satisfy this invariant.

Existing role-revocation locking in `staff_access.services` is reusable, but
all supported Admin and service mutation paths must use the authoritative
lifecycle boundary. Direct ORM and DBA paths remain explicit trusted boundaries
and must not be treated as operational Staff Management interfaces.

## Security findings and implementation obligations

### STAFF-001 — Direct mutation bypass

**Medium, confirmed control gap.** Direct ORM or Django Admin mutation can
bypass service-level role eligibility, final-admin protection, session
invalidation, policy updates and audit persistence. Evidence includes
`staff_access/models.py` and `staff_access/services.py`.

Implementation must inventory and constrain supported writers before exposing
new lifecycle operations.

### STAFF-002 — Incomplete audit attribution

**Medium, confirmed control gap.** Invitation creation, resend, cancellation
and supersession lack complete lifecycle events. Redemption attribution does
not clearly distinguish initiator from redeemer, and offboarding can produce an
actorless account-state event.

Implementation must add complete actor/target/outcome attribution and ensure
mandatory audit failure rolls back the state transition.

### STAFF-003 — Public endpoint throttling

**Low, probable.** Public invitation redemption and login-email confirmation
do not currently declare explicit endpoint throttles. Token entropy and digest
storage reduce guessing risk, but targeted abuse protection remains required.

These findings remain unresolved and are not documented as fixed.

## Final implementation obligations

The following approved refinements remain explicit implementation requirements:

- delivery status must remain separate from acceptance status;
- V1 invitations require established Person and User linkage before issuance;
- active Users retain credentials when accepting staff access;
- new unactivated accounts must be distinguished from globally disabled existing accounts;
- login-email confirmation requires token verification and authentication with the existing account;
- `User.email` and `Person.primary_email` remain independent;
- account enablement must not restore invalidated sessions, revoked assignments or suspended grants;
- global account disablement retains role assignments but prevents effective access through inactive authentication;
- approved but unused Administrator proposals remain subject to expiry and invalidation;
- provider acceptance is not confirmed inbox delivery;
- exceptional recovery is included in the account-security implementation work;
- Community and Mobile compatibility must be checked for global authentication changes;
- Administrator approval queue list/detail contracts are required;
- login-email change cancellation is required;
- existing operational mutation paths must not bypass new governance.

Where runtime implementation evidence is not yet available, behavior is
classified **Implemented / Unverified** rather than treated as verified.

## Audit requirements

Audit coverage must include identity linkage, invitation issue/resend/cancel/
expiry/acceptance, delivery outcomes, Administrator proposals and decisions,
role changes, staff deactivation/reactivation, account disablement/enablement,
email changes, administrative password recovery, exceptional recovery,
protected-account denials, self-management denials, Superuser actions,
automatic grant suspension and explicit grant restoration.

Do not record passwords, raw tokens, invitation URLs, email bodies, provider
payloads or unnecessary personal data.

## API surface

The following read-only, validation and operational contracts are implemented
with runtime verification pending:

```text
GET  /api/v1/staff/directory/
GET  /api/v1/staff/directory/{user_id}/
GET  /api/v1/staff/directory/{user_id}/activity/
GET  /api/v1/staff/onboarding/roles/
GET  /api/v1/staff/onboarding/people/?q={query}
POST /api/v1/staff/onboarding/validate/
POST /api/v1/staff/onboarding/
POST /api/v1/staff/role-change/
POST /api/v1/staff/deactivate/
POST /api/v1/staff/reactivate/
POST /api/v1/staff/account/disable/
POST /api/v1/staff/account/enable/
POST /api/v1/staff/invitations/{id}/resend/
POST /api/v1/staff/invitations/{id}/cancel/
GET  /api/v1/staff/administrator-proposals/
GET  /api/v1/staff/administrator-proposals/{id}/
POST /api/v1/staff/administrator-proposals/{id}/approve/
POST /api/v1/staff/administrator-proposals/{id}/reject/
POST /api/v1/staff/administrator-proposals/{id}/cancel/
```

The following mutation contracts remain outside this implementation:

```text
POST /api/v1/staff/invitations/{id}/accept/
POST /api/v1/staff/{id}/password-reset/
POST /api/v1/staff/{id}/email-change/
GET  /api/v1/staff/{id}/audit-history/
GET  /api/v1/staff/{id}/privacy-summary/
```

Exact routes, DTOs, error contracts and OpenAPI definitions require the
approved implementation design. No listed endpoint should be interpreted as
deployed.

## Verification and deferred work

Required future verification includes role and invitation concurrency, approval
staleness, final-admin continuity, technical-account protection, privacy grant
suspension, audit-failure rollback, throttling, restricted projections and
frontend cache invalidation.

Deferred items include comprehensive Person record-access auditing
(`GDPR-FUTURE-001`), a general exceptional recovery UI, and a permanent legacy
reconciliation subsystem.
