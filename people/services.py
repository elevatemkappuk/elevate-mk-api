from django.db.models import Q

from people.models import Person


def normalize_email(value):
    return value.strip().lower() if value else ""


def normalize_mobile(value):
    """Conservatively remove common visual separators without assuming a phone plan."""
    if not value:
        return ""
    return "".join(character for character in value.strip() if character not in " -()")


def find_business_duplicate_people(*, primary_email="", mobile="", exclude_person_id=None):
    """Return BUSINESS candidates matching the CRM's exact normalized identity signals."""
    normalized_email = normalize_email(primary_email)
    normalized_mobile = normalize_mobile(mobile)
    if not normalized_email and not normalized_mobile:
        return []

    queryset = Person.objects.business()
    if exclude_person_id is not None:
        queryset = queryset.exclude(pk=exclude_person_id)

    email_query = Q()
    if normalized_email:
        email_query = Q(primary_email__iexact=normalized_email)

    # Mobile formatting is not canonical in V1. Compare conservative normalized
    # values in application code so historical formatting remains supported.
    candidates = list(queryset.filter(email_query) if normalized_email else [])
    if normalized_mobile:
        mobile_candidates = queryset.exclude(mobile="").exclude(mobile__isnull=True)
        candidates.extend(
            person for person in mobile_candidates if normalize_mobile(person.mobile) == normalized_mobile
        )

    return list({person.id: person for person in candidates}.values())
