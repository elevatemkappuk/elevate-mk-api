# Elevate MK Community Platform — Implementation Checkpoint

Status: implemented checkpoint. This document describes the current code and
the implemented CommunityProfile foundation. The shared Django/DRF backend is
the authority for identity, membership, account eligibility, consent, and
lifecycle states.

## 1. System architecture

The product is split across three repositories:

| Repository | Current responsibility |
| --- | --- |
| `elevate-mk-api` | Shared Django/DRF backend, CRM domain, authentication, Community APIs, workers, and Brevo integrations |
| `elevate-mk-crm` | Angular 21 Staff CRM |
| `elevate-mk-community` | Angular 21 member-facing Community application |

The shared domain is deliberately not duplicated by either frontend:

- `people.Person` is the canonical human/CRM identity.
- `accounts.User` is the authentication account and has a one-to-one link to `Person`.
- `memberships.Membership` is the formal Elevate relationship.
- `professional_profiles.ProfessionalProfile` contains professional information.
- Community does not create separate Person, User, or Membership identities.
- CRM authorization and Community eligibility are separate: Staff CRM access is
  governed by active staff roles; Community access requires an active User linked
  to a non-archived BUSINESS Person with an ACTIVE Membership.

The frontends are separate applications using the shared API. Current local
frontend configuration uses the API at `http://localhost:8000/api/v1`;
Community development runs at `http://localhost:4201` and CRM development at
`http://localhost:4200`. Production custom-domain values are deployment
configuration, not frontend hard-coded assumptions. The established intended
Community production origin is `https://community.elevatemk.org`.

## 2. Authentication, sessions, and CSRF

The implementation uses Django server-side session/cookie authentication. It
does not use JWT. Angular sends requests with `withCredentials: true`; the API
session cookie is HttpOnly and remains scoped to the API host. The applications
do not intentionally share an SSO session.

The CSRF flow is:

1. `GET /api/v1/auth/csrf/` issues the Django CSRF cookie and returns
   `{ "csrf_token": "..." }`.
2. Community stores that returned token in memory. It does not depend on
   reading the API-origin `csrftoken` cookie from JavaScript.
3. Unsafe requests send `X-CSRFToken` and credentials.
4. Login and activation refresh the CSRF token after successful Django
   `login()`, because Django rotates the token on authentication.
5. Logout bootstraps a current token before sending its unsafe request.

The anonymous Join, Community login, activation, and password-reset request
endpoints are all explicitly CSRF-protected. CORS is not authorization.
Credentialed CORS and exact frontend origins are required. The API allows the
custom `idempotency-key` request header in CORS because Join uses it.

Relevant deployment settings are `CORS_ALLOWED_ORIGINS`,
`CORS_ALLOW_CREDENTIALS`, `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_SAMESITE`,
`CSRF_COOKIE_SAMESITE`, `SESSION_COOKIE_SECURE`, and
`CSRF_COOKIE_HTTPONLY=False`. In production, `DJANGO_DEBUG=False` makes the
session and CSRF cookies secure; the deployment currently uses `SameSite=None`
for cross-origin credentialed frontend/API requests. Cookie-domain broadening
is not part of the current independent-session architecture.

## 3. Native Community Join — J1

### Endpoints and response contract

- `GET /api/v1/community/industries/` returns active industries as public
  `{ "slug", "label" }` values.
- `POST /api/v1/community/join/` is anonymous, CSRF-protected, and scoped to
  the `community_join` throttle.

The Join body accepts required `first_name`, `last_name`, `gender`,
`age_range`, `email`, `location`, `industry`, and `job_title`. It accepts
optional `mobile`, `phone_region`, `linkedin_url`, and
`email_marketing_opt_in`. `Idempotency-Key` is an optional request header.
Client-controlled IDs, membership state/source, account fields, audit fields,
and collision overrides are rejected.

Accepted submissions return `202`:

```json
{
  "status": "accepted",
  "message": "Your membership submission has been received."
}
```

Unsafe identity or lifecycle cases return the same generic `409`:

```json
{
  "code": "SUBMISSION_REQUIRES_REVIEW",
  "detail": "Your membership submission requires review."
}
```

