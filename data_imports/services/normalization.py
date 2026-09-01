import re
from datetime import date, datetime, time

from django.core.validators import URLValidator, validate_email
from django.core.exceptions import ValidationError

from people.models import Person


def json_safe(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def normalize_mobile(value):
    value = clean_text(value)
    if value is None:
        return None
    return re.sub(r"[\s()\-./]", "", value)


def normalize_membership_form_row(raw_data, headers):
    value = lambda header: clean_text(raw_data.get(header))
    source_gender = value(headers["Gender"])
    source_age_range = value(headers["Age "])
    normalized = {
        "source_timestamp": raw_data.get(headers["Timestamp"]),
        "first_name": value(headers["First Name "]),
        "last_name": value(headers["Last Name "]),
        "gender": Person.normalize_gender(source_gender),
        "age_range": Person.normalize_age_range(source_age_range),
        "email": value(headers["Email (preferably your personal email)"]),
        "mobile": normalize_mobile(raw_data.get(headers["Mobile Number"])),
        "location": value(headers["Location"]),
        "industry": value(headers["What industry do you work in?"]),
        "job_title": value(headers["What is your current role or job title?"]),
        "linkedin_url": value(headers["Linkedin URL"]),
    }
    if normalized["email"]:
        normalized["email"] = normalized["email"].lower()
    errors = []
    if source_age_range and normalized["age_range"] is None:
        errors.append({
            "field": "age_range",
            "code": "unsupported_age_range",
            "message": "Age range value is not supported.",
        })
    if source_gender and normalized["gender"] is None:
        errors.append({
            "field": "gender",
            "code": "unsupported_gender",
            "message": "Gender value is not supported.",
        })
    if normalized["email"]:
        try:
            validate_email(normalized["email"])
        except ValidationError:
            errors.append({"field": "email", "code": "invalid_email", "message": "Email address is not valid."})
    if normalized["linkedin_url"]:
        try:
            URLValidator(schemes=["http", "https"])(normalized["linkedin_url"])
        except ValidationError:
            errors.append({"field": "linkedin_url", "code": "invalid_url", "message": "LinkedIn URL is not valid."})
    return normalized, errors
