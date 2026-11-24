# Elevate MK GDPR — Backend Technical Architecture and Implementation Guide

Status: Living Documentation. Capability foundation, governance models,
privacy-audit models, migrations, services and focused tests are implemented
and locally verified. Public activation, production/staging behavior and
deployment remain unverified.

## Scope and authority

The Django API remains authoritative for privacy authorization and data
protection. `people.Person` remains separate from `accounts.User`; existing
CRM roles, authentication flows, mobile contracts, and Community contracts are
preserved. Detailed database and authorization contracts remain in
[DATABASE.md](DATABASE.md) and [AUTHORIZATION.md](AUTHORIZATION.md).

This document records the approved architecture and the current
implementation status. It does not claim GDPR compliance through code alone.

## Capability foundation

Status: API foundation and capability governance implementation are
source-inspected and focused PostgreSQL validation is verified. Public
API/Admin activation, endpoint enforcement, and field enforcement remain
unverified or deferred.

The API repository remains authoritative for the capability catalog, role-default
matrix, evaluator precedence, and reusable `HasCapability` permission contract;
see [AUTHORIZATION.md](AUTHORIZATION.md). The authoritative capability schema,
integrity constraints, indexes, and migration details are maintained in
[DATABASE.md](DATABASE.md). This guide records the privacy governance boundary
and implementation status without duplicating those contracts.

### Governance boundary

The current public integration boundary does not expose capability mutation
endpoints or Django Admin mutation surfaces. The three governance persistence models are not
registered with Django Admin. The governance service is the application-level
writer for privacy capability mutations; role lifecycle services remain
separate writers for operational role state and are coordinated through the
documented lock contract. Direct ORM writes from trusted maintenance,
migration, or database-administrator paths remain outside this application
boundary and are not treated as an operational governance interface. The
governance service implements
proposal, approval, rejection, cancellation, expiry, reconciliation, and
capability mutation paths with transactional audit handling. **Historical
intermediate status (superseded below):** these paths and baseline capture were
source-updated while the new baseline shape remained unverified until the
focused suite was rerun. Caller
coverage, public activation and operational deployment remain unverified.
Automatic retention disposal and sensitive-data access paths remain deferred.

## Database and migration references

The additive schema is documented in [DATABASE.md](DATABASE.md). Migrations
are `audit.0020_alter_auditevent_action` and
`staff_access.0006_capability_foundation`. They preserve existing audit action
choices, role rows, and role assignments during forward migration. The
capability migration's seed reverse is a no-op, but reversing its schema would
drop capability tables and rows; production rollback therefore requires an
approved backup/export and recovery plan. Production migration execution was
not performed.

The governance migration is
`staff_access.0007_capability_governance`. It depends on
`staff_access.0006_capability_foundation` and
`audit.0021_alter_auditevent_action`. The audit migration
`audit.0021_alter_auditevent_action` depends on
`audit.0020_alter_auditevent_action`. Their application and baseline capture
are verified in the isolated PostgreSQL test
database used by focused validation; production rollback behavior remains
unverified. Migration `0007` source-defines
`baseline_captured_at`, `baseline_snapshot`, and the
`CAPABILITY_POLICY_BASELINED` audit event when the role-default and individual
assignment/restriction baseline is captured. The baseline stores internal
identifiers and capability/state metadata only; it does not store person names
or email addresses. Schema details belong in [DATABASE.md](DATABASE.md); evaluator and
approval semantics belong in [AUTHORIZATION.md](AUTHORIZATION.md).

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
- **Implemented / Source-inspected:** foundation API models, evaluator,
  reusable `HasCapability` permission, capability governance models, service,
  audit actions, lock-order coordination, no-Admin-registration boundary, and
  migration definitions. **Historical intermediate status (superseded below):**
  the baseline correction was newly changed and initially remained
  runtime-unverified; final isolated validation is recorded below.
