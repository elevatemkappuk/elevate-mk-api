def person_identity_source(record_or_data):
    """Return the source-neutral Person identity projection used by import analysis."""
    source = getattr(record_or_data, "normalized_data", record_or_data) or {}
    person = source.get("person")
    return person if isinstance(person, dict) else source


def person_source_display(record_or_data):
    """Adapt provider-neutral nested source data to the existing safe review display."""
    source = person_identity_source(record_or_data)
    return {
        "first_name": source.get("first_name"),
        "last_name": source.get("last_name"),
        "email": source.get("email"),
        "mobile": source.get("mobile"),
        "location": source.get("location") or source.get("city"),
        "industry": source.get("industry"),
        "job_title": source.get("job_title"),
        "linkedin_url": source.get("linkedin_url"),
    }