The public response does not reveal duplicate identity, conflicting fields,
archived state, membership state, CRM IDs, or existing account details.

### Domain writes and identity safety

An accepted Join creates or safely reuses one non-archived BUSINESS `Person`,
creates or safely reuses its `ProfessionalProfile`, and creates or preserves an
ACTIVE `Membership` with source `COMMUNITY_PLATFORM`. The profile receives the
Community job title, industry, and LinkedIn value only where the J1 enrichment
rules permit it. Populated CRM fields are not blindly overwritten; permitted
missing fields may be filled.

Identity rules are backend-authoritative:

- Email is normalized using the existing Elevate email primitive and is the
  primary safe-match signal.
- Mobile matching uses normalized mobile values and conservative legacy parsing.
- Name alone and mobile alone never establish identity.
- Email evidence may safely identify one eligible Person only when the mobile
  evidence is absent or consistent with that same Person.
- Cross-Person evidence, multiple candidates, contradictory evidence, archived
  matches, and unsafe lifecycle cases return generic review.
- Archived People are not restored.
- FORMER Memberships are not silently reactivated.
- Existing ACTIVE Memberships are preserved; no second Membership is created.

The operation is transactional. Person/Profile/Membership changes, audit events,
marketing consent changes, invitation creation, and their durable jobs commit as
one unit. Existing audit event types record Community provenance with anonymous
actor handling where appropriate. A failed transaction leaves no partial Join
state.

Join is throttled by DRF `ScopedRateThrottle` with scope `community_join`,
default `10/hour`, configurable by `COMMUNITY_JOIN_THROTTLE_RATE`. Anonymous
clients are identified using DRF's normal throttle client identity, normally the
request IP/proxy-aware address; shared public IPs therefore share the quota.

With an `Idempotency-Key`, `JoinSubmissionReceipt` stores only the SHA-256 key
hash, SHA-256 request digest, outcome/status, and timestamps. The digest uses
sorted, compact JSON over serializer-validated data after email normalization,
canonical E.164 mobile normalization, removal of `phone_region`, and stable
marketing boolean representation. Raw email, mobile, phone region, names, and
other request PII are not persisted in the receipt. Equivalent canonical
requests replay safely; a different canonical payload under the same key returns
the generic review response.

## 4. Community-specific normalization

Community names are trimmed, repeated internal whitespace is collapsed, and
obvious all-lowercase/all-uppercase names receive conservative human-readable
casing for ordinary spaces, apostrophes, and hyphens. Meaningful mixed casing
such as `McDonald`, `MacDonald`, `de Souza`, and `van der Berg` is preserved.
This is Community input normalization; Staff CRM and import naming behavior is
unchanged.

When `mobile` is supplied, `phone_region` is required and is ISO alpha-2
phonenumbers parsing metadata only. It never changes `Person.location` and is
not a Person country field. The backend uses the installed `phonenumbers`
metadata. Explicit international values must be compatible with the submitted
region; ordinary contradictions such as a GB region with a Ghanaian `+233`
number are rejected. National values are parsed using the submitted region.

New Community mobile values are stored as E.164. Existing populated legacy
mobile values are not rewritten and no historical phone migration has been
performed. During identity matching, legacy values may be compared cautiously
using the submitted region. The existing general `normalize_mobile()` semantics
remain unchanged. The frontend may visually default its selector to GB/`+44`,
but the backend never silently assumes GB.

## 5. Marketing consent during Join

`email_marketing_opt_in` is an optional EMAIL-only boolean:

- `true` records `EMAIL=OPTED_IN` with source `COMMUNITY_JOIN`, history/audit
  evidence, and a durable asynchronous Brevo sync job.
- `false` and omission do not create a new preference row and do not mean an
  explicit marketing opt-out.
- Existing `OPTED_IN` remains opted in when Join sends false/omits the field.
- Existing `OPTED_OUT` is protected and cannot be reversed by public Join.

Membership acceptance is independent from consent. Consent, preference history,
and the durable external sync job participate in the Join transaction. No
synchronous Brevo request is made by Join. SMS marketing has no Community Join
consent field, no SMS preference channel, and no Community SMS synchronization.

