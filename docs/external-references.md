# External person references

For the complete Brevo-specific implementation reference, see
[Brevo CRM integration](brevo-crm-integration.md). Campaign lifecycle and
provider preparation are defined in the [Campaign V1 foundation](campaign-v1-foundation.md).
This document remains focused on provider-neutral external identity and
durable-job concepts.

`external_references.ExternalPersonReference` stores provider-neutral identity links from Elevate CRM `people.Person` rows to external person records. It keeps external identifiers out of `Person`, so Elevate remains authoritative for name, email, mobile, lifecycle, and other CRM identity/contact data.

The first supported reference type is `MARKETING_CONTACT`; `MAILCHIMP` is represented as a provider value, not as a Mailchimp-specific column or domain model. The same model can later support another provider or another external person-record type without changing `Person`.

## Invariants and lifecycle

- Only `BUSINESS` People may have an external person reference. `TECHNICAL` records are rejected by model validation and the service.
- `(provider, reference_type, external_id)` is unique, so one external identity cannot point at multiple CRM People.
- `(person, provider, reference_type)` is unique, so one Person has at most one external identity of a given provider/type.
- Links have `ACTIVE` and `REVOKED` states. Revocation retains the identity for provenance and prevents silent replacement; the same identity may be explicitly reactivated through the service.
- `on_delete=PROTECT` preserves the link when a Person lifecycle operation is introduced later. Current Person archive/restore does not mutate the reference.

`attach_person_reference()` and `revoke_person_reference()` are the intended write paths. They lock the relevant row in a transaction and write append-only audit events: `EXTERNAL_PERSON_REFERENCE_LINKED`, `EXTERNAL_PERSON_REFERENCE_REACTIVATED`, and `EXTERNAL_PERSON_REFERENCE_REVOKED`. Audit metadata contains only provider, reference type, external ID, and Person/reference identifiers; no provider credentials, contact payloads, or API responses are stored.

Django Admin provides read-only technical inspection. Mailchimp verification is configured with the backend-only `MAILCHIMP_API_KEY`, `MAILCHIMP_SERVER_PREFIX`, and `MAILCHIMP_AUDIENCE_ID` settings. Run `python manage.py verify_mailchimp_connection` for a developer-only read-only `GET /3.0/lists/{audience_id}` check. The command reports only audience ID, name, and safe Mailchimp list counts when present. It never prints or returns the API key, stores an external reference, or writes to Mailchimp.

Verification failures are controlled as missing configuration, authentication failure, inaccessible/not-found audience, temporary network/API failure, or other Mailchimp API failure. Future Mailchimp work should treat `Person` as the source of truth and use this identity link for addressing.

## Brevo marketing provider compatibility

Brevo is the active marketing-provider implementation while Mailchimp remains frozen as a rollback/reference implementation. The separate `brevo_marketing` client reuses the existing backend-only `BREVO_API_KEY` setting and keeps marketing operations separate from transactional email. The provider-neutral reference and job models already store a free-form normalized provider code, so no schema migration was required to support `BREVO`; existing `MAILCHIMP` references and jobs are unchanged.

`BREVO_MARKETING_LIST_ID` is an explicit backend environment setting. The manual command `python manage.py sync_brevo_marketing_person <person_id>` uses that configured list and never creates, renames, or deletes lists. `MARKETING_SYNC_PROVIDER` defaults to `BREVO` and is validated at Django check/runtime boundaries. New meaningful EMAIL preference changes enqueue BREVO jobs; automatic dual-provider synchronization is not enabled.

The first approved profile mapping is deliberately minimal: `Person.primary_email` to the Brevo contact email, `Person.first_name` to `FIRSTNAME`, `Person.last_name` to `LASTNAME`, and an already-international `Person.mobile` value to `SMS`. The CRM field is explicitly a mobile number, but its normalization only removes visual separators and does not infer a country. Values with a `+` or `00` international prefix and a valid digit length are sent after separator normalization; ambiguous local values are omitted without failing email synchronization. `LANDLINE_NUMBER` is never populated. No address, membership, Eventbrite, ticket, internal-ID, `EXT_ID`, job, social, timezone, note, relationship, audit, or historical-import fields are synchronized, and no custom Brevo attributes are created.

