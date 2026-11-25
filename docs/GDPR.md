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
Community-specific session and projection contracts. Phase 1A.4A adds the
session-only `GET /api/v1/staff/access/me/` projection, but does not add a
capability projection to `/auth/me/`, a frontend guard, Person field masking,
search protection, campaign protection, or export enforcement. Runtime
compatibility across the API, CRM, Mobile, and Community applications was not
validated in this stage.

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
- **Implemented / Source-inspected for Phase 1A.4A:** the exact
  `GET /api/v1/staff/access/me/` route and two-field response contract
  (`capability_codes`, `policy_version`), CRM-session and active-role admission,
  the then-empty CRM disclosure allowlist (superseded by the approved single
  `STAFF_CAPABILITY_ADMIN` allowlist in the later Phase 1A.4C contract), superuser boundary, transactionally consistent
  policy-version read, and `Cache-Control: private, no-store` behavior were
  inspected in the Staff Access route, view, serializer, and permission source.
- **Verified — focused API evidence for Phase 1A.4A:** 34/34 focused tests
  passed against isolated PostgreSQL database
  `test_elevate_gdpr_phase1a4a_20261124`; `manage.py check` passed;
  `makemigrations --check --dry-run` reported `No changes detected`;
  `migrate --plan` reported no planned migration operations; and `git diff --check`
  was clean. No schema or migration files changed, and no migrations were
  applied for Phase 1A.4A. Broader runtime, deployment, CRM tests/build/runtime,
  and cross-repository consumption remain unverified.
- **Implemented / source-inspected:** Phase 1A.4B CRM Staff Access lifecycle
  integration and fail-closed projection handling. Capability-consuming CRM
  administration remains deferred, as does capability disclosure expansion.
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

### Phase 1A.4C.2A lifecycle remediation

Status: **Implemented / focused validation verified in an isolated PostgreSQL
test environment; governance API exposure remains deferred.**

Staff-role assignment and reactivation now use a shared lifecycle service
boundary. The service acquires the capability policy row, canonical role rows,
assignment rows, and User rows in the documented order, revalidates the Staff
Person boundary, updates policy version when effective role state changes, and
preserves audit and transaction rollback behavior. Invitation redemption no
longer locks an existing User before invoking role assignment. Staff-role
deactivation, revocation and Admin reactivation use the same policy/role-first
ordering and update policy state consistently.

The remediation did not expose governance HTTP endpoints, expand capability
disclosure, modify CRM code, reconcile legacy TECHNICAL records, or change
GOV-005. GOV-003 and GOV-006 are reduced for the reviewed lifecycle paths but
remain open for uncovered privileged writers until independent security review
and broader activation evidence are complete.

Historical Phase 1A.4C planning validation recorded 21/21 Admin, role-model, assignment and
concurrency tests and 34/34 governance and invitation tests passing, alongside
a clean Django system check, no schema-affecting model changes, no planned
migrations and a clean migration graph against the isolated database. The full
broader focused
run remains limited by two pre-existing Admin audit assertions that do not
exclude the migration-generated `CAPABILITY_CATALOG_EXTENDED` event; those
assertions are not treated as remediation failures.

### Phase 1A.4C.2B governance API

Status: **Implemented / focused API and CRM consumption validation verified in
the isolated local scope; deployment remains unverified.**

The API exposes safe governance administration routes for searchable Staff
target selection, target access detail, role-default state, proposal creation
and resolution, individual mutations, governance history, and the dedicated
audited Django-superuser operation path.
Ordinary routes require an effective `STAFF_CAPABILITY_ADMIN` capability and
remain subject to the existing eligibility, restriction, separation-of-duty,
policy-version and transactional-audit rules. The superuser route requires an
authenticated active Django superuser with CRM admission and invokes the
existing direct-operation service; it does not accept client authority flags.

The shared governance read projections admit either an ordinary effective
`STAFF_CAPABILITY_ADMIN` administrator or an active Django superuser with an
active CRM role. Ordinary mutation routes remain unchanged. An admitted
superuser receives direct role-default and individual actions through the
dedicated audited route; ordinary administrators retain proposal and
separation-of-duty rules. The directory and target projections contain identifiers, safe Staff display
metadata, staff-role state, capability state and backend-permitted actions only.
Person contact and demographic fields, general Person search, exports and
exceptional restricted-access workflows remain outside this stage. Ordinary
individual changes are immediate where authorized; grant/restore/unrestrict of
`STAFF_CAPABILITY_ADMIN` remains proposal-based. Duplicate pending proposals
are returned as sanitized HTTP 409 conflicts. Focused governance API validation
The historical Phase 1A.4C manual API validation passed 21/21 tests; the current
governance remediation run passed 17/17 tests against isolated PostgreSQL. The
isolated Phase 1A.4C manual
API runtime is verified, while deployment and operational activation remain
unverified.

