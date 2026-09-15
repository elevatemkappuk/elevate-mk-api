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

Verification failures are controlled as missing configuration, authentication failure, inaccessible/not-found audience, temporary network/API failure, or other Mailchimp API failure. No CRM endpoint, sync job, unsubscribe handler, campaign workflow, webhook, segment, journey, or frontend UI is implemented. Future Mailchimp work should treat `Person` as the source of truth, use this identity link for addressing, and add sync state/events as separate provider-neutral models if needed.
