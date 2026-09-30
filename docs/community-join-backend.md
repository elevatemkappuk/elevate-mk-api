# Community Native Membership Join

The public Community workflow is intentionally separate from the CRM People and historical import APIs.

## Endpoints

- `GET /api/v1/community/industries/` returns active public Industry values as `{slug, label}`.
- `POST /api/v1/community/join/` accepts the native join payload.

The POST accepts required `first_name`, `last_name`, `gender`, `age_range`, `email`, `location`, `industry`, and `job_title`, plus optional `mobile`, `phone_region`, `linkedin_url`, and `email_marketing_opt_in`. When `mobile` is supplied, `phone_region` is required and must be an ISO alpha-2 phone region such as `GB`, `GH`, `IT`, or `US`. It represents phone parsing/calling-code context only, not Member Country or `Person.location`. `Idempotency-Key` is an optional HTTP header. Client-controlled Person, Membership, account, audit, and collision fields are rejected.

Successful submissions return `202` with only:

```json
{
  "status": "accepted",
  "message": "Your membership submission has been received."
}
```

Unsafe identity or lifecycle cases return a generic `409` `SUBMISSION_REQUIRES_REVIEW` response. The public API does not reveal whether a Person exists, identity evidence, archive state, membership state, CRM IDs, or contact details.

## Processing rules

The service creates or safely reuses a BUSINESS Person, fills only missing Person/Profile fields, creates an ACTIVE Membership with `COMMUNITY_PLATFORM`, and records existing audit event types with anonymous actor handling. A former or archived Person is never reactivated, restored, or mutated. The workflow does not create Users, CommunityProfiles, directory access, connections, messaging access, Business Profiles, Events, or ticketing records.

Community phone values are parsed with the installed `phonenumbers` library. National numbers are parsed using the submitted `phone_region`; explicit `+` and `00` international numbers must also be compatible with that region. Invalid, impossible, unsupported, or contradictory values return field-level validation errors. New Community mobile values are stored as E.164. Existing populated legacy mobile values are not rewritten; Community identity evaluation may cautiously compare them by parsing with the explicitly submitted region. `Person.location` remains separate.

An exact normalized email may reuse one unambiguous non-archived BUSINESS Person only when mobile evidence is not contradictory. Name-only and mobile-only identity are never sufficient. Multiple, archived, contradictory, or colliding evidence returns the generic review response.

The POST is anonymous but explicitly CSRF-protected and throttled through the `community_join` DRF scope. The default is `10/hour`, configurable with the `COMMUNITY_JOIN_THROTTLE_RATE` environment setting. The Angular client must bootstrap and send the Django CSRF token. CORS is not used as authorization.

Idempotency canonicalization uses normalized email and canonical E.164 mobile. `phone_region` is omitted after successful phone canonicalization because it is parsing metadata. Receipts store only a SHA-256 key hash, request digest, status, and timestamps; they do not store raw email, mobile, or phone region. Reusing a key with the same canonical payload replays the safe accepted result; reusing it with a different canonical payload returns the generic conflict.

Community names are trimmed, repeated internal whitespace is collapsed, and clearly all-lowercase/all-uppercase values receive conservative human-readable casing for ordinary spaces, apostrophes, and hyphens. Meaningful mixed-case names such as `McDonald`, `MacDonald`, `de Souza`, and `van der Berg` are preserved. This does not change Staff or import name semantics.

The future Angular selector is planned to default visually to United Kingdom / `+44`, but the backend never silently assumes GB and no Country field is introduced on Person.

`email_marketing_opt_in` is an optional EMAIL-only boolean. `true` records an
affirmative `EMAIL=OPTED_IN` preference with source `COMMUNITY_JOIN`, normal
history/audit evidence, and the existing asynchronous Brevo synchronization
job. `false` and omission both mean no new preference; they do not record an
opt-out. Existing opt-ins are preserved, and public Community Join cannot
reverse an existing explicit opt-out. Marketing consent never becomes a
membership requirement. SMS marketing preferences are not supported by Join V1.
