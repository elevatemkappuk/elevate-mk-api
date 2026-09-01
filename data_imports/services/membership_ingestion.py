from django.db import transaction

from data_imports.adapters.membership_form import MembershipFormStructureError, REQUIRED_HEADERS, SHEET_NAME, iter_membership_form_rows
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.fingerprints import fingerprint_bytes, fingerprint_source_row
from data_imports.services.normalization import normalize_membership_form_row


def ingest_membership_form(*, workbook_bytes, source_filename, created_by=None):
    """Stage a Membership Form workbook. This never mutates authoritative CRM data."""
    batch = ImportBatch.objects.create(
        source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
        source_filename=source_filename,
        source_fingerprint=fingerprint_bytes(workbook_bytes),
        status=ImportBatch.Status.PROCESSING,
        created_by=created_by,
    )
    header_map = {header: header for header in REQUIRED_HEADERS}
    try:
        with transaction.atomic():
            for row_number, raw_data in iter_membership_form_rows(workbook_bytes):
                normalized_data, validation_errors = normalize_membership_form_row(raw_data, header_map)
                ImportRecord.objects.create(
                    batch=batch,
                    source_row_identifier=f"sheet:{SHEET_NAME}:row:{row_number}",
                    source_fingerprint=fingerprint_source_row(raw_data),
                    raw_data=raw_data,
                    normalized_data=normalized_data,
                    validation_errors=validation_errors,
                    status=ImportRecord.Status.INVALID if validation_errors else ImportRecord.Status.STAGED,
                )
            # Orchestration retains PROCESSING until identity analysis completes.
            batch.save(update_fields=["updated_at"])
    except Exception:
        batch.status = ImportBatch.Status.FAILED
        batch.save(update_fields=["status", "updated_at"])
        raise
    return batch