## 6. Account activation and invitations — J2

`community.CommunityAccountInvitation` belongs to a `Person` and contains a
public UUID (`public_id`), intended email, expiry, and lifecycle timestamps for
used, revoked, and superseded states. A conditional database constraint permits
only one current invitation per Person while preserving historical invitations.
The raw activation token is never persisted; delivery stores only its SHA-256
hash. Accepted Join creates a current invitation and a pending
`notifications.TransactionalEmailJob` for eligible People without a User.

The transactional job stores delivery metadata and a protected invitation
relationship, not a plaintext token or full URL. The worker command is:

```text
python manage.py process_background_jobs --watch
```

It processes transactional activation email first and Brevo marketing sync
second. Domain-specific commands remain available. The transactional worker
issues the raw token only immediately before delivery, hashes it, and constructs
the URL transiently as:

```text
<COMMUNITY_FRONTEND_URL>/activate/<invitation_public_uuid>/<raw_token>
```

The UUID is `public_id`, not the database primary key. The configured activation
template is `BREVO_COMMUNITY_ACTIVATION_TEMPLATE_ID`; the actual parameters are
`first_name`, `activation_url`, and `expires_in_hours`. Default invitation
expiry is 72 hours via `COMMUNITY_ACTIVATION_EXPIRY_HOURS`.

Delivery statuses include pending, processing, sent, failed, cancelled, and
delivery-uncertain. Provider errors where acceptance is ambiguous are not
blindly retried or token-rotated; stale processing leases become
`DELIVERY_UNCERTAIN`. The worker uses database locking and a lease.

### Activation endpoint

- `GET /api/v1/community/activate/<invitation_uuid>/<token>/` checks whether a
  link is usable and returns `{ "usable": true }` when valid.
- `POST /api/v1/community/activate/<invitation_uuid>/<token>/` accepts
  `password` and `confirm_password`.

Activation is anonymous, CSRF-protected, credentialed, and throttled by
`community_activation`, default `10/hour`, configurable with
`COMMUNITY_ACTIVATION_THROTTLE_RATE`. It validates the token with a timing-safe
hash comparison, rechecks the invitation, Person, active Membership, email and
account state transactionally, validates the password with Django's configured
password validators, creates one active non-staff User linked to the existing
Person, consumes the invitation, writes activation audit evidence, logs the User
in, and returns only the Community user DTO (`id`, `first_name`, `last_name`).

Errors are generic and stable: `INVALID_OR_EXPIRED_ACTIVATION`,
`PASSWORD_VALIDATION_ERROR`, and `ACCOUNT_SETUP_UNAVAILABLE`. Existing Users
are never replaced or reactivated by activation.

## 7. Community sign-in and sign-out

Community uses `POST /api/v1/community/login/`, not the CRM-shaped generic login
DTO. It accepts normalized email and password, authenticates through Django,
checks current Community eligibility, creates a session only after eligibility
passes, refreshes the CSRF token in Angular, and returns only the Community user
DTO. Invalid credentials return `400 INVALID_CREDENTIALS` with generic wording.
An ineligible account returns `403 COMMUNITY_ACCESS_UNAVAILABLE`. The endpoint
uses `community_login`, default `10/hour`, configurable by
`COMMUNITY_LOGIN_THROTTLE_RATE`.

`GET /api/v1/community/me/` returns the same minimal DTO for an authenticated
eligible Community user. `POST /api/v1/auth/logout/` is the shared CSRF-protected
logout endpoint. Community does not expose staff roles, CRM authorization, or
CRM-shaped identity data to its member-facing DTO.

## 8. Community password recovery

The request endpoint is `POST /api/v1/community/password-reset/`. It accepts an
email and always returns the same enumeration-safe `200` response for
well-formed input. Only active Users with usable passwords linked to an eligible
Community Person receive email. It uses `community_password_reset`, default
`5/hour`, configurable with `COMMUNITY_PASSWORD_RESET_THROTTLE_RATE`.

