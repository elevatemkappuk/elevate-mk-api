from django.db import migrations, transaction


CATALOGUE_EXTENSION_ACTION = "CAPABILITY_CATALOG_EXTENDED"


def seed_legal_hold_admin(apps, schema_editor):
    CapabilityDefinition = apps.get_model("staff_access", "CapabilityDefinition")
    CapabilityPolicyState = apps.get_model("staff_access", "CapabilityPolicyState")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    with transaction.atomic():
        capability, _created = CapabilityDefinition.objects.update_or_create(
            code="LEGAL_HOLD_ADMIN",
            defaults={
                "name": "Legal hold administration",
                "description": "Place, release and evaluate legal holds through governed workflows.",
                "is_active": True,
            },
        )
        state = CapabilityPolicyState.objects.select_for_update().get(singleton_key=1)
        existing_event = AuditEvent.objects.filter(
            action=CATALOGUE_EXTENSION_ACTION,
            entity_type="CapabilityPolicyState",
            entity_id=str(state.pk),
            metadata__added_capability_code=capability.code,
        ).first()
        if existing_event:
            return
        previous_version = state.version
        state.version = previous_version + 1
        state.reason = "Phase 1A.3 capability catalogue extension"
        state.save(update_fields=["version", "reason", "updated_at"])
        AuditEvent.objects.create(
            actor_user=None,
            action=CATALOGUE_EXTENSION_ACTION,
            entity_type="CapabilityPolicyState",
            entity_id=str(state.pk),
            changes={
                "added_capabilities": [{"code": capability.code, "name": capability.name}],
                "previous_policy_version": previous_version,
                "new_policy_version": state.version,
            },
            metadata={
                "source": "staff_access.0008_legal_hold_admin",
                "actor_type": "SYSTEM_MIGRATION",
                "added_capability_code": capability.code,
                "policy_version": state.version,
            },
            audit_domain="PRIVACY",
            provenance_source="MIGRATION",
            provenance_id="staff_access.0008_legal_hold_admin",
            provenance_trusted=True,
            retention_class="PRIVACY_AUDIT",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0022_auditeventsubject_legalhold_legalholdreleaserequest_and_more"),
        ("staff_access", "0007_capability_governance"),
    ]

    operations = [migrations.RunPython(seed_legal_hold_admin, migrations.RunPython.noop)]
