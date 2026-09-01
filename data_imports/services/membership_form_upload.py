from data_imports.services.identity_analysis import analyze_import_batch
from data_imports.services.membership_ingestion import ingest_membership_form


class MembershipFormAnalysisError(Exception):
    """Raised when analysis fails after a workbook was safely staged."""


def ingest_and_analyze_membership_form(*, uploaded_file, created_by):
    """Stage and analyze one Membership Form workbook without authoritative CRM mutation."""
    workbook_bytes = uploaded_file.read()
    batch = ingest_membership_form(
        workbook_bytes=workbook_bytes,
        source_filename=uploaded_file.name,
        created_by=created_by,
    )
    try:
        return analyze_import_batch(batch)
    except Exception as error:
        batch.refresh_from_db()
        batch.status = batch.Status.FAILED
        batch.save(update_fields=["status", "updated_at"])
        raise MembershipFormAnalysisError from error
