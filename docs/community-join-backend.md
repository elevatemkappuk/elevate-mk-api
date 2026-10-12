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

Accepted eligible joins create a durable `CommunityAccountInvitation` for a
BUSINESS Person who has no `accounts.User`, plus one pending
`notifications.TransactionalEmailJob`. Invitations are historical many-to-one
records: only one current invitation may exist for a Person, while superseded,
revoked, expired, or used invitations remain preserved. A future resend/reissue
will supersede the current invitation and create a new invitation/job pair.
The pending J2.1 invitation is current but has no token hash until delivery
begins; an invitation is redeemable only after the future worker has issued its
hashed token.
J2.1 does not create a User, redeem an invitation, or call Brevo. The invitation
uses an opaque UUID in the future `/activate/<invitation-id>/<token>` URL; the
raw token and full URL are never stored. The eventual transactional template
receives `first_name`, `activation_url`, and `expires_in_hours`. This lifecycle
is independent of marketing consent and uses the configured
`COMMUNITY_ACTIVATION_EXPIRY_HOURS` (72 by default),
`COMMUNITY_FRONTEND_URL`, and `BREVO_COMMUNITY_ACTIVATION_TEMPLATE_ID` settings.

The pending job references the invitation rather than storing a plaintext
activation URL. J2.1 does not mint a token during Join. A later delivery worker
must issue and hash a token immediately before sending, then construct the URL
transiently. It must not rotate a token on every retry: if Brevo may have
accepted a send but the process crashed before marking the job sent, the job
must enter an operator-reconcilable uncertain state and preserve the token hash
rather than blindly invalidating a link that may already be in the member's
inbox. A definitive pre-send failure may be retried with a replacement token.
This ambiguity is an intentional follow-up for J2.2; raw token persistence is
not a permitted workaround.

The transactional worker command is
`python manage.py process_transactional_email_jobs --watch`. It claims jobs
with database row locks, records a processing lease, and converts stale
processing jobs to `DELIVERY_UNCERTAIN`; stale jobs are never returned to the
automatic queue. Successful sends become `SENT`. Only definitive failures that
occur before provider acceptance are retried with bounded backoff. The current
provider abstraction exposes generic delivery failures without proving whether
Brevo accepted the request, so those failures become `DELIVERY_UNCERTAIN` and
are not automatically retried. This worker is transactional-email-only and is
separate from the Brevo marketing synchronization processor. In production V1,
both processors may run in the single `process_background_jobs --watch`
Railway worker; their durable models and retry semantics remain separate.

## Community account activation redemption

`POST /api/v1/community/activate/<invitation_uuid>/<token>/` redeems a valid
activation invitation. The JSON body is:

```json
{
  "password": "a-new-password",
  "confirm_password": "a-new-password"
}
```

The request is anonymous, CSRF-protected, credentialed, and throttled by
`COMMUNITY_ACTIVATION_THROTTLE_RATE` (default `10/hour`). The raw token is
hashed with SHA-256 and compared using a timing-safe comparison; it is never
stored, returned, or audited.

Successful redemption creates exactly one active non-staff User linked to the
already-resolved Person, marks the invitation used, establishes the normal
Django session, and returns only `id`, `first_name`, and `last_name`.

`GET /api/v1/community/me/` requires that session and an ACTIVE Membership. It
returns the same Community-safe representation. Existing Users—including
active usable, inactive, and unusable-password Users—receive the generic
`ACCOUNT_SETUP_UNAVAILABLE` response; they are never replaced or reactivated.

Activation failures use `INVALID_OR_EXPIRED_ACTIVATION`,
`PASSWORD_VALIDATION_ERROR`, or `ACCOUNT_SETUP_UNAVAILABLE`. Password errors
do not consume the invitation. The existing CSRF-protected
`POST /api/v1/auth/logout/` remains the logout endpoint.

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
