# Campaign V1 foundation

Implemented in Phase 1:

- provider-neutral campaign draft persistence;
- normalized canonical audience criteria;
- authoritative CRM-side preparation;
- immutable recipient snapshots with preparation-time consent and exclusion reasons;
- read-only campaign and recipient APIs;
- campaign creation and preparation audit events.

Phase 1 deliberately makes no Brevo API calls. Brevo campaign-specific lists,
contact preparation, Brevo campaign drafts, editor links, provider retries,
post-snapshot consent removal, and the frontend Campaign workflow are not yet
implemented.

`SNAPSHOT_READY` is the Phase 1 state. It means the CRM recipient snapshot is
complete; it does not mean that a Brevo execution target exists or that the
campaign is ready to send.

## Phase 2 provider preparation

Phase 2 adds `POST /api/v1/marketing/campaigns/{id}/prepare-provider/`. It
re-checks current CRM consent, reuses the established Brevo contact identity
and synchronization services, creates one dedicated Brevo list per
preparation, adds ready contacts, and creates a Brevo draft from the configured
starter template. The broad `BREVO_MARKETING_LIST_ID` is never used as the
campaign recipient target.

`BREVO_MARKETING_CAMPAIGN_FOLDER_ID` is optional. Brevo email campaign drafts
do not receive a folder field. For the required contact-list creation, a blank
setting causes the client to read the actual folder of the configured base
marketing list; no folder ID is invented or defaulted. A configured positive
folder ID continues to be used directly.

Provider preparation uses `PROVIDER_PREPARING`, `PREPARED`,
`RECONCILIATION_REQUIRED`, `PROVIDER_FAILED`, and `NO_READY_RECIPIENTS` while
the Campaign carries the corresponding public lifecycle state. Retries reuse
the same preparation and list. The known Brevo list-index propagation response
is retried with bounded backoff. A campaign draft is looked up by its
deterministic name before creation; if the provider accepted a create request
but the response was lost, this lookup is the available V1 recovery mechanism.

Brevo visual editing, sending, scheduling, post-preparation consent removal,
automatic list cleanup, and the frontend Campaign workflow remain outside this
phase.