The backend controls the destination using `COMMUNITY_FRONTEND_URL` and the
path `/reset-password/<uid>/<token>`. Delivery uses the dedicated
`BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID` and only the `reset_url` parameter.
Provider failure does not disclose account existence.

Confirmation uses the shared `POST /api/v1/auth/password-reset/confirm/` endpoint
with `uid`, `token`, `new_password`, and `confirm_password`. It uses Django's
`default_token_generator` and password validators, returns controlled generic
invalid/expired-token errors, and does not auto-login after reset. Resetting the
shared `accounts.User` password changes the Elevate account password, including
for a person who may also have CRM access; UI copy therefore says “Elevate MK
account password”, not a separate Community-only password.

Implemented Community routes are `/forgot-password` and
`/reset-password/:uid/:token`, with success and invalid-link states. Existing
sessions are invalidated by Django's password session-hash behavior after reset.

## 8.1 Authenticated password change

The authenticated Account & Preferences page uses `POST
/api/v1/community/account/password/` for an eligible Community member. The
request contains `current_password`, `new_password`, and `confirm_password`.
The endpoint is session-authenticated and CSRF-protected, locks the authoritative
User row, verifies the current password, applies Django's configured validators,
rejects an unchanged password, and writes the existing `PASSWORD_CHANGED` audit
action with `COMMUNITY_SELF_SERVICE` provenance. Passwords, hashes, session
identifiers, and CSRF values are not audited or logged.

The successful mutation preserves the current browser session through Django's
`update_session_auth_hash()` mechanism. The old password no longer authenticates,
the new password does, and a subsequent Community request continues to use the
same session. The dedicated `community_account_password` throttle defaults to
`5/hour` and is configurable with `COMMUNITY_ACCOUNT_PASSWORD_THROTTLE_RATE`.
No Person, Profile, marketing preference, Connect, Brevo, or transactional-email
state is changed by this operation.

## 8.2 Authenticated mobile management

Account & Preferences also supports `PATCH
/api/v1/community/account/mobile/` for eligible authenticated members. Person's
`mobile` remains the canonical contact field; CommunityProfile does not duplicate
it. Add and change requests submit `mobile` plus `phone_region`, while removal
submits both values as empty strings. The existing phonenumbers-backed Community
normalizer validates region/number compatibility and stores genuine non-empty
updates as E.164. `phone_region` is never persisted.

The mutation blocks a submitted canonical number when it belongs to another
active BUSINESS Person, including safely normalizable legacy formatting, without
revealing the other member's identity. Ambiguous or invalid legacy values are
ignored for comparison rather than rewritten. A normalized equivalent of the
member's existing number is a no-op and preserves legacy formatting; genuine
add/change/remove operations create a safe `PERSON_UPDATED` self-service audit
event and enqueue the coalesced `PERSON_PROFILE` synchronization job. The
existing provider mapping sends E.164 mobile as `SMS` and sends an empty `SMS`
attribute on removal so stale downstream mobile state can be cleared.

The response is the refreshed masked Account summary. Mobile mutations use the
CSRF-protected `community_account_mobile` scope, default `10/hour`, configurable
with `COMMUNITY_ACCOUNT_MOBILE_THROTTLE_RATE`. Mobile editing does not alter
`directory_visible`, `email_visible`, `mobile_visible`, membership, marketing
preferences, account credentials, or identity ownership.

## 9. Community frontend state

Current Angular routes and behavior:

| Route | State |
| --- | --- |
| `/` | Redirects to `/join` |
| `/join` | Native Join form |
| `/join/success` | Join completion state |
| `/activate/:invitationId/:token` | Create-password activation |
| `/activate/invalid` | Generic invalid activation state |
| `/sign-in` | Community sign-in, guest guarded |
| `/forgot-password` | Enumeration-safe reset request |
| `/reset-password/:uid/:token` | Password reset form/success/invalid states |
| `/community` | Authenticated Community home, auth guarded |
| `/community/profile` | Authenticated owner-facing composed My Profile |
| `/community/profile/edit` | Authenticated My Profile editor |

