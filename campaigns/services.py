from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from marketing_preferences.audience import annotate_email_marketing_classification, build_active_people_selection
from marketing_preferences.models import MarketingPreference
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