The role-default projection includes every active canonical CRM role and active
capability, including disabled defaults. Each row includes backend-calculated
`permitted_actions` and an `execution_mode`; a matching pending proposal
suppresses ordinary further actions, while an admitted superuser receives a
`DIRECT_SUPERUSER` mode. Target capability rows similarly expose
`permitted_action_modes`. The CRM consumes these backend descriptors rather
than reproducing authorization rules.
Proposal payloads similarly expose only the actions available to the current
administrator: proposers may cancel, while distinct authorized administrators
may approve or reject. Clients do not reproduce these governance decisions.
The earlier 12/12 additive-contract result is historical. The current
governance API suite passed 17/17, including action-mode, superuser, non-STAFF
admission and invalid-proposal-operation coverage.

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

## Phase 1A.4A effective Staff Access projection

Status: **Implemented / isolated local runtime verified; deployment and
operational runtime remain unverified.**

The API now exposes exactly `GET /api/v1/staff/access/me/` for an authenticated
Staff CRM session. The successful response contains exactly:

```json
{
  "capability_codes": ["STAFF_CAPABILITY_ADMIN"],
  "policy_version": 2
}
```

The `capability_codes` value is projected from the single approved CRM
disclosure allowlist entry, `STAFF_CAPABILITY_ADMIN`; the other capability
codes remain excluded. The endpoint admits only authenticated
sessions with an active `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER` role;
anonymous callers receive `401`, non-CRM authenticated users receive `403`, and
mobile JWTs are not accepted. Django `is_staff` or `is_superuser` alone does not
admit access.

`policy_version` is an informational snapshot of the capability-policy version,
not a grant, token, client precondition, or authorization result. The view locks
the singleton policy state and evaluates the allowlisted disclosure set in one
transaction, without mutating policy state. Responses set
`Cache-Control: private, no-store`.

An admitted Django superuser retains the existing audited superuser evaluator
boundary, including transactional `PRIVACY_SUPERUSER_BYPASS` recording, without
expanding the public response beyond the single allowlisted capability. Phase
1A.4B is implemented. This contract is source-inspected from `staff_access/urls.py`,
`views.py`, `serializers.py`, and `permissions.py`. The focused API suite passed
34/34 against isolated PostgreSQL database
`test_elevate_gdpr_phase1a4a_20261124`; the Django check, migration-drift check,
migration plan, and `git diff --check` were clean as recorded above. No schema
or migration files changed or were applied. CRM tests/build/runtime, deployment,
and cross-repository CRM consumption were unverified in the earlier 1A.4A
evidence; the later isolated Phase 1A.4C manual environment verified the live
projection contract without using the operational database.

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

### Phase 1A.4C remediation notes

The proposal endpoint rejects direct individual operations instead of creating
pending proposals that cannot be approved. Governance reasons reject obvious
credential-like content with a validation error; this is input minimization,
not a substitute for secrets handling.

### Mandatory access-control acceptance checklist

- Least privilege: **Implemented; runtime verified in the 17/17 isolated API run.**
- Deny by default: **Implemented; runtime verified in protected API tests.**
- Backend-authoritative authorization: **Implemented; runtime verified.**
- Resource scope and safe projections: **Implemented; runtime verified in API
  projections.**
- Horizontal escalation prevention: **Implemented; partially runtime verified.**
- Vertical escalation prevention: **Implemented; partially runtime verified.**
- Authorization revocation: **Implemented; runtime verified in focused paths.**
- Security-sensitive authorization-failure logging: **Implemented; verified in
  the focused 17/17 governance API run.** Denials use the canonical
  `CAPABILITY_PERMISSION_MUTATED` audit action with
  `event_type=GOVERNANCE_AUTHORIZATION_DENIED`, safe reason codes, an
  opaque target reference, and no request payload or credentials.
- Privileged-operation auditing: **Implemented; runtime verified in focused tests.**
- Functional access-control regression testing: **Verified for the 17/17 API
  governance suite; broader cross-repository coverage remains separate.**
- OWASP review: **Implemented scoped review; independent runtime security
  evidence remains unverified.**

The governance API records applicable denials for missing capability,
ineligible staff, CRM admission, prohibited execution paths and superuser
admission failures. Domain denials raised after a governance transaction
rolls back are recorded after rollback. Audit-write failures are logged
server-side without changing the original authorization response.

Target detail responses include backend-calculated `effective` and
`effective_explanation` values. The explanation identifies role-default,
individual-grant, explicit-restriction, suspended/revoked, superuser-bypass,
or no-effective-grant sources and keeps pending proposals separate from the
effective result. History combines safe proposal and direct-superuser AFTER
projections with actor, timestamp, target, operation, outcome, reason, and
before/resulting state; raw AuditEvent metadata is not exposed.

The API denial and projection behavior is implemented and the 17-test focused
governance run passed in isolated PostgreSQL. Angular validation and
independent OWASP acceptance for this batch remain separately unverified.
