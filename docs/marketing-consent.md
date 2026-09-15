# CRM marketing consent

Elevate CRM is authoritative for a Person's provider-neutral marketing preference. A CRM Person, membership, event attendance, or primary email address does not imply consent.

The first supported channel is `EMAIL`. Its effective states are:

- `UNKNOWN`: no affirmative or negative preference evidence is recorded.
- `OPTED_IN`: explicit affirmative preference evidence is current.
- `OPTED_OUT`: explicit negative preference evidence is current and marketing must not be sent.

`UNKNOWN` is represented by no current preference row, so existing and newly created People safely resolve to `UNKNOWN` unless an explicit preference is recorded. Current explicit rows store source, timestamp, and optional staff/system actor. Sources are provider-neutral values including `MEMBERSHIP_FORM`, `WEBSITE_SIGNUP`, `STAFF_RECORDED`, `HISTORICAL_IMPORT`, `MAILCHIMP`, and `OTHER`.

Each meaningful explicit recording also creates append-only `MarketingPreferenceHistory`. Repeating the same state, source, and actor is idempotent; `OPTED_IN -> OPTED_OUT -> OPTED_IN` remains fully recoverable. The corresponding `AuditEvent` action stores only state/source transitions and Person/channel identifiers, never secrets or unnecessary contact PII.

CRM staff can read `GET /api/v1/people/{person_id}/marketing-preference/`; `CRM_ADMIN` and `CRM_MANAGER` can record explicit `OPTED_IN` or `OPTED_OUT` through `POST` on the same route. `CRM_VIEWER` is read-only. The effective preference is also exposed in the read-only Person overview. Authorization uses active CRM role assignments, not Django `is_staff` or `is_superuser`.

The model is ready for later provider eligibility decisions: only an explicitly `OPTED_IN` email preference should qualify a Person for marketing subscription or delivery, while `UNKNOWN` and `OPTED_OUT` should not. This step does not infer consent from existing Mailchimp audiences, import existing Mailchimp states, change Mailchimp contacts, or alter Brevo transactional email. The existing one-Person Mailchimp pending-contact behavior remains an integration-development capability and is not the final marketing-eligibility rule.
