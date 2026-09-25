import re
import time

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from marketing_preferences.audience import annotate_email_marketing_classification, build_active_people_selection
from marketing_preferences.models import MarketingPreference
from marketing_preferences.services import get_effective_marketing_preference
from brevo_marketing.client import BrevoMarketingClient
from brevo_marketing.exceptions import (
    BrevoMarketingError,
    BrevoMarketingIdentityConflictError,
    BrevoMarketingPropagationDelay,
    BrevoMarketingTemporaryError,
)
from brevo_marketing.sync import BrevoPersonSyncOutcome, synchronize_person_to_brevo
from people.services import normalize_email

from .models import Campaign, CampaignPreparation, CampaignRecipientSnapshot


@transaction.atomic
def prepare_campaign_snapshot(*, campaign_id, actor_user=None):
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    if campaign.status == Campaign.Status.PREPARING:
        raise RuntimeError("Campaign preparation is already in progress.")
    if campaign.status != Campaign.Status.DRAFT:
        raise RuntimeError("Only draft campaigns can be prepared in this phase.")

    attempt_number = campaign.preparations.aggregate(max_attempt=Max("attempt_number"))["max_attempt"]
    attempt_number = (attempt_number or 0) + 1
    preparation = CampaignPreparation.objects.create(
        campaign=campaign,
        attempt_number=attempt_number,
        status=CampaignPreparation.Status.PREPARING,
    )
    campaign.status = Campaign.Status.PREPARING
    campaign.current_preparation = preparation
    campaign.save(update_fields=["status", "current_preparation", "updated_at"])
    record_audit_event(
        action=AuditEvent.Action.CAMPAIGN_PREPARATION_STARTED,
        entity_type="Campaign",
        entity_id=campaign.id,
        actor_user=actor_user,
        metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": attempt_number, "status_from": Campaign.Status.DRAFT, "status_to": Campaign.Status.PREPARING},
    )

    queryset = annotate_email_marketing_classification(
        build_active_people_selection(campaign.audience_selection, campaign.audience_ordering)
    )
    snapshots = []
    for person in queryset:
        classification = person.audience_classification
        included = classification == "ELIGIBLE"
        snapshots.append(CampaignRecipientSnapshot(
            preparation=preparation,
            person=person,
            email_snapshot=normalize_email(person.primary_email),
            first_name_snapshot=person.first_name,
            last_name_snapshot=person.last_name,
            consent_state_snapshot=(MarketingPreference.State.OPTED_IN if person.audience_opted_in else MarketingPreference.State.OPTED_OUT if person.audience_opted_out else MarketingPreference.State.UNKNOWN),
            decision=CampaignRecipientSnapshot.Decision.INCLUDED if included else CampaignRecipientSnapshot.Decision.EXCLUDED,
            exclusion_reason=None if included else classification,
        ))
    CampaignRecipientSnapshot.objects.bulk_create(snapshots)
    selected_count = len(snapshots)
    included_count = sum(snapshot.decision == CampaignRecipientSnapshot.Decision.INCLUDED for snapshot in snapshots)
    excluded_count = selected_count - included_count
    now = timezone.now()
    preparation.status = CampaignPreparation.Status.SNAPSHOT_READY
    preparation.completed_at = now
    preparation.selected_count = selected_count
    preparation.included_count = included_count
    preparation.excluded_count = excluded_count
    preparation.save(update_fields=["status", "completed_at", "selected_count", "included_count", "excluded_count", "updated_at"])
    campaign.status = Campaign.Status.SNAPSHOT_READY
    campaign.save(update_fields=["status", "updated_at"])
    metadata = {"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": attempt_number, "selected_count": selected_count, "included_count": included_count, "excluded_count": excluded_count}
    record_audit_event(action=AuditEvent.Action.CAMPAIGN_RECIPIENT_SNAPSHOT_CREATED, entity_type="CampaignPreparation", entity_id=preparation.id, actor_user=actor_user, metadata=metadata)
    record_audit_event(action=AuditEvent.Action.CAMPAIGN_PREPARATION_SNAPSHOT_READY, entity_type="Campaign", entity_id=campaign.id, actor_user=actor_user, metadata=metadata)
    return campaign