`CommunityAuthService` owns CSRF bootstrap, Community login, activation, reset,
logout, and `/community/me` calls. The shared account-security shell is reused
by activation, sign-in, forgot password, and reset password. The onboarding
shell is used by Join and sign-in; the authenticated Community shell currently
provides a minimal welcome/sign-out experience.

The current visual language uses Elevate mustard, cream, black editorial type,
shared header/shell components, and no public-site navigation on the Community
application pages. The authenticated Community application includes the
owner-facing My Profile and Edit Profile pages described below.

### CommunityProfile foundation

The backend now includes `community.CommunityProfile`, a small Community-owned
one-to-one extension of `people.Person`. It stores only Community presentation
and onboarding state; it does not duplicate canonical CRM identity,
professional, membership, taxonomy, or account fields.

| Field | Type | Meaning |
| --- | --- | --- |
| `person` | `OneToOneField(Person, on_delete=PROTECT)` | Owning canonical Person; exposed through `person.community_profile` |
| `bio` | `TextField` | Optional plain-text Community bio, maximum 400 characters |
| `person_preexisted_community` | `BooleanField` | Whether the Person existed before original Community onboarding; defaults to `False` and is not editable |
| `review_acknowledged_at` | `DateTimeField` | Timestamp for explicit acknowledgement of the existing-record review banner |
| `created_at` | `DateTimeField` | Row creation timestamp |
| `updated_at` | `DateTimeField` | Last row update timestamp |

The model exposes the derived `review_required` property:

```text
person_preexisted_community = true
AND review_acknowledged_at IS NULL
```

This state supports the authenticated My Profile banner:

> Check your details
>
> We already had some information associated with your Elevate MK membership.
> Please review your profile and make sure everything is up to date.

#### Provenance and Join behavior

Community Join establishes provenance from the actual Person create/match
decision inside the existing transaction:

- a newly created Person receives `person_preexisted_community = false`;
- a safely matched existing Person receives `true` when the CommunityProfile is
  first established;
- an existing CommunityProfile is never rewritten by a later Join or replay.

The centralized `get_or_create_community_profile()` service creates the row
lazily and sets provenance only through `get_or_create()` defaults. This keeps
`False` permanently false and `True` permanently true after establishment.
Profile creation participates in Join's existing PostgreSQL advisory-lock and
database transaction behavior, so failed Join operations do not leave an
orphaned CommunityProfile.

There is no bulk backfill. Historical eligible Community users may continue to
exist without a CommunityProfile row. Future authenticated Profile flows may
call the centralized lazy-creation service; when historical provenance cannot
be established reliably, the created row defaults to `false` and does not show
the existing-record review banner.

The model is registered minimally in Django admin. Provenance and timestamps
are read-only there; member-facing endpoints must continue using explicit
Community-safe serializers.

### Community Profile V1 — authenticated My Profile

My Profile is an authenticated owner-facing composed profile. It is assembled
from the canonical `Person`, `ProfessionalProfile`, `Membership`,
`PersonSkill`/`Skill`, and `PersonInterest`/`Interest` records together with
the Community-owned `CommunityProfile` extension. `CommunityProfile` is not a
duplicate Person or professional-profile record.

Authenticated eligible members can update only:

- Person: `first_name`, `last_name`, `location`;
- ProfessionalProfile: `job_title`, `company`, `industry`, `career_stage`,
  `linkedin_url`;
- CommunityProfile: `bio`;
- relationships: `skills` and `interests`.

Email, mobile, demographics, membership state, marketing preferences, tags,
notes, staff roles, account state, provider state, and review provenance are
outside the self-service mutation boundary. Skills and interests use active
canonical slugs with replacement semantics: omitted means unchanged and `[]`
clears the relationship.

The backend derives completion for Name, Professional details, Bio, Skills,
and Interests. Completion is returned in the composed response and is not
persisted or independently recalculated by Angular.

The member-facing profile endpoints are:

- `GET /api/v1/community/profile/` — composed My Profile;
- `PATCH /api/v1/community/profile/` — partial canonical/profile update;
- `GET /api/v1/community/profile/options/` — active editor taxonomy options;
- `POST` and `DELETE /api/v1/community/profile/photo/` — private profile-photo
  upload/replacement/removal;
