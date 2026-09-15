# CRM marketing consent

Elevate CRM is authoritative for a Person's provider-neutral marketing preference. A CRM Person, membership, event attendance, or primary email address does not imply consent.

The first supported channel is `EMAIL`. Its effective states are:

- `UNKNOWN`: no affirmative or negative preference evidence is recorded.
- `OPTED_IN`: explicit affirmative preference evidence is current.
- `OPTED_OUT`: explicit negative preference evidence is current and marketing must not be sent.

`UNKNOWN` is represented by no current preference row, so existing and newly created People safely resolve to `UNKNOWN` unless an explicit preference is recorded. Current explicit rows store source, timestamp, and optional staff/system actor. Sources are provider-neutral values including `MEMBERSHIP_FORM`, `WEBSITE_SIGNUP`, `STAFF_RECORDED`, `HISTORICAL_IMPORT`, `MAILCHIMP`, and `OTHER`.

Each meaningful explicit recording also creates append-only `MarketingPreferenceHistory`. Repeating the same state, source, and actor is idempotent; `OPTED_IN -> OPTED_OUT -> OPTED_IN` remains fully recoverable. The corresponding `AuditEvent` action stores only state/source transitions and Person/channel identifiers, never secrets or unnecessary contact PII.

CRM staff can read `GET /api/v1/people/{person_id}/marketing-preference/`; `CRM_ADMIN` and `CRM_MANAGER` can record explicit `OPTED_IN` or `OPTED_OUT` through `POST` on the same route. `CRM_VIEWER` is read-only. The effective preference is also exposed in the read-only Person overview. Authorization uses active CRM role assignments, not Django `is_staff` or `is_superuser`.

The one-Person Mailchimp synchronization flow now uses this preference as its eligibility gate. `UNKNOWN` never creates or subscribes a Mailchimp member. An explicit `OPTED_IN` preference can create a new member with `status=subscribed`, reconcile existing subscribed contacts, and never automatically resubscribe protected provider states. An explicit `OPTED_OUT` preference does not create a missing member; it changes an existing subscribed member to Mailchimp `unsubscribed`, treats an already-unsubscribed member idempotently, and leaves cleaned or other protected states unchanged. Mailchimp status is not treated as CRM consent, and this flow does not infer or import consent from Mailchimp. Brevo transactional email is unchanged.

Meaningful preference changes transactionally enqueue one durable provider-neutral external sync job keyed to the preference history event. The consent and its append-only history commit without waiting for Mailchimp. `python manage.py process_mailchimp_sync_jobs --limit 10` processes pending work for development or a future worker/scheduler; temporary failures receive bounded retry/backoff, while permanent configuration, authentication, access, validation, API, and identity-conflict failures are recorded safely for operational review. The worker re-reads current CRM consent before acting, and repeated identical preference writes do not enqueue duplicate work. No Mailchimp inbound webhook synchronization is implemented.
