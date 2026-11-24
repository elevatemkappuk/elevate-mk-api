# Elevate MK GDPR — Backend Technical Architecture and Implementation Guide

Status: Living Documentation. Phase 1A.1 capability foundation implemented;
verification is limited to the evidence listed below.

## Scope and authority

The Django API remains authoritative for privacy authorization and data
protection. `people.Person` remains separate from `accounts.User`; existing
CRM roles, authentication flows, mobile contracts, and Community contracts are
preserved. Detailed database and authorization contracts remain in
[DATABASE.md](DATABASE.md) and [AUTHORIZATION.md](AUTHORIZATION.md).

This document records the approved Phase 1A architecture and the current
implementation status. It does not claim GDPR compliance through code alone.

## Phase 1A.1 capability foundation

### Approved catalog and defaults

The six capabilities are `PERSON_CONTACT_READ`, `PERSON_DEMOGRAPHIC_READ`,
`PERSON_EXPORT`, `PERSON_RESTRICTED_ACCESS`, `PRIVACY_AUDIT_READ`, and
`STAFF_CAPABILITY_ADMIN`.

The initial defaults are `CRM_VIEWER: none`, `CRM_MANAGER:
PERSON_CONTACT_READ`, and `CRM_ADMIN: PERSON_CONTACT_READ,
PERSON_DEMOGRAPHIC_READ, PERSON_EXPORT, STAFF_CAPABILITY_ADMIN`.
`PERSON_RESTRICTED_ACCESS` and `PRIVACY_AUDIT_READ` are not ordinary role
defaults.

### Evaluator and precedence

The central backend evaluator in `staff_access.permissions`:

1. denies unauthenticated and inactive users;
2. requires an active operational staff role for ordinary capability grants;
3. combines active role defaults and active individual assignments;
4. excludes suspended and revoked assignments;
5. subtracts active explicit restrictions; and
6. preserves assignment and restriction history in the database.

An active Django superuser receives the approved full privacy-capability
bypass. The evaluator writes `PRIVACY_SUPERUSER_BYPASS` in the same transaction
before returning the bypass result. Audit failure prevents the bypass result.
This is not an operational CRM-role grant.

### Governance boundary

Phase 1A.1 creates the data and evaluation foundation only. It does not expose
capability mutation endpoints, Django Admin mutation surfaces, proposals,
two-person approval, justifications, automatic retention disposal, or
sensitive-data access paths. Privacy governance mutations must remain
unavailable until their mandatory transactional audit and approved workflow
are implemented.

## Database and migration references

The additive schema is documented in [DATABASE.md](DATABASE.md). Migrations
are `audit.0020_alter_auditevent_action` and
`staff_access.0006_capability_foundation`. They preserve existing audit action
choices, role rows, and role assignments during forward migration. The
capability migration's seed reverse is a no-op, but reversing its schema would
drop capability tables and rows; production rollback therefore requires an
approved backup/export and recovery plan. Production migration execution was
not performed.

## Compatibility boundaries

Existing endpoint permission classes remain role-based. CRM, Mobile, and
Community continue consuming their existing authentication and `staff_roles`
contracts. No `/auth/me/` capability projection, frontend guard, Person field
masking, search protection, campaign protection, or export enforcement was
added.

## Evidence and status

- **Approved:** Phase 1A.1 catalog, default matrix, evaluator precedence,
  audited superuser exception, and deferred enforcement boundaries.
- **Implemented:** API models, evaluator, reusable `HasCapability` permission,
  audit action, migrations, and focused regression tests.
- **Verified:** `manage.py check --fail-level ERROR`,
  `manage.py makemigrations --check --dry-run`, Python compilation, and
  `manage.py test staff_access.tests.CapabilityFoundationTests --keepdb
  --verbosity 0`. The focused suite ran 9 tests and returned `OK`.
- **Planned:** governance workflow, approval lifecycle, API exposure,
  projections, masking, search/export enforcement, and CRM UI behavior.
- **Unverified:** production migration safety, deployment behavior, live
  database state, and independent specialist review completion where noted in
  the Main Codex handoff.

## Security and privacy limitations

The existing Django Admin path can still expose raw audit records to principals
with technical audit permissions, and existing superusers can administer
operational staff roles through the pre-existing Staff Admin path. These are
separate security findings and are not silently changed by Phase 1A.1.
