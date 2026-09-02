# Historical Imports - Backend Technical Guide

Last updated: 2026-09-02

Scope: implemented V1 Historical Import behavior for backend maintainers. For the UI, see [Historical Imports - Frontend Technical Guide](../../elevate-mk-crm/docs/historical-imports-frontend.md). For non-technical operations guidance, see [Historical Data Imports - Business & Founder Guide](../../elevate-mk-crm/docs/historical-imports-business-guide.md).

## Purpose And Principles

Historical Import brings source workbook data into Elevate MK in two stages. Staging and reconciliation happen before any authoritative CRM mutation; the backend and database remain authoritative throughout.

```text
XLSX source -> ImportBatch / ImportRecord staging -> identity analysis
  -> staff reconciliation when needed -> READY_FOR_IMPORT
  -> authoritative source-specific import -> IMPORTED
```

V1 favors false negatives over false-positive Person merges. Names support review evidence but never identify a Person on their own. Imports are designed for whole-batch atomicity, provenance, and idempotency by batch lifecycle.

## Import Domain

| Model | Current role |
| --- | --- |
| `ImportBatch` | One uploaded source file, source type, lifecycle, fingerprint, timestamps, and creating User. |
| `ImportRecord` | One retained source row with raw and normalized data, validation, resolution, reviewer, final Person, and commit state. |

`ImportBatch.source_type` is `MEMBERSHIP_FORM` or `EVENTBRITE`. Batch states are:

| State | Meaning |
| --- | --- |
| `PROCESSING` | Normalization or internal analysis is in progress. |
| `STAGED` | Rows are normalized but have not completed identity analysis. Eventbrite uploads intentionally stop here. |
| `READY_FOR_REVIEW` | One or more rows require CRM_ADMIN reconciliation. |
| `READY_FOR_IMPORT` | Every valid row has a safe resolution; authoritative import is allowed. |
| `IMPORTED` | Terminal successful authoritative import. |
| `FAILED` | Safe structural ingestion or post-staging failure. |

ImportRecord supports `STAGED`, `INVALID`, `ANALYZED`, `REVIEW_REQUIRED`, `RESOLVED`, `COMMITTED`, `SKIPPED`, and `FAILED`. The primary V1 flow uses staged, invalid, review-required, resolved, and committed records; invalid authoritative-import rows retain `INVALID` status with the `SKIPPED` outcome. Outcomes are `CREATED`, `MATCHED`, `UPDATED`, and `SKIPPED`.

Records retain structured `validation_errors`, analyzer `match_candidates` and `match_evidence`, `resolution_method` and reason, optional `resolved_person`, reviewer and review timestamp, and `committed_at`. Source data remains provenance; it is not returned by authoritative-import summaries.

## Source Adapters

### Membership Form

The Membership Form XLSX adapter parses and normalizes demographics, email, mobile, profile fields, submission timestamp, and validation errors. Demographic vocabulary is normalized through Person's current supported values. Unsupported demographic values and invalid emails/URLs are retained as row validation errors rather than becoming CRM mutations.

### Eventbrite

The Eventbrite XLSX adapter ignores empty rows and Totals rows, then creates a nested normalized projection:

```text
person: first name, last name, email, mobile, city/county/country
event: Eventbrite Event ID, name, start time, timezone, location
source: provider, Order ID, order date, ticket quantity, guest value
```

Buyer fields are the only identity projection. Eventbrite Event ID is an external Event identity. Order ID, ticket quantity, guest value, and financial/payment workbook fields remain source staging/provenance; they are not Person, EventParticipation, or Membership identities. The adapter validates date/time/timezone and supported Excel values as row errors instead of allowing malformed cells to become server errors.

## Identity Analysis And Reconciliation

Analysis considers BUSINESS Persons only, including archived BUSINESS Persons. TECHNICAL Persons are excluded. It uses normalized exact email and normalized mobile; names are supporting evidence and never a name-only match.

| Result | Meaning |
| --- | --- |
| `AUTO_MATCH` | One safe exact email candidate with no contradiction. |
| `REVIEW_REQUIRED` | Multiple candidates, mobile-only evidence, or contradictory evidence. |
| `NO_MATCH` | No strong candidate. |
| `STAFF_MATCH` | CRM_ADMIN explicitly confirms an analyzer candidate as the same Person. |
| `STAFF_CREATE_NEW` | CRM_ADMIN confirms a separate Person after review. |

`DIFFERENT_PERSON` reconciliation preserves reviewed collision evidence. Mobile-only collisions can be confirmed as different people; an email collision requires explicit strong confirmation. Authoritative import re-evaluates that evidence, so a changed candidate set produces a stale-review conflict rather than an unsafe mutation.

## Reconciliation API

All endpoints are under `/api/v1/`, require an authenticated session and active operational `CRM_ADMIN`; frontend visibility is not authorization.

