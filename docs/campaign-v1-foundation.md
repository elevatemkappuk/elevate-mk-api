# Campaign V1 foundation

Campaign V1 is the CRM-owned audience and preparation workflow. Elevate owns
**WHO**: audience criteria, CRM consent eligibility, campaign records,
immutable recipient evidence, current-state rechecks, provider identity
resolution, dedicated recipient lists, draft preparation, and reconciliation
visibility. Brevo owns **WHAT/WHEN**: email design, final content and subject,
preview/test, scheduling, sending, and delivery.

The Angular workflow is described in the [Staff CRM frontend guide](../../elevate-mk-crm/docs/brevo-crm-frontend-guide.md).
Provider identity and consent rules are defined in the [Brevo integration guide](brevo-crm-integration.md).

## End-to-end workflow

1. Staff use People criteria in Audience Preview.
2. Preview reports selected, eligible, and excluded counts using CRM data only.
3. An authorized Admin or Manager continues to Campaign creation and supplies
   a campaign name. The normalized criteria are stored with the Campaign.
4. **Prepare recipients** re-evaluates current CRM EMAIL consent and creates an
   immutable `CampaignRecipientSnapshot` with selected, included, excluded,
   and safe exclusion reasons.
5. **Prepare in Brevo** re-checks current consent and provider identity,
   synchronizes eligible contacts, creates or reuses one campaign-specific
   list, and creates or reuses a Brevo draft.
6. Staff edit, preview/test, schedule, and send from Brevo. Elevate does not
   provide an email designer or send/schedule operation in V1.

Audience Preview never calls Brevo, changes consent, creates contacts, or
reserves an audience. A snapshot is historical CRM evidence; it is not the
mutable provider recipient list. Provider preparation uses the dedicated list
and never uses the broad `BREVO_MARKETING_LIST_ID` as the campaign target.

## API and lifecycle

The implemented endpoints are:

```text
POST /api/v1/marketing/audiences/preview/
POST /api/v1/marketing/campaigns/
GET  /api/v1/marketing/campaigns/
GET  /api/v1/marketing/campaigns/{id}/
POST /api/v1/marketing/campaigns/{id}/prepare/
POST /api/v1/marketing/campaigns/{id}/prepare-provider/
GET  /api/v1/marketing/campaigns/{id}/recipients/
```

CRM Admins and Managers may create campaigns, prepare snapshots, prepare in
Brevo, and retry supported provider preparation. CRM Viewers have read-only
campaign and recipient visibility. The recipients endpoint is paginated with
a maximum page size of 100.

Campaign and preparation states are intentionally distinct from a simple
“ready to send” flag:

| State | Meaning |
| --- | --- |
| `DRAFT` | Criteria and campaign name exist; no snapshot yet. |
| `PREPARING` / `PROVIDER_PREPARING` | The relevant CRM or provider operation is running. |
| `SNAPSHOT_READY` | The immutable CRM snapshot exists; this is not provider readiness. |
| `PREPARED` | Included recipients are provider-ready and a Brevo draft exists or was reused. |
| `PROVIDER_FAILED` | Provider preparation failed without a completed safe provider result. |
| `RECONCILIATION_REQUIRED` | One or more included recipients need safe identity, consent, or provider-state review. The whole campaign remains blocked in V1. |
| `NO_READY_RECIPIENTS` | No included recipient can be prepared safely. |

Retries reuse the same `CampaignPreparation`, immutable snapshot, dedicated
list, and completed recipient work. Draft lookup by deterministic name happens
before draft creation. A known Brevo list-propagation/no-contacts condition
uses only the existing bounded retry/backoff behavior. There is no automatic
list cleanup, send, schedule, or partial campaign readiness.

## Provider preparation contract

The configured positive `BREVO_MARKETING_STARTER_TEMPLATE_ID` is required for
draft creation. `BREVO_MARKETING_CAMPAIGN_FOLDER_ID` is optional because
campaign folders are unavailable on the Brevo Free plan. When blank, no folder
ID is invented or defaulted; dedicated-list creation derives the actual folder
of the configured base list. A configured valid folder remains supported. The
draft payload uses the Campaign name as a deterministic, provider-required
editable starter subject. It is not a new Elevate content field; final subject
and content remain owned by Brevo.

Contact synchronization uses the established Brevo identity/reference rules:
exact matching only, stable references, minimal approved profile fields, and
no fuzzy identity matching or force merge. SMS is optional profile data. If
Brevo rejects a non-empty optional SMS value specifically as an invalid phone,
the same synchronization operation retries exactly once without SMS. CRM
mobile and consent are unchanged. Country-aware E.164 normalization remains a
separate future TODO.

## Reconciliation review and retry

`RECONCILIATION_REQUIRED` intentionally blocks the whole Campaign V1
preparation. It does not mean that a partial campaign is ready: no draft is
created while any included recipient remains unresolved. Completed safe
recipients remain auditable and reusable on retry, but unsafe recipients are
never silently placed in the operational list.

The recipient API exposes snapshot names, decisions, outcomes, and only safe
allowlisted reason codes. It does not expose Brevo contact IDs, external
reference IDs, raw provider messages, or credentials. Current live Person
inspection uses these safe diagnostic codes:

- `BREVO_CONTACT_RESTRICTED`: Brevo has a restrictive email state; Elevate
  will not automatically unblock or resubscribe it.
- `BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE`: an active CRM reference
  points to a provider contact that cannot be found.
- `BREVO_EMAIL_IDENTITY_MISMATCH`: the linked contact's email identity differs
  from the current CRM email.
- `BREVO_CRM_EMAIL_MISSING`: a current usable CRM email is required to verify
  the link.
- `BREVO_CONTACT_LINKED_TO_OTHER_PERSON`: the contact is already linked to
  another active CRM Person.
- `BREVO_CONTACT_IDENTITY_CONFLICT`: safe fallback for other ambiguities.

Historical snapshot outcomes may retain the generic
`BREVO_CONTACT_IDENTITY_CONFLICT`; documentation or live inspection must not
rewrite historical snapshot evidence. After an administrator resolves an
underlying issue through an existing supported workflow, Admins or Managers
may deliberately retry provider preparation. The retry re-evaluates unresolved
recipients and current CRM consent, preserves successful work, and proceeds to
draft lookup/creation only when every included recipient is safe.

## Deliberate manual validation

The controlled validation path has demonstrated the following without sending
or scheduling a campaign:

- a single-recipient preparation can reuse an existing Brevo contact, create
  one dedicated list, populate it, and create a starter-template draft;
- an invalid optional SMS response falls back to email-only synchronization
  without changing CRM mobile or consent;
- restrictive provider states, missing references, and identity mismatches
  remain visible reconciliation cases and block the whole campaign;
- a valid two-recipient preparation reaches `PREPARED`, with the dedicated
  list containing exactly the two included snapshot recipients and the draft
  targeting that list, with no excluded or extra recipient and no send or
  schedule.

## Scope and future work

V1 does not include a full reconciliation management system, automatic
unblock/resubscribe, force merge or automatic relink, saved audiences, tags,
journeys, post-`PREPARED` consent removal from the mutable provider list,
automatic list cleanup, campaign send/schedule controls, or a Brevo editor
inside Elevate. Future work may add country-aware E.164 normalization,
explicit administrative identity repair, stronger post-prepared consent
hardening, and safe editor deep-linking. Folder handling should be revisited
only if the Brevo plan exposes campaign folders.