PROVIDER_SUCCESS_OUTCOMES = {
    BrevoPersonSyncOutcome.CREATED_MARKETING_CONTACT,
    BrevoPersonSyncOutcome.UPDATED_MARKETING_CONTACT,
    BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED,
    BrevoPersonSyncOutcome.EXISTING_PROVIDER_CONTACT_LINKED,
}
PROVIDER_RECONCILIATION_OUTCOMES = {
    BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
    BrevoPersonSyncOutcome.SKIPPED_PROTECTED_PROVIDER_STATE,
}
PROVIDER_BACKOFF_SECONDS = (0, 1, 3, 10, 30)


def _safe_campaign_list_name(campaign, preparation):
    value = re.sub(r"[\x00-\x1f<>\"/\\]+", " ", campaign.name or "Campaign")
    value = " ".join(value.split())
    prefix = f"Elevate Campaign {campaign.id} | "
    suffix = f" | Prep {preparation.attempt_number}"
    return f"{prefix}{value[:max(1, 100 - len(prefix) - len(suffix))]}{suffix}"


def _provider_error_code(error):
    if isinstance(error, BrevoMarketingIdentityConflictError):
        return "BREVO_IDENTITY_CONFLICT"
    if isinstance(error, BrevoMarketingPropagationDelay):
        return "BREVO_LIST_PROPAGATION_DELAY"
    if isinstance(error, BrevoMarketingTemporaryError):
        return "BREVO_TEMPORARY"
    return "BREVO_FAILURE"


def _safe_provider_error(error):
    return " ".join(str(error or "Provider preparation failed.").split())[:500]


def _mark_snapshot(snapshot, *, outcome, contact_id=None, error_code=None, error_message=None):
    snapshot.brevo_contact_id = contact_id
    snapshot.provider_outcome = outcome
    snapshot.provider_error_code = error_code
    snapshot.provider_error_message = error_message
    snapshot.save(update_fields=["brevo_contact_id", "provider_outcome", "provider_error_code", "provider_error_message"])