One-Person synchronization uses normalized email lookup and Brevo's stable numeric contact ID for `ExternalPersonReference(provider=BREVO, reference_type=MARKETING_CONTACT)`. Existing contacts are reconciled before creation; identity/reference conflicts fail safely. New opted-in contacts are created with only the approved attributes and configured list membership. Existing non-restrictive opted-in contacts receive only missing approved attributes/list membership. Brevo email-campaign blocklisted contacts and contacts unsubscribed from the configured list are protected and are not automatically re-enabled. CRM `OPTED_OUT` does not create a missing contact; for an existing contact it sets Brevo's email-campaign blocklist only when no stronger provider state is already present. This does not alter Brevo transactional-email settings or CRM consent.

Primary-email changes create a separate `PERSON_EMAIL_MIGRATION` job with
`previous_email` and `requested_email` snapshots; they are not folded into the
coalesced profile job. The job can update the same stable Brevo contact ID only
when the current BUSINESS Person is EMAIL `OPTED_IN`, the active reference and
contact identity are known, the provider contact is unrestricted, and the
requested email is not already owned by another contact. It re-reads the
contact after updating and preserves the existing reference. Stale A -> B jobs
are superseded when CRM is already at C, while the latest job can use append-only
Person email audit history to recognize the legitimate A -> B -> C chain.

Missing/ambiguous identity, invalid or cleared email, non-eligible lifecycle or
record type, UNKNOWN/OPTED_OUT consent, provider blocklisting/list
unsubscription, and target collisions return `RECONCILIATION_REQUIRED` without
mutation. The migration never uses `forceMerge`, creates contacts or consent,
clears provider restrictions, or moves a reference. Brevo documents that
updating a blocklisted contact's email can remove the blocklist and resubscribe
it, so restricted contacts are explicitly protected.

This controlled migration path has been proven in staging for an existing
contact: CRM email A changed to B, the worker updated Brevo by numeric contact
ID, and the existing `ExternalPersonReference` remained attached to that same
contact. A retry converges as an already-synchronized result. Restricted
contacts deliberately take the reconciliation path and are not changed or
resubscribed.

## Automatic Brevo preference jobs

`python manage.py process_brevo_sync_jobs --watch` runs the durable BREVO preference queue continuously until SIGINT or SIGTERM. It polls with `BREVO_SYNC_WORKER_POLL_SECONDS` (default `3` seconds) and caps each batch with `BREVO_SYNC_WORKER_BATCH_SIZE` (default `20`, maximum `100`). It uses the same synchronization service as the one-shot command, so consent, provider-state protection, retries, and terminal failure classification are not duplicated. CRM preference requests enqueue durable work and do not wait for Brevo network calls.

CRM edits to `Person.first_name`, `last_name`, or `mobile` enqueue the
provider-neutral `PERSON_PROFILE` job type after the authoritative Person update.
A pending BREVO profile job for the same Person is coalesced, and the worker
reads current CRM values when it runs. A `primary_email` edit creates the
dedicated migration job described above; a combined edit creates both jobs, with
migration processed first. Profile work never creates a marketing contact or
changes consent; without an active BREVO reference it completes as
`SKIPPED_NO_MARKETING_CONTACT`. Existing referenced contacts receive only
`FIRSTNAME`, `LASTNAME`, and safe `SMS` profile attributes. Blank values are
sent as empty attributes to clear stale text/SMS values; unsafe local mobile
values are omitted without failing name synchronization. Profile updates never
alter email blocklisting/list-unsubscribe state and do not migrate email.

For local development, run Django in one terminal and the worker in another:

```text
Terminal 1: python manage.py runserver
Terminal 2: python manage.py process_brevo_sync_jobs --watch
```