- **Verified — local migration and schema validation for the foundation:** a backup was created
  and verified locally before validation; the local PostgreSQL target was
  `localhost:5432/elevate`; migration state reported no pending operations;
  the historical six-capability Phase 1A.2 baseline and approved defaults were
  present; the current Phase 1A.3 catalogue contains seven capabilities; four
  capability tables, four explicit indexes, and three named constraints were
  present; `manage.py check --fail-level ERROR` passed; and
  `manage.py makemigrations --check --dry-run` reported no changes.
- **Test-executed / Verified locally for the foundation:**
  `CapabilityFoundationTests` ran 13 tests in 34.658 seconds and returned
  `OK` against the local PostgreSQL test database; the local test database was
  preserved.
- **Planned / Deferred:** public API/Admin exposure, projections, masking,
  search/export enforcement, CRM UI behavior, and capability-governance
  enforcement.
- **Historical runtime evidence (superseded):** governance migrations applied
  cleanly in newly named isolated test databases; the final governance rerun
  passed 21 tests in 166.170 seconds; the combined focused run of governance,
  capability-foundation, and final CRM-admin concurrency tests passed 35 tests;
  audit rollback, proposal
  lifecycle, immutability, expiry, stale-state handling, authorization,
  restriction precedence, uniqueness and covered lock-order concurrency all
  passed. The initial stale `test_elevate` database attempt was discarded from
  acceptance evidence.
- **Unverified:** production and staging migration safety, deployment behavior,
  live deployment state, cross-repository compatibility, all privileged
  operational writers, and independent specialist review completion where noted
  in the Main Codex handoff.

## Security and privacy limitations

The existing Django Admin path can still expose raw audit records to principals
with technical audit permissions, and existing superusers can administer
operational staff roles through the pre-existing Staff Admin path. These are
separate security findings and are not silently changed by this foundation.
The current security review leaves GOV-003 (direct ORM/trusted-writer boundary)
open. GOV-004, the former controlled-recovery finding, is superseded by the
approved direct Django superuser authority decision; final isolated validation
verified the replacement path. Mandatory transactional audit, before/after
recording, and rollback-on-audit-failure integrity remain required. GOV-006 lock ordering is verified for governance,
role revocation, offboarding and reconciliation paths covered by focused tests,
but all privileged operational writers are not yet covered. GOV-005's actorless
invocation condition is rejected by the service, while caller coverage and
operational scheduling remain unverified. These remain activation gates, not
resolved security findings.
AUTH-001 and AUTH-002 remain open and are not resolved by this local validation
or documentation update. The Phase 1A.2 foundation verification gate is
complete for the isolated local scope; operational governance activation
remains blocked by the documented activation gates.

The previous controlled-recovery requirement is superseded by the approved
full Django superuser authority model. The API now provides one explicit,
audited direct-operation service path for active superusers. It does not expose
new public endpoints or Django Admin governance screens. Ordinary administrators
remain subject to eligibility, restrictions, self-administration limits,
proposal lifecycle, and two-person approval. The replacement path is
implemented and runtime-verified in the final isolated suite; production,
operational activation, and cross-repository behavior remain unverified.

### Final validation supersession (authoritative current status)

This final status supersedes the earlier intermediate 21-test, 35-test, and
failed 42-discovery validation entries above. Synthetic pre-`0007` migration
verification passed, and the final isolated focused run passed 41/41 tests.
The baseline captured role defaults, active/suspended/revoked assignments,
active/historical restrictions, identifiers, timestamps, and its matching audit
event. QA accepted the scoped evidence. Production/staging, deployment,
cross-repository runtime behavior, and operational activation remain
unverified.

## Revised superuser validation evidence

This subsection preserves historical intermediate runtime evidence and is
superseded by the authoritative final validation status recorded above.

**Runtime validation — Partial / Not accepted:** the configured target was
confirmed as PostgreSQL at `localhost:5432`, database `elevate`; no migrations
were applied there. The isolated database `elevate_gdpr_phase1a2_superuser`
was created and destroyed by the test runner. System checks passed, migration
drift reported no changes, and the migration plan showed the expected
`audit.0021` and `staff_access.0007` operations pending on the configured
database.