| Endpoint | Responsibility |
| --- | --- |
| `GET /imports/` | List batches and safe summary counts. |
| `GET /imports/{batch_id}/` | Retrieve one batch. |
| `POST /imports/membership-form/` | Upload, stage, and analyze a Membership Form workbook. |
| `POST /imports/eventbrite/` | Upload and stage an Eventbrite workbook. |
| `POST /imports/{batch_id}/analyze/` | Analyze a `STAGED` Eventbrite batch. |
| `GET /imports/{batch_id}/records/` | Paginated resolution preview. |
| `GET /imports/{batch_id}/review/` | Review queue. |
| `GET /imports/{batch_id}/review/{record_id}/` | One review record and candidates. |
| `POST /imports/{batch_id}/review/{record_id}/resolve/` | Resolve `SAME_PERSON` or `DIFFERENT_PERSON`. |
| `POST /imports/{batch_id}/import/` | Source-aware authoritative import for a ready Membership Form or Eventbrite batch. |

The resolve payload uses `resolution: SAME_PERSON` with `person_id`, or `resolution: DIFFERENT_PERSON`; `confirm_identity_override: true` is required when strong email evidence needs explicit confirmation.

## Membership Form Authoritative Import

Membership Form import locks the batch and ImportRecords, completes deterministic preflight, then mutates within one transaction.

- Matched rows reuse their resolved BUSINESS Person and fill only missing Person fields.
- Create-new rows create a BUSINESS Person after current collision/stale-review checks.
- A missing Membership is created as `ACTIVE`, with `membership_source=MEMBERSHIP_FORM` and source submission date as `joined_at`.
- Existing ACTIVE Memberships are reused. Existing FORMER Memberships block the entire batch; they are never reactivated.
- ProfessionalProfile fields are created or filled only when missing. Industry matches only one active exact-name or canonical-slug taxonomy; source text never creates a taxonomy.
- Invalid rows get the `SKIPPED` outcome. Valid rows become `COMMITTED`, then the batch becomes `IMPORTED`.

Person, Membership, and ProfessionalProfile audit events carry identifier-only import provenance. `IMPORT_BATCH_IMPORTED` records safe batch completion. An imported batch cannot be imported again.

## Eventbrite Authoritative Import

Business rule: a historical Eventbrite Buyer is `REGISTERED` for the Event associated with the order. Ticket quantity does not create additional participants, and the importer never infers `ATTENDED`.

### People

- `AUTO_MATCH` and `STAFF_MATCH` reuse the resolved BUSINESS Person and fill missing Person fields only.
- `NO_MATCH` and `STAFF_CREATE_NEW` create a BUSINESS Person after shared collision and stale-evidence policy checks.
- Repeated create-new rows in the same batch coalesce only when every populated normalized email/mobile signal agrees. A missing second signal is allowed; a populated conflicting signal fails preflight. Names never drive this coalescing.
- A Person may have ACTIVE, FORMER, or no Membership. Eventbrite neither reads Membership state as eligibility nor creates, changes, reactivates, ends, or deletes Membership.

### Events And External References

```text
EVENTBRITE + Eventbrite Event ID
  -> ExternalEventReference(reference_type=EVENT)
  -> provider-neutral Event
  -> EventParticipation(Person, Event)
```

Eventbrite Event ID is the sole Eventbrite Event identity. The same ID reuses one Event; equal names do not merge different IDs. Provider is part of identity, so a `COMMUNITY` reference with the same external ID does not collide. When a valid Event reference exists, name/date/location drift cannot create a duplicate Event; only blank Event fields are safely filled. Preflight locks and rejects an inconsistent `EVENT` reference that incorrectly carries a participation.

### Participation And Provenance

`EventParticipation` is unique by `(Person, Event)`. Repeated Eventbrite orders for the same buyer/Event reuse it; ticket quantity is not copied into participation count semantics. Order ID remains source provenance only. V1 does not invent an `ExternalEventReference(PARTICIPATION)` because Eventbrite historical rows do not identify the attendee or registration.

New participation is `REGISTERED`. Existing `REGISTERED` remains registered, `ATTENDED` remains attended, and `CANCELLED` remains cancelled. Result counts distinguish created, reused registered, and preserved non-registered rows.

The Eventbrite result contains safe aggregate counts: processed, People created/matched/enriched, Events created/reused, participations created/reused/preserved, and skipped. It exposes no raw buyer or payment data.

## Integrity, Atomicity, And Audit

Both authoritative services start with `transaction.atomic()` and a `select_for_update()` batch lock. ImportRecords are locked directly, avoiding nullable joined `FOR UPDATE` queries. Event references, matched People, Events needing fill-missing updates, and participation uniqueness are protected with direct row locks and database constraints.

Deterministic validation occurs before authoritative mutation. Any later failure rolls back People, Events, participations, references, record commits, batch state, and audit entries. The `ImportBatch` status transition prevents repeated successful submissions and audit duplication. PostgreSQL concurrency behavior still warrants a dedicated concurrent integration test when the project test policy permits it.

Audit metadata contains IDs, source type, and safe aggregate counts only. It excludes raw email/mobile, Order ID, payment data, and workbook contents.

## Testing And Maintenance

Focused tests cover adapters, row validation, identity analysis, reconciliation, authoritative Membership Form import, Eventbrite Person/Event/participation behavior, idempotency, Membership isolation, external-reference integrity, and API authorization. Tests are added as implementation evolves; their execution is controlled by the task-specific test policy.

Future sources should add a source adapter, normalized Person identity projection, lifecycle/analysis eligibility, and a source-aware authoritative service. Keep provider fields out of core Event and EventParticipation models, reuse the shared identity/reconciliation policy, preserve staged provenance, and add only a source-specific result presentation where necessary.