- `POST /api/v1/community/profile/review-acknowledgement/` — explicit review
  acknowledgement.

Self-service mutations use the existing append-only audit mechanism with
`metadata.source = COMMUNITY_SELF_SERVICE`. Audit changes record changed
fields/relationship actions and request context; raw profile values are not
placed in the source metadata.

Review is not an approval workflow and Profile V1 has no field-by-field
mismatch UI. Anonymous Join remains generic. After authentication, an
existing-record member receives the general Check Your Details prompt and may
review/update the canonical profile or explicitly acknowledge it through
Review Profile or Looks Good. Editing alone does not acknowledge review.

## 10. Staff CRM Community visibility

The Person overview has a dedicated read-only Community account card. Its
backend-authoritative status values are:

`ACTIVE`, `SETUP_PENDING`, `NOT_SET_UP`, and `ACCESS_UNAVAILABLE`.

The overview may safely project account/setup email, account creation time,
shared `User.last_login` labelled **Last sign in**, invitation delivery state,
and expiry where applicable. It does not expose raw tokens, token hashes, setup
URLs, provider IDs, job internals, or audit details. Membership remains a
separate card/domain concept.

The People directory now exposes the minimum list projection
`community_account_status`. The final table order is:

```text
Name | Email | Mobile | Job title | Type | Location | Status | Community
```

CRM labels are `ACTIVE` → Active, `SETUP_PENDING` → Setup pending,
`NOT_SET_UP` → Not set up, and `ACCESS_UNAVAILABLE` → Unavailable. The list
reuses the backend Community lifecycle derivation; Angular maps only the
already-authoritative enum to a visual badge. The list has no Community filter
and no Community account actions.

## 11. Brevo and background processing

Marketing preference/contact synchronization and transactional email delivery
are separate durable systems. `ExternalPersonSyncJob` is processed by the
Brevo marketing worker. `TransactionalEmailJob` is processed by the
transactional worker. The combined `process_background_jobs --watch` command
services both queues in one Railway worker while retaining separate models,
processors, retry rules, and failure states.

Marketing changes are committed and queued without waiting for Brevo. The
worker re-reads current CRM state, coalesces safe work, preserves provider
blocklist/unsubscribe protection, and distinguishes retryable failures from
terminal or uncertain outcomes. Provider credentials and message IDs are
backend-only and are not member-facing API data.

Required configuration names include `BREVO_API_KEY`,
`BREVO_MARKETING_LIST_ID`, `MARKETING_SYNC_PROVIDER`,
`BREVO_COMMUNITY_ACTIVATION_TEMPLATE_ID`,
`BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID`, and the worker polling/batch
settings. Values are deployment secrets/configuration and are not documented
here.

## 12. Security and privacy boundary

Community endpoints must use Community-specific serializers and endpoints. They
must not expose Staff CRM People endpoints or return:

- CRM internal notes, staff/internal tags, audit history, or import provenance;
- staff roles or CRM authorization state;
- invitation raw tokens, token hashes, setup URLs, or password data;
- Brevo/provider IDs, provider responses, message IDs, worker internals, or
  credentials;
- marketing administration state unless a future member-facing feature
  explicitly requires a safe projection.

Generic review and password-recovery responses are intentionally
enumeration-safe. Community eligibility is checked server-side for every
relevant authenticated or account-creation flow.

## 13. Deferred / not implemented

The following remain future scope and must not be inferred from the current
Profile V1 implementation:

- QR profile sharing;
- email/mobile self-service editing;
- messaging and broader social-graph features beyond Connections V1;
- Community events, opportunities, or other in-app content modules;
- SMS marketing consent or synchronization;
- historical E.164 migration of existing Person mobile values;
- Community filtering or account actions in the CRM People list;
- invitation resend/reissue UI or public account actions.

### Directory V1 D1 privacy foundation

Directory is an authenticated Community-member feature. Eligible Community
profiles are discoverable by default through `CommunityProfile.directory_visible`.
Members may opt out by setting it to `False`. `email_visible` and
`mobile_visible` are independent opt-in sharing preferences and default to
`False`. Disabling whole-profile visibility suppresses the entire Connect
profile but does not clear those preferences, so they remain preserved if
visibility is later enabled.

