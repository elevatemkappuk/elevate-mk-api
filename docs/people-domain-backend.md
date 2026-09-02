# People Domain: Backend Guide

Last updated: 2026-09-02

## Purpose and Boundaries

`people.Person` is the canonical person record for Elevate MK CRM. The CRM People API exposes only `BUSINESS` Persons. `TECHNICAL` Persons are outside this domain and resolve as `404`, including when the caller has a CRM role.

This guide describes implemented backend behavior. Endpoint-level request and response detail remains in [API.md](API.md); staff authorization rules remain in [AUTHORIZATION.md](AUTHORIZATION.md).

Related guides:

- [CRM implementation guide](../../elevate-mk-crm/docs/people-domain-frontend.md)
- [staff business guide](../../elevate-mk-crm/docs/people-domain-business-guide.md)
- [Historical Imports backend guide](historical-imports-backend.md)

Current cardinality:

```text
Person
|- 0..1 User
|- 0..1 Membership
|- 0..1 ProfessionalProfile
|- 0..* PersonSkill -> Skill
|- 0..* PersonInterest -> Interest
|- 0..* PersonTag -> Tag
|- 0..* InternalNote
|- 0..* AuditEvent
`- 0..* EventParticipation -> Event
```

## Authoritative Model

`Person` uses Django's primary key and owns the following person data:

- Required: `first_name`, `last_name`.
- Optional: `primary_email`, `mobile`, `location`, `age_range`, `gender`, and `archived_at`.
- Managed timestamps: `created_at` and `updated_at`.
- Classification: `record_type`, either `BUSINESS` (the default CRM domain) or `TECHNICAL`.

`primary_email` is intentionally not unique on `Person`. It is a CRM contact value, not an authentication identifier. `age_range` and `gender` use the canonical model vocabularies; import adapters may normalize supported source spellings before writing the canonical value.

Maintainership-relevant constraints include the Person ordering by last name, first name, and id; the User/Person and Membership/Person one-to-one constraints; and the database uniqueness constraints on taxonomy names/slugs and their Person assignment join models. `PersonTag` uses one assignment row per Person/Tag and retains lifecycle data instead of creating a replacement record on reactivation.

The canonical demographic values are `UNDER_25`, `25_29`, `30_34`, `35_39`, `40_45`, and `OVER_45` for age range; and `MALE`, `FEMALE`, `NON_BINARY`, `TRANSGENDER`, and `OTHER` for gender. Frontend labels and supported source normalization must continue to map to these values, not create competing vocabularies.

Person is not hard-deleted through the CRM API. Archive and restore are lifecycle operations on `archived_at`; archive does not change the Person's membership, profile, classifications, User, notes, or other relationships.

## Related Domains

The Person record is the root for several domains. They have distinct ownership and lifecycle rules:

| Domain | Relationship to Person | Current meaning |
| --- | --- | --- |
| Custom User | zero or one User per Person; every human User has exactly one Person | Authentication identity. User email is unique and case-normalized; names remain on Person. |
| Membership | zero or one | A Person is a Contact without membership, an Active Member with `ACTIVE`, or a Former Member with `FORMER`. The relationship label is derived, not stored on Person. |
| Professional Profile | zero or one | Optional job, company, industry, career-stage, and LinkedIn information. Person `location` remains Person-owned. |
| Skills / Interests | zero or many | Taxonomy assignments. The taxonomy entries are independent, reusable records. |
| Tags | zero or many active assignments | `PersonTag` retains assignment/removal audit lifecycle and can be reactivated rather than duplicated. |
| Internal Notes | zero or many | Append-only business notes with archive/restore lifecycle; no hard-delete application workflow. |
| Audit Events | zero or many | Immutable, append-only history. |
| Event Participation | zero or many | Separate Event domain relationship, unique per Event/Person. It is not a Membership substitute. |

Person-to-User is protected from deletion. Membership and ProfessionalProfile are also protected relations. Django `is_staff` and `is_superuser` provide framework administration access only; Elevate CRM authorization comes from active `StaffRoleAssignment` records.

Membership records `joined_at`, status, optional `ended_at`, and source. The supported source values are `WEBSITE_FORM`, `MEMBERSHIP_FORM`, `STAFF`, `COMMUNITY_PLATFORM`, and `OTHER`. An active membership cannot have an end date; a former membership requires one. A former record blocks the current normal Make Member workflow rather than being silently reactivated.

## API and Visibility

Implemented People routes are mounted under `/api/v1/`:

- `GET, POST /people/`: directory list and Contact creation.
- `POST /people/members/`: create a Person with an active Membership.
- `GET, PATCH /people/{person_id}/`: basic Person read/update.
- `POST /people/{person_id}/archive/` and `/restore/`: lifecycle operations.
- `GET /people/{person_id}/overview/`: aggregate projection for the CRM detail screen.
- `GET /people/{person_id}/audit-history/`: paginated Person audit history.
- `GET, POST /people/{person_id}/membership/` and `POST /people/{person_id}/membership/end/`: Membership read/create/end.
- `GET, POST, PATCH /people/{person_id}/professional-profile/`: Professional Profile read/create/update; `GET /industries/` lists active Industry choices.
- `GET /skills/`, `GET, POST /people/{person_id}/skills/`, and `DELETE /people/{person_id}/skills/{skill_id}/`.
- `GET /interests/`, `GET, POST /people/{person_id}/interests/`, and `DELETE /people/{person_id}/interests/{interest_id}/`.
- `GET /tags/`, `GET, POST /people/{person_id}/tags/`, and `POST /people/{person_id}/tags/{tag_id}/remove/`.
- `GET, POST /people/{person_id}/notes/`, `PATCH /people/{person_id}/notes/{note_id}/`, and note archive/restore actions.

See [API.md](API.md) for endpoint-level request and response schemas.

All People reads require an authenticated CRM staff role: `CRM_ADMIN`, `CRM_MANAGER`, or `CRM_VIEWER`. `CRM_ADMIN` and `CRM_MANAGER` perform Person, membership, profile, skill, interest, and tag writes. Internal Notes are restricted to Admin/Manager; a Viewer neither reads nor writes notes. Viewers can read the audit history, except Internal Note audit events are excluded. The backend is authoritative; UI gating is not a permission boundary.

Archived BUSINESS Persons remain directly retrievable and may appear in the directory according to `record_state`; they are not eligible for normal mutation. Missing and TECHNICAL Persons both return `404` from CRM People routes.

## Directory Query Contract

The directory is server-side, paginated, and BUSINESS-only. Current query dimensions include free-text `q`, repeated `relationship`, `location`, `industry`, `career_stage`, `interest`, `skill`, and `tag`, plus `record_state`, `ordering`, `page`, and `page_size`.

Repeated values within one filter category are alternatives; filter categories combine together. `record_state` supports `active`, `archived`, and `all`. The default is active records, last-name ordering, page 1, and page size 25. The current allowed page sizes are 25, 50, and 100.

The `relationship` filter is a derived view of membership state:

- `CONTACT`: no Membership.
- `ACTIVE_MEMBER`: Membership status `ACTIVE`.
- `FORMER_MEMBER`: Membership status `FORMER`.

The API intentionally does not expose `record_type`, User internals, Django admin flags, StaffRoleAssignment internals, or unrelated future domains in normal Person DTOs.

## Identity and Creation Safety

Contact and Member creation use the centralized People identity policy. It normalizes email by trimming/lowercasing and mobile by removing presentation punctuation. Candidate matching considers BUSINESS Persons, including archived records, and never treats a name alone as sufficient identity evidence.

When email and/or mobile collide with an existing Business Person, the initial create response requires staff review instead of silently creating or merging. A reviewed retry must include the explicit identity override confirmation where the policy requires it. The backend recomputes evidence during the retry; stale evidence returns a controlled conflict rather than trusting the client. Audit metadata records safe collision context only, not raw sensitive values.

The collision response is the structured `IDENTITY_COLLISION` `409`; the explicit retry uses `confirm_identity_override` with the reviewed candidate IDs and collision type. If those facts changed, `IDENTITY_COLLISION_STALE` prevents a decision based on outdated evidence.

This policy is shared by normal CRM creation and historical-import reconciliation rules. See [Historical Imports backend guide](historical-imports-backend.md) for import-specific resolution and provenance.

## Lifecycle, Audit, and Imports

Person writes, membership transitions, profiles, classifications, notes, taxonomy assignments, and archive/restore operations record `AuditEvent` entries through the established mutation services. Audit events are immutable; metadata is deliberately safe and excludes raw PII, note body content, and note archive reasons.

Historical Imports can create or reuse Persons through their own authoritative import services. Membership Form imports may establish Memberships; Eventbrite imports create/reuse Persons and Event Participation, not Memberships. Import provenance remains in `ImportBatch` and `ImportRecord`; normal People API writes do not emulate import workflow behavior.

Membership Form import only fills missing supported Person/Profile values when its service rules allow it; it does not overwrite populated CRM values. That source-specific enrichment decision belongs to the import service rather than normal Person PATCH behavior.

## Django Admin

Django Admin is a technical management interface, not the Staff CRM. It exposes Person classification and lifecycle administration where configured, while operational CRM authorization remains role-assignment based. Do not infer CRM access from Django `is_staff` or `is_superuser`, or from a Person's linked User.

## Extension Guidance

Keep Person as the core identity record. Add new business concepts through their own models and Person relationships rather than widening Person with unrelated fields. Preserve the BUSINESS visibility boundary, centralized identity policy, append-only audit approach, and role-based API permissions when extending this domain.

## Regression Coverage

Focused backend tests cover model/manager invariants, API authorization and BUSINESS visibility, directory filters and pagination, identity-collision and stale-evidence handling, lifecycle and audit behavior, membership/profile/taxonomy/notes workflows, and import integration where Person creation or enrichment is involved. Run only the focused module affected by a change unless a task explicitly requires broader coverage.
