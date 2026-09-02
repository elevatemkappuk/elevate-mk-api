from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from data_imports.adapters.eventbrite import EventbriteStructureError, iter_eventbrite_rows
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.fingerprints import fingerprint_bytes, fingerprint_source_row
from data_imports.services.normalization import clean_text, normalize_mobile


def ingest_eventbrite_workbook(*, workbook_bytes, source_filename, created_by=None):
    """Stage Eventbrite rows only; this does not analyze identity or mutate CRM/Event data."""
    batch = ImportBatch.objects.create(
        source_type=ImportBatch.SourceType.EVENTBRITE,
        source_filename=source_filename,
        source_fingerprint=fingerprint_bytes(workbook_bytes),
        status=ImportBatch.Status.PROCESSING,
        created_by=created_by,
    )
    try:
        with transaction.atomic():
            for row_number, raw_data, header_map in iter_eventbrite_rows(workbook_bytes):
                normalized_data, validation_errors = normalize_eventbrite_row(raw_data, header_map)
                ImportRecord.objects.create(
                    batch=batch,
                    source_row_identifier=f"sheet:{row_number}",
                    source_fingerprint=fingerprint_source_row(raw_data),
                    raw_data=raw_data,
                    normalized_data=normalized_data,
                    validation_errors=validation_errors,
                    status=ImportRecord.Status.INVALID if validation_errors else ImportRecord.Status.STAGED,
                )
    except Exception:
        batch.status = ImportBatch.Status.FAILED
        batch.save(update_fields=["status", "updated_at"])
        raise
    batch.status = ImportBatch.Status.STAGED
    batch.save(update_fields=["status", "updated_at"])
    return batch


def normalize_eventbrite_row(raw_data, headers):
    value = lambda field: clean_text(raw_data.get(headers[field]))
    email = value("buyer_email")
    normalized = {
        "person": {
            "first_name": value("buyer_first_name"),
            "last_name": value("buyer_last_name"),
            "email": email.lower() if email else None,
            "mobile": normalize_mobile(raw_data.get(headers["mobile"])),
            "city": value("city"),
            "county": value("county"),
            "country": value("country"),
        },
        "event": {
            "external_event_id": value("external_event_id"),
            "name": value("event_name"),
            "start_at": None,
            "timezone": value("event_timezone"),
            "location_name": value("event_location"),
        },
        "source": {
            "provider": "EVENTBRITE",
            "external_order_id": value("external_order_id"),
            "order_date": _normalize_datetime_or_date(raw_data.get(headers["order_date"])),
            "ticket_quantity": None,
            "guest": None,
        },
    }
    errors = []
    if normalized["person"]["email"]:
        try:
            validate_email(normalized["person"]["email"])
        except ValidationError:
            errors.append(_error("person.email", "invalid_email", "Email address is not valid."))
    for field, path in (("external_event_id", "event.external_event_id"), ("event_name", "event.name")):
        if not normalized["event"][field]:
            errors.append(_error(path, "required", "This value is required."))
    start_at, start_errors = _normalize_event_start(
        raw_data.get(headers["event_start_date"]),
        raw_data.get(headers["event_start_time"]),
        normalized["event"]["timezone"],
    )
    normalized["event"]["start_at"] = start_at
    errors.extend(start_errors)
    quantity, quantity_error = _normalize_ticket_quantity(raw_data.get(headers["ticket_quantity"]))
    normalized["source"]["ticket_quantity"] = quantity
    if quantity_error:
        errors.append(quantity_error)
    guest, guest_error = _normalize_guest(raw_data.get(headers["guest"]))
    normalized["source"]["guest"] = guest
    if guest_error:
        errors.append(guest_error)
    return normalized, errors


def _normalize_event_start(source_date, source_time, timezone_name):
    errors = []
    parsed_date = _parse_date(source_date)
    parsed_time = _parse_time(source_time)
    if parsed_date is None:
        errors.append(_error("event.start_date", "invalid_event_date", "Event start date is not valid."))
    if parsed_time is None:
        errors.append(_error("event.start_time", "invalid_event_time", "Event start time is not valid."))
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name else None
    except ZoneInfoNotFoundError:
        timezone = None
    if timezone is None:
        errors.append(_error("event.timezone", "invalid_timezone", "Event timezone is not valid."))
    if errors:
        return None, errors
    return datetime.combine(parsed_date, parsed_time, tzinfo=timezone).isoformat(), errors


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_time(value):
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = clean_text(value)
    if not text:
        return None
    try:
        return time.fromisoformat(text)
    except ValueError:
        return None


def _normalize_datetime_or_date(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return clean_text(value)


def _normalize_ticket_quantity(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None, _error("source.ticket_quantity", "invalid_ticket_quantity", "Ticket quantity is not valid.")
    if quantity < 0 or str(value).strip() not in (str(quantity), f"{quantity}.0"):
        return None, _error("source.ticket_quantity", "invalid_ticket_quantity", "Ticket quantity is not valid.")
    return quantity, None


def _normalize_guest(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return value, None
    normalized = str(value).strip().casefold()
    if normalized in ("yes", "true", "1"):
        return True, None
    if normalized in ("no", "false", "0"):
        return False, None
    return None, _error("source.guest", "invalid_guest", "Guest value is not valid.")


def _error(field, code, message):
    return {"field": field, "code": code, "message": message}
