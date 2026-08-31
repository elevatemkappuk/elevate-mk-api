from django.db import transaction
from django.db.models import Q

from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.normalization import clean_text, normalize_mobile
from people.models import Person


def analyze_import_batch(batch):
    """Analyze staged identity evidence without mutating authoritative CRM records."""
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.status not in (ImportBatch.Status.STAGED, ImportBatch.Status.ANALYZED, ImportBatch.Status.READY_FOR_REVIEW):
            raise ValueError("Import batch is not eligible for identity analysis.")
        records = batch.records.filter(status__in=[ImportRecord.Status.STAGED, ImportRecord.Status.RESOLVED, ImportRecord.Status.REVIEW_REQUIRED]).exclude(
            status=ImportRecord.Status.COMMITTED
        ).exclude(resolution_method__in=[
            ImportRecord.ResolutionMethod.STAFF_MATCH,
            ImportRecord.ResolutionMethod.STAFF_CREATE_NEW,
        ])
        review_required = False
        for record in records:
            classification = analyze_import_record(record)
            review_required = review_required or classification == ImportRecord.Status.REVIEW_REQUIRED
        batch.status = ImportBatch.Status.READY_FOR_REVIEW if review_required else ImportBatch.Status.ANALYZED
        batch.save(update_fields=["status", "updated_at"])
    return batch


def analyze_import_record(record):
    data = record.normalized_data
    email = data.get("email")
    mobile = data.get("mobile")
    candidates = Person.objects.business()
    query = Q()
    if email:
        query |= Q(primary_email__iexact=email)
    if mobile:
        query |= Q(mobile__iexact=mobile)
    people = list(candidates.filter(query).distinct()) if query else []
    snapshots = [_candidate_snapshot(person, data, email, mobile) for person in people]
    record.match_candidates = snapshots
    record.match_evidence = {"candidate_count": len(snapshots)}
    record.outcome = None

    email_candidates = [snapshot for snapshot in snapshots if "email" in snapshot["matched_on"]]
    if len(people) > 1:
        _set_review(record, "MULTIPLE_STRONG_CANDIDATES")
    elif len(email_candidates) == 1:
        snapshot = email_candidates[0]
        if snapshot["contradiction_codes"]:
            _set_review(record, "UNIQUE_EMAIL_WITH_CONTRADICTION")
        else:
            record.resolved_person_id = snapshot["person_id"]
            record.resolution_method = ImportRecord.ResolutionMethod.AUTO_MATCH
            record.resolution_reason = "UNIQUE_EMAIL_MATCH"
            record.status = ImportRecord.Status.RESOLVED
    elif len(people) == 1:
        _set_review(record, "MOBILE_ONLY_MATCH")
    else:
        record.resolved_person = None
        record.resolution_method = ImportRecord.ResolutionMethod.NO_MATCH
        record.resolution_reason = "NO_STRONG_CANDIDATE"
        record.status = ImportRecord.Status.RESOLVED
    record.save(update_fields=["match_candidates", "match_evidence", "outcome", "resolved_person", "resolution_method", "resolution_reason", "status", "updated_at"])
    return record.status


def _set_review(record, reason):
    record.resolved_person = None
    record.resolution_method = ImportRecord.ResolutionMethod.NOT_RESOLVED
    record.resolution_reason = reason
    record.status = ImportRecord.Status.REVIEW_REQUIRED


def _candidate_snapshot(person, source, email, mobile):
    source_first = _casefold(source.get("first_name"))
    source_last = _casefold(source.get("last_name"))
    person_first = _casefold(person.first_name)
    person_last = _casefold(person.last_name)
    name_agreement = None if not (source_first and source_last) else source_first == person_first and source_last == person_last
    person_mobile = normalize_mobile(person.mobile)
    mobile_agreement = None if not (mobile and person_mobile) else mobile == person_mobile
    email_agreement = None if not (email and person.primary_email) else email.casefold() == person.primary_email.strip().casefold()
    contradictions = []
    if name_agreement is False:
        contradictions.append("NAME_CONFLICT")
    if mobile_agreement is False:
        contradictions.append("MOBILE_CONFLICT")
    matched_on = []
    if email_agreement:
        matched_on.append("email")
    if mobile_agreement:
        matched_on.append("mobile")
    return {
        "person_id": person.id,
        "matched_on": matched_on,
        "name_agreement": name_agreement,
        "mobile_agreement": mobile_agreement,
        "email_agreement": email_agreement,
        "person_record_state": "archived" if person.archived_at else "active",
        "contradiction_codes": contradictions,
    }


def _casefold(value):
    value = clean_text(value)
    return value.casefold() if value else None