@transaction.atomic
def prepare_campaign_provider(*, campaign_id, actor_user=None, client=None, sleep_fn=None, backoff_seconds=PROVIDER_BACKOFF_SECONDS):
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    preparation = CampaignPreparation.objects.select_for_update().filter(
        pk=campaign.current_preparation_id,
        campaign=campaign,
    ).first()
    if preparation is None:
        raise RuntimeError("The campaign has no current preparation.")
    if preparation.status == CampaignPreparation.Status.PROVIDER_PREPARING:
        raise RuntimeError("Provider preparation is already in progress.")
    if preparation.status not in (CampaignPreparation.Status.SNAPSHOT_READY, CampaignPreparation.Status.PROVIDER_FAILED):
        raise RuntimeError("The current preparation is not ready for provider preparation.")
    if campaign.status not in (Campaign.Status.SNAPSHOT_READY, Campaign.Status.PROVIDER_FAILED):
        raise RuntimeError("The campaign is not ready for provider preparation.")

    was_retry = preparation.status == CampaignPreparation.Status.PROVIDER_FAILED
    preparation.status = CampaignPreparation.Status.PROVIDER_PREPARING
    preparation.provider_error_code = None
    preparation.provider_error_message = None
    preparation.save(update_fields=["status", "provider_error_code", "provider_error_message", "updated_at"])
    campaign.status = Campaign.Status.PREPARING
    campaign.save(update_fields=["status", "updated_at"])
    record_audit_event(
        action=AuditEvent.Action.CAMPAIGN_PROVIDER_RETRY if was_retry else AuditEvent.Action.CAMPAIGN_PROVIDER_PREPARATION_STARTED,
        entity_type="CampaignPreparation",
        entity_id=preparation.id,
        actor_user=actor_user,
        metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number},
    )

    snapshots = list(preparation.recipient_snapshots.select_related("person").filter(decision=CampaignRecipientSnapshot.Decision.INCLUDED).order_by("id"))
    candidates = []
    for snapshot in snapshots:
        if snapshot.provider_outcome == "ADDED_TO_CAMPAIGN_LIST":
            candidates.append(snapshot)
            continue
        preference = get_effective_marketing_preference(person=snapshot.person)
        if preference.state != MarketingPreference.State.OPTED_IN:
            _mark_snapshot(snapshot, outcome="SKIPPED_CURRENT_CONSENT", error_code="CURRENT_CONSENT_NOT_OPTED_IN")
            continue
        if snapshot.provider_outcome in {"RECONCILIATION_REQUIRED", "SKIPPED_CURRENT_CONSENT"}:
            continue
        candidates.append(snapshot)

    if not candidates:
        has_reconciliation = any(snapshot.provider_outcome == "RECONCILIATION_REQUIRED" for snapshot in snapshots)
        preparation.status = CampaignPreparation.Status.RECONCILIATION_REQUIRED if has_reconciliation else CampaignPreparation.Status.NO_READY_RECIPIENTS
        preparation.completed_at = timezone.now()
        preparation.save(update_fields=["status", "completed_at", "updated_at"])
        campaign.status = Campaign.Status.RECONCILIATION_REQUIRED if has_reconciliation else Campaign.Status.NO_READY_RECIPIENTS
        campaign.save(update_fields=["status", "updated_at"])
        record_audit_event(
            action=AuditEvent.Action.CAMPAIGN_RECONCILIATION_REQUIRED if has_reconciliation else AuditEvent.Action.CAMPAIGN_PROVIDER_PREPARATION_COMPLETED,
            entity_type="CampaignPreparation",
            entity_id=preparation.id,
            actor_user=actor_user,
            metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "ready_count": 0},
        )
        return campaign

    try:
        client = client or BrevoMarketingClient.from_settings(require_marketing_list=True)
        if preparation.brevo_list_id is None:
            campaign_list = client.create_campaign_list(name=_safe_campaign_list_name(campaign, preparation))
            preparation.brevo_list_id = campaign_list.list_id
            preparation.save(update_fields=["brevo_list_id", "updated_at"])
            record_audit_event(
                action=AuditEvent.Action.CAMPAIGN_BREVO_LIST_CREATED,
                entity_type="CampaignPreparation",
                entity_id=preparation.id,
                actor_user=actor_user,
                metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "brevo_list_id": preparation.brevo_list_id},
            )

        ready_count = 0
        reconciliation_count = 0
        for snapshot in candidates:
            if snapshot.provider_outcome == "ADDED_TO_CAMPAIGN_LIST":
                ready_count += 1
                continue
            try:
                if snapshot.brevo_contact_id is None:
                    result = synchronize_person_to_brevo(person_id=snapshot.person_id, client=client, actor_user=actor_user)
                    if result.outcome in PROVIDER_RECONCILIATION_OUTCOMES or result.contact_id is None:
                        _mark_snapshot(snapshot, outcome="RECONCILIATION_REQUIRED", contact_id=result.contact_id, error_code="BREVO_RECONCILIATION_REQUIRED", error_message=result.reason)
                        reconciliation_count += 1
                        continue
                    snapshot.brevo_contact_id = result.contact_id
                _mark_snapshot(snapshot, outcome="CONTACT_READY", contact_id=snapshot.brevo_contact_id)
                client.add_contact_to_list(list_id=preparation.brevo_list_id, contact_id=snapshot.brevo_contact_id)
                _mark_snapshot(snapshot, outcome="ADDED_TO_CAMPAIGN_LIST", contact_id=snapshot.brevo_contact_id)
                ready_count += 1
            except BrevoMarketingIdentityConflictError as error:
                _mark_snapshot(snapshot, outcome="RECONCILIATION_REQUIRED", contact_id=snapshot.brevo_contact_id, error_code=_provider_error_code(error), error_message=_safe_provider_error(error))
                reconciliation_count += 1
            except BrevoMarketingTemporaryError as error:
                _mark_snapshot(snapshot, outcome="PROVIDER_FAILED", contact_id=snapshot.brevo_contact_id, error_code=_provider_error_code(error), error_message=_safe_provider_error(error))
                raise
            except BrevoMarketingError as error:
                _mark_snapshot(snapshot, outcome="PROVIDER_FAILED", contact_id=snapshot.brevo_contact_id, error_code=_provider_error_code(error), error_message=_safe_provider_error(error))
                raise

        if reconciliation_count:
            preparation.status = CampaignPreparation.Status.RECONCILIATION_REQUIRED
            preparation.completed_at = timezone.now()
            preparation.provider_error_code = "BREVO_RECONCILIATION_REQUIRED"
            preparation.provider_error_message = "One or more included recipients require provider reconciliation."
            preparation.save(update_fields=["status", "completed_at", "provider_error_code", "provider_error_message", "updated_at"])
            campaign.status = Campaign.Status.RECONCILIATION_REQUIRED
            campaign.save(update_fields=["status", "updated_at"])
            record_audit_event(action=AuditEvent.Action.CAMPAIGN_RECONCILIATION_REQUIRED, entity_type="CampaignPreparation", entity_id=preparation.id, actor_user=actor_user, metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "ready_count": ready_count, "reconciliation_count": reconciliation_count, "brevo_list_id": preparation.brevo_list_id})
            return campaign

        record_audit_event(action=AuditEvent.Action.CAMPAIGN_LIST_POPULATED, entity_type="CampaignPreparation", entity_id=preparation.id, actor_user=actor_user, metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "ready_count": ready_count, "brevo_list_id": preparation.brevo_list_id})
        campaign_name = _safe_campaign_list_name(campaign, preparation)
        campaign_draft = None
        for index, delay in enumerate(backoff_seconds):
            if index and sleep_fn is not None:
                sleep_fn(delay)
            elif index:
                time.sleep(delay)
            campaign_draft = client.find_draft_campaign_by_name(name=campaign_name)
            if campaign_draft is not None:
                break
            try:
                campaign_draft = client.create_email_campaign_draft(
                    name=campaign_name,
                    subject=campaign.name,
                    template_id=settings.BREVO_MARKETING_STARTER_TEMPLATE_ID,
                    list_id=preparation.brevo_list_id,
                )
                break
            except BrevoMarketingPropagationDelay:
                if index == len(backoff_seconds) - 1:
                    raise
        if campaign_draft is None:
            raise BrevoMarketingTemporaryError("Brevo campaign draft could not be established.")
        preparation.brevo_campaign_id = campaign_draft.campaign_id
        preparation.save(update_fields=["brevo_campaign_id", "updated_at"])
        record_audit_event(action=AuditEvent.Action.CAMPAIGN_BREVO_DRAFT_CREATED, entity_type="CampaignPreparation", entity_id=preparation.id, actor_user=actor_user, metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "brevo_list_id": preparation.brevo_list_id, "brevo_campaign_id": preparation.brevo_campaign_id})
    except BrevoMarketingError as error:
        preparation.status = CampaignPreparation.Status.PROVIDER_FAILED
        preparation.completed_at = timezone.now()
        preparation.provider_error_code = _provider_error_code(error)
        preparation.provider_error_message = _safe_provider_error(error)
        preparation.save(update_fields=["status", "completed_at", "provider_error_code", "provider_error_message", "updated_at"])
        campaign.status = Campaign.Status.PROVIDER_FAILED
        campaign.save(update_fields=["status", "updated_at"])
        record_audit_event(action=AuditEvent.Action.CAMPAIGN_PROVIDER_FAILURE, entity_type="CampaignPreparation", entity_id=preparation.id, actor_user=actor_user, metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "brevo_list_id": preparation.brevo_list_id, "error_code": preparation.provider_error_code})
        return campaign

    preparation.status = CampaignPreparation.Status.PREPARED
    preparation.completed_at = timezone.now()
    preparation.save(update_fields=["status", "completed_at", "updated_at"])
    campaign.status = Campaign.Status.PREPARED
    campaign.save(update_fields=["status", "updated_at"])
    record_audit_event(action=AuditEvent.Action.CAMPAIGN_PROVIDER_PREPARATION_COMPLETED, entity_type="CampaignPreparation", entity_id=preparation.id, actor_user=actor_user, metadata={"campaign_id": campaign.id, "preparation_id": preparation.id, "attempt_number": preparation.attempt_number, "ready_count": ready_count, "brevo_list_id": preparation.brevo_list_id, "brevo_campaign_id": preparation.brevo_campaign_id})
    return campaign
