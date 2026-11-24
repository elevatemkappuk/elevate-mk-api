# Elevate MK GDPR — Backend Technical Architecture and Implementation Guide

Status: Living Documentation. Capability foundation implemented;
verification is limited to the evidence listed below.

## Scope and authority

The Django API remains authoritative for privacy authorization and data
protection. `people.Person` remains separate from `accounts.User`; existing
CRM roles, authentication flows, mobile contracts, and Community contracts are
preserved. Detailed database and authorization contracts remain in
[DATABASE.md](DATABASE.md) and [AUTHORIZATION.md](AUTHORIZATION.md).

This document records the approved architecture and the current
implementation status. It does not claim GDPR compliance through code alone.

## Capability foundation

Status: API foundation implemented; endpoint and field enforcement remain
planned.

The API repository remains authoritative for the capability catalog, role-default
matrix, evaluator precedence, and reusable `HasCapability` permission contract;
see [AUTHORIZATION.md](AUTHORIZATION.md). The authoritative capability schema,
integrity constraints, indexes, and migration details are maintained in
[DATABASE.md](DATABASE.md). This guide records the privacy governance boundary
and implementation status without duplicating those contracts.

### Governance boundary

This foundation creates the data and evaluation layer only. It does not expose
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

Status: **Unverified** for cross-repository runtime compatibility in this
documentation update.

Existing endpoint permission classes remain role-based. CRM and Mobile retain
their existing authentication contracts, while Community uses its
Community-specific session and projection contracts. No `/auth/me/` capability
projection, frontend guard, Person field masking, search protection, campaign
protection, or export enforcement was added. Runtime compatibility across the
API, CRM, Mobile, and Community applications was not validated in this stage.

## Evidence and status

- **Approved:** catalog, default matrix, evaluator precedence,
  audited superuser exception, and deferred enforcement boundaries.
- **Implemented:** API models, evaluator, reusable `HasCapability` permission,
  audit action, and migrations.
- **Verified — local migration and schema validation:** a backup was created
  and verified locally before validation; the local PostgreSQL target was
  `localhost:5432/elevate`; migration state reported no pending operations;
  exactly six capabilities and the approved defaults were present; four
  capability tables, four explicit indexes, and three named constraints were
  present; `manage.py check --fail-level ERROR` passed; and
  `manage.py makemigrations --check --dry-run` reported no changes.
- **Test-executed / Verified locally:**
  `CapabilityFoundationTests` ran 13 tests in 34.658 seconds and returned
  `OK` against the local PostgreSQL test database; the local test database was
  preserved.
- **Planned / Deferred:** governance workflow, approval lifecycle, API
  exposure, projections, masking, search/export enforcement, CRM UI behavior,
  and later governance and enforcement work remain deferred.
- **Unverified:** production and staging migration safety, deployment
  behavior, live deployment state, cross-repository compatibility, and
  independent specialist review completion where noted in the Main Codex
  handoff.

## Security and privacy limitations

The existing Django Admin path can still expose raw audit records to principals
with technical audit permissions, and existing superusers can administer
operational staff roles through the pre-existing Staff Admin path. These are
separate security findings and are not silently changed by this foundation.
AUTH-001 and AUTH-002 remain open and are not resolved by this local validation
or documentation update.