`CommunityProfile.directory_id` is a stable random non-sequential identifier
for future member-facing Directory routes and QR sharing. It is intentionally
separate from `asset_namespace_id`, which remains the private S3 ownership
namespace. Canonical email and mobile remain on Person; only their sharing
preferences are stored on CommunityProfile. Ordinary profile fields do not
have independent visibility controls in D1.

The D1 owner-facing My Profile contract returns and partially updates these
three privacy settings. D2 now provides authenticated member-only Directory
list and direct Profile read APIs. Their queryset is the privacy boundary:
only visible profiles with an eligible active Community account, active
Membership, non-archived BUSINESS Person, and CommunityProfile are eligible.
Hidden or otherwise ineligible direct targets return generic 404 responses.

D2 uses a compact paginated list projection and a richer direct Profile
projection. The bounded, case-insensitive `q` search covers canonical Person
first name, last name, and location; active Industry, Skill, and Interest
slugs are supported as AND-combined filters. Email and
mobile are omitted from lists and independently projected in direct results
only when sharing is enabled. Profile photos are represented only by a
read-time private/signed `photo_url`.

Each authenticated Directory list result also contains a small
viewer-relative relationship projection using the existing Connections states:
`NO_RELATIONSHIP`, `OUTGOING_PENDING`, `INCOMING_PENDING`, or `CONNECTED`.
It contains no contact or internal database identifiers beyond the existing
safe public connection UUID where a relationship exists. The lookup is
batched for the returned page so relationship state does not introduce a
per-card query pattern.

Directory projections exclude CRM/internal fields, demographics, marketing
preferences, audit and import data, provider state, account/invitation
internals, database IDs, raw S3 keys, and AWS metadata. Directory reads are
read-only and do not create profiles, enqueue Brevo jobs, write audits, or
modify storage. The `community_directory` throttle defaults to `60/hour` and
is configurable with `COMMUNITY_DIRECTORY_THROTTLE_RATE`. Community Connect V1
now provides the authenticated discovery and member-profile frontend for these
read APIs. Connections V1 backend foundations now provide mutual connection
requests, acceptance/decline, disconnection, and backend-authoritative
relationship/contact rules. QR sharing, messaging, recommendations, and social
graph features remain future scope.

### Connections V1 — C2 backend foundation

Community connections are mutual Person-to-Person relationships. A request is
sent to a currently discoverable eligible member and must be explicitly
accepted or declined. Accepted connections are equal in both directions.

The backend stores one canonical unordered Person pair and never copies email,
mobile, names, directory identifiers, or profile data into the relationship.
The recipient is derived as the pair member other than the persisted requester.
The states are `PENDING`, `ACCEPTED`, `DECLINED`, and `DISCONNECTED`.
Declined/disconnected pairs remain historical rows and may be reopened by a
later valid request.

General Connect discovery remains limited to profiles with
`directory_visible=True`. An accepted connection may still access a currently
eligible member's hidden Connect detail profile. This exception does not
change general search/list behavior and does not apply to pending, declined,
disconnected, or unrelated members.

Accepted connections receive mutual access to each other's canonical email and
mobile values where present, regardless of `email_visible` and
`mobile_visible`. Those flags continue to control non-connected viewers.
Contact access is calculated by the backend and disappears when the
connection is removed or either participant loses current Community
eligibility.

The C2 API uses scoped throttles for connection reads, request reads, request
creation, and relationship mutations. It does not add messaging, notifications,
followers, recommendations, blocking, QR sharing, counts, or CRM-created
relationships.

The member-facing Angular workspace for Connections V1 is documented in the
Community repository's `docs/connections.md`. That document covers the
Discover, My Connections, Requests, and Home request-preview experiences;
this document and `docs/API.md` remain authoritative for backend eligibility,
state transitions, privacy, and authorization.

### Profile Photo V1