In Railway, deploy the worker as a separate process/service using `python manage.py process_brevo_sync_jobs --watch`. It shares the application, database, `BREVO_API_KEY`, `BREVO_MARKETING_LIST_ID`, and `MARKETING_SYNC_PROVIDER=BREVO` with the web service. Webhook Basic credentials are needed by the web service receiving inbound webhooks and should not be added to the worker unless shared variables are required. Mailchimp jobs are never consumed automatically, and transactional Brevo email remains independent.

The one-shot command claims pending `BREVO` jobs of type
`EMAIL_MARKETING_PREFERENCE`, `PERSON_EMAIL_MIGRATION`, or `PERSON_PROFILE`.
It calls the same synchronization services used by the manual commands, so
consent, migration, profile, restrictive-state, identity, list, and
minimal-profile rules are not duplicated in the worker. Successful, no-op,
UNKNOWN, opted-out-without-contact, no-marketing-contact, and protected-provider
outcomes complete successfully because retrying cannot improve them.
Configuration, authentication, access, validation, API, identity-conflict, and
reconciliation-required outcomes are terminal. Network, timeout, rate-limit,
and temporary provider failures retry with bounded backoff and max-attempt
behavior; stale processing locks are recoverable.

Existing MAILCHIMP references and jobs are preserved and never reinterpreted as BREVO jobs. The existing Mailchimp worker remains a separately invoked rollback/reference path and processes only rows explicitly owned by `MAILCHIMP`; operators should not run it against historical pending rows unless Mailchimp rollback processing is intentional.

## Campaign V1 identity boundary

Campaign provider preparation reuses these stable references and synchronization
rules. It may reuse an active `BREVO` / `MARKETING_CONTACT` reference, but it
never guesses an identity, force-merges contacts, or silently relinks a stale
reference. An unresolved reference, restrictive provider state, missing current
CRM email, email identity mismatch, or contact linked to another Person is a
safe reconciliation outcome. The campaign snapshot remains immutable evidence;
the campaign-specific Brevo list is a separate operational recipient set.

The read-only Person inspection endpoint is
`GET /api/v1/people/{person_id}/brevo-integration/`. It exposes only safe
status/reason information and never exposes provider IDs or raw payloads. It
does not attach, revoke, relink, enqueue synchronization, or change consent.
Campaign retry reuses the same preparation, snapshot, list, and completed work
after an administrator or manager has resolved the underlying issue through a
supported workflow.

## Inbound Brevo marketing unsubscribe

`POST /api/v1/webhooks/brevo/marketing/` accepts only authenticated Brevo marketing webhook traffic. Brevo Basic webhook authentication is configured with `BREVO_MARKETING_WEBHOOK_USERNAME` and `BREVO_MARKETING_WEBHOOK_PASSWORD`; these credentials are separate from the API key and are compared without logging or persisting them. The route is not a CRM staff endpoint and does not use normal session/token authorization.

The only supported event is Brevo marketing `event=unsubscribe`. Brevo documents the payload `id` as the internal ID of the webhook, not as a unique delivery/event ID, so it is never used alone for replay protection. When a valid event timestamp is available, the handler derives a SHA-256 fingerprint from the event type, a digest of normalized recipient email, list context when supplied, campaign ID, event timestamp, and webhook ID as an additional component. The recipient email itself is never stored in the receipt. Brevo campaign unsubscribe payloads may omit `list_id`; those are accepted only with a valid email and reliable event timestamp. When `list_id` is supplied, the configured `BREVO_MARKETING_LIST_ID` must be present, so conflicting list context remains out of scope. The handler also consumes `email`, `ts_event`/`date_event`/`ts`, and `camp_id`. Opens, clicks, delivery, bounce, contact, list-addition, SMS, and transactional events are ignored or rejected without consent mutation. Brevo's documented marketing unsubscribe payload supplies email/list/campaign context rather than a stable contact ID, so resolution safely falls back to exact normalized primary-email matching; names and phones are never used.

