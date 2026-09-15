# External person references

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

## One-Person Mailchimp synchronization

`mailchimp.sync.synchronize_person_to_mailchimp(person_id=...)` synchronizes exactly one active `BUSINESS` Person. It uses only normalized `primary_email`, `first_name`, and `last_name`. A missing email, `TECHNICAL` Person, or archived Person returns a safe explicit `SKIPPED_*` result without calling Mailchimp; archived People are not automatically reintroduced to the marketing Audience.

The provider client resolves a member with Mailchimp's canonical MD5 subscriber hash of the normalized email. The one-Person synchronization flow first requires an existing BUSINESS Person with a usable primary email and an explicit CRM email preference. `UNKNOWN` does not call Mailchimp. For `OPTED_IN`, a missing member is created with `status=subscribed`, and only the CRM-owned email plus `FNAME`/`LNAME` fields are sent. An existing `subscribed` member may receive those same fields. For `OPTED_OUT`, a missing member is left absent; an existing `subscribed` member is patched to `status=unsubscribed`, while an already-unsubscribed member is a no-op. `cleaned`, `pending`, `transactional`, `archived`, and any other non-subscribed status is protected and is not weakened or changed. Mailchimp status is not imported as CRM consent.

After resolution, the existing `ExternalPersonReference` service links the Mailchimp member using provider `MAILCHIMP` and reference type `MARKETING_CONTACT`. Existing contacts without a reference are linked safely; repeated runs are idempotent and do not add duplicate references or duplicate link audit rows. A reference conflict with another Person, or a Person's existing different reference, fails safely before an existing provider contact is changed. The external-reference link/reactivation audit is the only synchronization lifecycle audit; no provider payload or credential is stored.

For controlled developer testing, run `python manage.py sync_mailchimp_person <person_id>`. The required explicit ID command reports the Person ID, safe outcome, provider member ID/status when available, and reference ID; it does not print email, name, credentials, or full Mailchimp responses. It uses the same consent and protected-state rules as automatic processing.

## Durable preference synchronization work

Each meaningful EMAIL preference history event transactionally creates one provider-neutral `ExternalPersonSyncJob` for the `MAILCHIMP` provider. Repeating an identical preference operation creates no new history row and no duplicate job. Consent persistence and history commit independently of any provider request; Mailchimp HTTP calls occur only in the worker/command after the database transaction has completed. Processing re-reads the current CRM preference, so a later opt-out cannot be undone by stale queued opt-in work.

Run pending work manually in development or from a future Railway worker/scheduler with `python manage.py process_mailchimp_sync_jobs --limit 10`. Jobs track pending, processing, succeeded, and terminal failed states, attempts, timestamps, bounded retry backoff, and safe error codes. Temporary provider/network failures are requeued up to the bounded attempt limit; configuration, authentication, audience-access, validation, API, and identity-conflict failures are recorded as terminal failures without credentials or complete provider responses. Interrupted processing is recoverable after the stale lock window. This is a one-Person preference workflow only; bulk sync, campaigns, tags/segments, inbound webhooks, engagement events, and journeys remain out of scope.
