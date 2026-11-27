# Staff Management V1

Status: **Approved architecture / Planned implementation**

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

Planned durable addition: `StaffAdministratorProposal`, separate from privacy
capability proposals. It should preserve proposer/approver identity, target,
requested operation, expected state, reason, expiry, terminal status,
invalidation reason and resulting operation reference.

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
- retained role-assignment behavior during global account disablement must be finalized before implementation;
- approved but unused Administrator proposals remain subject to expiry and invalidation;
- provider acceptance is not confirmed inbox delivery;
- exceptional recovery is included in the account-security implementation phase;
- Community and Mobile compatibility must be checked for global authentication changes;
- Administrator approval queue list/detail contracts are required;
- login-email change cancellation is required;
- existing operational mutation paths must not bypass new governance.

Where implementation evidence does not yet settle a detail, the detail remains
an explicit Phase 2 design requirement rather than an invented contract.

## Audit requirements

Audit coverage must include identity linkage, invitation issue/resend/cancel/
expiry/acceptance, delivery outcomes, Administrator proposals and decisions,
role changes, staff deactivation/reactivation, account disablement/enablement,
email changes, administrative password recovery, exceptional recovery,
protected-account denials, self-management denials, Superuser actions,
automatic grant suspension and explicit grant restoration.

Do not record passwords, raw tokens, invitation URLs, email bodies, provider
payloads or unnecessary personal data.

## Planned API surface

The following are planned, not currently available contracts:

```text
GET  /api/v1/staff/directory/
GET  /api/v1/staff/directory/{user_id}/
POST /api/v1/staff/onboarding/
POST /api/v1/staff/invitations/{id}/resend/
POST /api/v1/staff/invitations/{id}/cancel/
POST /api/v1/staff/invitations/{id}/accept/
POST /api/v1/staff/admin-proposals/
POST /api/v1/staff/admin-proposals/{id}/approve/
POST /api/v1/staff/admin-proposals/{id}/reject/
POST /api/v1/staff/{id}/role/
POST /api/v1/staff/{id}/deactivate/
POST /api/v1/staff/{id}/reactivate/
POST /api/v1/staff/{id}/account-disable/
POST /api/v1/staff/{id}/account-enable/
POST /api/v1/staff/{id}/password-reset/
POST /api/v1/staff/{id}/email-change/
GET  /api/v1/staff/{id}/audit-history/
GET  /api/v1/staff/{id}/privacy-summary/
```

Exact routes, DTOs, error contracts and OpenAPI definitions require Phase 2
implementation design. No listed endpoint should be interpreted as deployed.

## Verification and deferred work

Required future verification includes role and invitation concurrency, approval
staleness, final-admin continuity, technical-account protection, privacy grant
suspension, audit-failure rollback, throttling, restricted projections and
frontend cache invalidation.

Deferred items include comprehensive Person record-access auditing
(`GDPR-FUTURE-001`), a general exceptional recovery UI, and a permanent legacy
reconciliation subsystem.
