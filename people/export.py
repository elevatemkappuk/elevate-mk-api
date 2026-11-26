from io import BytesIO, StringIO
import csv
from datetime import date, datetime
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from audit.models import AuditEvent
from audit.services import record_audit_event
from people.privacy import project_person_privacy
from staff_access.models import CapabilityDefinition


BASE_COLUMNS = (
    ("id", "Person ID"),
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("record_type", "Record type"),
    ("created_at", "Created at"),
    ("updated_at", "Updated at"),
)


def export_columns(capabilities):
    columns = list(BASE_COLUMNS)
    if CapabilityDefinition.PERSON_CONTACT_READ in capabilities:
        columns.extend((("primary_email", "Email"), ("mobile", "Mobile")))
    if CapabilityDefinition.PERSON_DEMOGRAPHIC_READ in capabilities:
        columns.extend((("age_range", "Age range"), ("gender", "Gender")))
    return [key for key, _label in columns]


def _column_definitions(capabilities):
    columns = list(BASE_COLUMNS)
    if CapabilityDefinition.PERSON_CONTACT_READ in capabilities:
        columns.extend((("primary_email", "Email"), ("mobile", "Mobile")))
    if CapabilityDefinition.PERSON_DEMOGRAPHIC_READ in capabilities:
        columns.extend((("age_range", "Age range"), ("gender", "Gender")))
    return columns


def _safe_cell(value):
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    if value is None:
        return ""
    value = str(value)
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _rows(people, capabilities):
    columns = _column_definitions(capabilities)
    result = [[label for _key, label in columns]]
    for person in people:
        projected = project_person_privacy(person=person, capabilities=capabilities)
        values = {
            "id": person.pk,
            "first_name": person.first_name,
            "last_name": person.last_name,
            "record_type": person.record_type,
            "created_at": person.created_at,
            "updated_at": person.updated_at,
            "primary_email": projected["primary_email"],
            "mobile": projected["mobile"],
            "age_range": projected["age_range"],
            "gender": projected["gender"],
        }
        result.append([_safe_cell(values[key]) for key, _label in columns])
    return result


def _csv_bytes(rows):
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _xlsx_bytes(rows):
    def cell(value, column, row_number):
        reference = f"{column}{row_number}"
        return f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(cell(value, _excel_column(index), row_number) for index, value in enumerate(row, start=1))
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="People" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _excel_column(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def build_people_export(people, *, capabilities, export_format):
    rows = _rows(people, capabilities)
    if export_format == "xlsx":
        return {
            "content": _xlsx_bytes(rows),
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": "people-export.xlsx",
        }
    return {
        "content": _csv_bytes(rows),
        "content_type": "text/csv; charset=utf-8",
        "filename": "people-export.csv",
    }


def record_export_audit(*, request, export_format, fields, record_count, outcome, reason_code):
    metadata = {
        "event_type": "PERSON_EXPORT",
        "format": export_format,
        "scope": "PEOPLE_DIRECTORY",
        "field_categories": sorted({
            "CONTACT" if field in {"primary_email", "mobile"} else
            "DEMOGRAPHIC" if field in {"age_range", "gender"} else "IDENTITY"
            for field in fields
        }),
        "field_count": len(fields),
        "record_count": record_count,
        "outcome": outcome,
        "superuser_authority": bool(getattr(request.user, "is_superuser", False)),
    }
    if reason_code:
        metadata["reason_code"] = reason_code
    return record_audit_event(
        action=(AuditEvent.Action.PERSON_EXPORT_GENERATED if outcome == "SUCCESS" else AuditEvent.Action.PERSON_EXPORT_DENIED),
        entity_type="PersonExport",
        entity_id=__import__("uuid").uuid4().hex,
        actor_user=request.user if getattr(request.user, "is_authenticated", False) else None,
        metadata=metadata,
        audit_domain=AuditEvent.Domain.PRIVACY,
        retention_class="PRIVACY_AUDIT",
    )