Profile Photo V1 is an authenticated owner-facing extension of My Profile. It
uses `POST` and `DELETE` on `/api/v1/community/profile/photo/`, accepts only
JPEG, PNG, and WebP uploads up to 5 MiB, and returns the normal composed profile
projection. The backend normalizes accepted images to metadata-stripped JPEG
or PNG objects with a maximum 1024-pixel longest edge and rejects animated,
malformed, unsupported, oversized, and over-25-megapixel images. It preserves
transparency where practical and does not crop to a square.

Only an opaque generated storage object name under the Community profile-owned
asset convention is persisted:
`community/profiles/<profile-uuid>/<asset-type>/<asset>`. Profile Photo uses
`community/profiles/<profile-uuid>/profile-photos/<photo-uuid>.<ext>`, where the
profile UUID is a stable random non-PII storage namespace and the final asset
name remains independently random. It is not an authentication identifier.
`photo_url` is generated at read time through the configured default storage,
so private S3 deployments receive temporary signed URLs rather than a
persisted URL. Replacement commits the new reference before old-object deletion;
failed database writes attempt to remove the new object. Upload, replacement,
and removal use the existing
`COMMUNITY_SELF_SERVICE` audit path without storing image bytes, URLs,
credentials, or original filenames. Profile Photo does not change completion,
identity, membership, consent, Brevo, or Directory behavior.

## 14. Deployment configuration checkpoint

The backend reads these Community settings from environment configuration:

| Variable | Current default/meaning |
| --- | --- |
| `COMMUNITY_FRONTEND_URL` | `http://localhost:4201`; production intended custom origin is `https://community.elevatemk.org` |
| `COMMUNITY_JOIN_THROTTLE_RATE` | `10/hour` |
| `COMMUNITY_ACTIVATION_THROTTLE_RATE` | `10/hour` |
| `COMMUNITY_LOGIN_THROTTLE_RATE` | `10/hour` |
| `COMMUNITY_PASSWORD_RESET_THROTTLE_RATE` | `5/hour` |
| `COMMUNITY_ACTIVATION_EXPIRY_HOURS` | `72` |
| `BREVO_COMMUNITY_ACTIVATION_TEMPLATE_ID` | Required for activation delivery; no value documented |
| `BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID` | Required for Community reset delivery; no value documented |
| `CORS_ALLOWED_ORIGINS` | Local defaults include ports 4200 and 4201; production must list exact frontend origins |
| `CSRF_TRUSTED_ORIGINS` | Local defaults include ports 4200 and 4201; production must list exact frontend origins |
| `SESSION_COOKIE_SAMESITE` / `CSRF_COOKIE_SAMESITE` | `Lax` in debug, `None` otherwise unless explicitly configured |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | Automatically true when `DEBUG=False` |
| `BACKGROUND_WORKER_POLL_SECONDS` | `3` for the combined worker |
| `TRANSACTIONAL_EMAIL_WORKER_BATCH_SIZE` | `20` |
| `BREVO_SYNC_WORKER_BATCH_SIZE` | `20`, worker-capped at 100 |

The current deployment documentation describes the intended Railway split of
API/web and one combined background worker. A final production DNS/TLS and
staging Community custom-domain decision remains deployment work; do not copy
secrets into documentation.

## 15. Testing and confidence

Implemented focused coverage includes Join identity collision, enrichment,
idempotency, normalization, consent and receipt privacy; phone/provider
normalization; activation lifecycle and worker behavior; Community login/session
eligibility; password recovery; and Staff CRM Community account projection.
The People directory has focused coverage for all four Community status values,
column order, labels, tones, and the absence of extra account data.

Development work generally adds or updates focused tests and runs focused
validation first. Accumulated/full-suite execution remains a separate final
hardening step; this checkpoint does not claim that every repository suite has
been run.

## Related documentation

- [Authentication and sessions](AUTHENTICATION.md)
- [Authorization](AUTHORIZATION.md)
- [API reference](API.md)
- [Deployment and workers](DEPLOYMENT.md)
- [Community Join backend detail](community-join-backend.md)
- [Marketing consent](marketing-consent.md)
- [People domain](people-domain-backend.md)
- [Brevo CRM integration](brevo-crm-integration.md)