The combined run discovered 42 tests, ran 41, and failed with one new baseline
assertion failure plus one `FinalCrmAdminConcurrencyTests` setup error caused
by a duplicate serialized `auth_permission` fixture row. Separate runs passed
`CapabilityFoundationTests` 13/13 and
`FinalCrmAdminConcurrencyTests` 1/1. The governance suite ran 28 tests, with
27 passing and one failure because the fixture retained a legacy empty policy
baseline snapshot. Superuser runtime acceptance, the expanded baseline, and
the documentation quality gate therefore remain unverified.

**Historical source-status note (superseded):** the latest source correction also locks role-assignment creation/reactivation
in the shared role-to-assignment-to-user order and invalidates pending
role-default proposals when their role becomes inactive. These changes were
implemented but runtime-unverified until the final focused rerun.

## Phase 1A.3 privacy-audit foundation

Status: **Implemented and locally verified.** This stage adds
the versioned audit contract, trusted request provenance, separate operational
and privacy audit domains, normalized `AuditEventSubject` Person associations,
the `LEGAL_HOLD_ADMIN` capability, versioned retention-policy records, and
event- and Person-scoped legal holds. It does not expose new public governance
endpoints, enable writable Django Admin governance, or dispose of records.

`PRIVACY_AUDIT_READ` gates privacy-audit projections. Raw `AuditEvent` Admin
visibility is active-superuser-only. Privacy payloads reject sensitive values,
and audit writes remain transactional with the protected operation. The
retention evaluator requires an authorized `LEGAL_HOLD_ADMIN`, is
non-destructive, and uses an explicit evaluation
timestamp; it reports eligible, held, unclassified, unresolved-subject, and
excluded events without deleting data.

The initial 24-month retention period remains provisional and requires formal
legal/privacy approval. Retention policy versions are immutable and use audit
event creation time as the retention reference. Ordinary hold release requires
a distinct approver through the dedicated `LegalHoldReleaseRequest` workflow;
the approved active-superuser authority remains available. This dedicated
model keeps hold-release invariants separate from capability proposals.

**Historical evidence status:** isolated PostgreSQL upgrade from the Phase 1A.2 state
preserved synthetic assignments, restrictions, audit events, the seeded
baseline, normalized Person associations, and governance records. The
earlier Phase 1A.3 focused suite passed 19/19 and the affected broader suite passed
102/102. Operational database migration, disposal, production/staging
behavior, CRM integration, and cross-repository runtime compatibility remain
deferred or unverified.

## Targeted remediation status

**Implemented and locally verified:** the catalogue extension now
uses explicit `SYSTEM_MIGRATION` metadata and `MIGRATION` provenance, advances
the existing policy version atomically, preserves the original baseline, and
does not seed a `LEGAL_HOLD_ADMIN` role default. The normalized-subject
backfill validates Person existence in batches and preserves unresolved audit
events without inventing subjects; unresolved privacy events remain excluded
from retention eligibility.

The existing Person audit-history endpoint remains an operational CRM history
projection: it uses the operational audit domain and existing CRM record and
field visibility rules, so privacy events are excluded before count and
pagination. The dedicated privacy projection service requires an active user,
`PRIVACY_AUDIT_READ`, and an authorized business-domain Person scope before
returning privacy-domain events; STAFF subjects remain available only through
the existing CRM-admin record boundary. No public privacy-audit endpoint or Angular integration
is introduced.

The focused validation command set was: `manage.py check --fail-level ERROR`,
`manage.py makemigrations --check --dry-run`, isolated PostgreSQL migration
verification from Phase 1A.2 through Phase 1A.3, the synthetic normalized
subject migration test, focused privacy-audit projection tests, and the
existing Phase 1A.3 regression suites. The remediation subset passed 9/9 and
the broader authorized focused set passed 69/69 against isolated PostgreSQL
test database `test_elevate`; no operational database was migrated. AUTH-002
remains preserved with its scoped raw-admin resolution;
trusted provenance construction is a non-blocking follow-up, not an accepted
risk. GOV-003, GOV-005, uncovered GOV-006 writers, AUTH-001, legacy TECHNICAL
reconciliation, legal approval for disposal, and cross-repository runtime
compatibility remain open or unverified.
