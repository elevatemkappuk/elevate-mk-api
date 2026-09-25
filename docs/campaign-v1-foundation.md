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