An exact single BUSINESS Person receives `MarketingPreference.EMAIL=OPTED_OUT` with source `BREVO`, provider event metadata in the audit event, and the provider event timestamp when valid. Existing `BREVO` references are preserved; this webhook does not create or revoke references because the payload has no stable contact ID to attach. Missing People are acknowledged without creation. Ambiguous email identity is acknowledged as a safe conflict without mutation. Webhook receipts store only bounded event metadata and outcome, not the raw payload or email address.

Provider-originated preference mutation passes `origin_provider=BREVO` through the authoritative preference service. It preserves preference, history, and audit behavior but suppresses the outbound BREVO echo job. Repeated deliveries with the same fingerprint are acknowledged as `REPLAY_IGNORED`; deliveries without a reliable fingerprint still rely on preference idempotency. A later event with a different recipient, campaign, or occurrence timestamp is not suppressed. Authentication failures return 401, malformed/scoping failures return 400, valid ignored/handled events return 2xx, and unexpected processing failures return 5xx for retry.

## One-Person Mailchimp synchronization

`mailchimp.sync.synchronize_person_to_mailchimp(person_id=...)` synchronizes exactly one active `BUSINESS` Person. It uses only normalized `primary_email`, `first_name`, and `last_name`. A missing email, `TECHNICAL` Person, or archived Person returns a safe explicit `SKIPPED_*` result without calling Mailchimp; archived People are not automatically reintroduced to the marketing Audience.

The provider client resolves a member with Mailchimp's canonical MD5 subscriber hash of the normalized email. The one-Person synchronization flow first requires an existing BUSINESS Person with a usable primary email and an explicit CRM email preference. `UNKNOWN` does not call Mailchimp. For `OPTED_IN`, a missing member is created with `status=subscribed`, and only the CRM-owned email plus `FNAME`/`LNAME` fields are sent. An existing `subscribed` member may receive those same fields. For `OPTED_OUT`, a missing member is left absent; an existing `subscribed` member is patched to `status=unsubscribed`, while an already-unsubscribed member is a no-op. `cleaned`, `pending`, `transactional`, `archived`, and any other non-subscribed status is protected and is not weakened or changed. Mailchimp status is not imported as CRM consent.

After resolution, the existing `ExternalPersonReference` service links the Mailchimp member using provider `MAILCHIMP` and reference type `MARKETING_CONTACT`. Existing contacts without a reference are linked safely; repeated runs are idempotent and do not add duplicate references or duplicate link audit rows. A reference conflict with another Person, or a Person's existing different reference, fails safely before an existing provider contact is changed. The external-reference link/reactivation audit is the only synchronization lifecycle audit; no provider payload or credential is stored.

For controlled developer testing, run `python manage.py sync_mailchimp_person <person_id>`. The required explicit ID command reports the Person ID, safe outcome, provider member ID/status when available, and reference ID; it does not print email, name, credentials, or full Mailchimp responses. It uses the same consent and protected-state rules as automatic processing.

## Durable preference synchronization work

Each meaningful EMAIL preference history event transactionally creates one provider-neutral `ExternalPersonSyncJob` for the `MAILCHIMP` provider. Repeating an identical preference operation creates no new history row and no duplicate job. Consent persistence and history commit independently of any provider request; Mailchimp HTTP calls occur only in the worker/command after the database transaction has completed. Processing re-reads the current CRM preference, so a later opt-out cannot be undone by stale queued opt-in work.

Run pending work manually in development or from a future Railway worker/scheduler with `python manage.py process_mailchimp_sync_jobs --limit 10`. Jobs track pending, processing, succeeded, and terminal failed states, attempts, timestamps, bounded retry backoff, and safe error codes. Temporary provider/network failures are requeued up to the bounded attempt limit; configuration, authentication, audience-access, validation, API, and identity-conflict failures are recorded as terminal failures without credentials or complete provider responses. Interrupted processing is recoverable after the stale lock window. This is a one-Person preference workflow only; bulk sync, campaigns, tags/segments, inbound webhooks, engagement events, and journeys remain out of scope.
