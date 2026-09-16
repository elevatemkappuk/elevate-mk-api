from django.db.models import Case, CharField, Count, Exists, OuterRef, Q, Value, When
from django.db.models.functions import Trim

from marketing_preferences.models import MarketingPreference
from people.models import Person
from people.querying import PeopleDirectoryQuery


ELIGIBLE = "ELIGIBLE"
EXCLUDED_OPTED_OUT = "EXCLUDED_OPTED_OUT"
EXCLUDED_CONSENT_UNKNOWN = "EXCLUDED_CONSENT_UNKNOWN"
EXCLUDED_NO_EMAIL = "EXCLUDED_NO_EMAIL"

FILTER_KEYS = (
    "q",
    "relationship",
    "location",
    "industry",
    "career_stage",
    "interest",
    "skill",
    "tag",
)


def build_active_people_selection(selection, ordering):
    """Apply the canonical People directory semantics to active BUSINESS People."""
    query_params = {
        **selection,
        "record_state": "active",
        "ordering": ordering,
    }
    return PeopleDirectoryQuery(Person.objects.active_business(), query_params).apply()


def annotate_email_marketing_classification(queryset):
    preference = MarketingPreference.objects.filter(
        person_id=OuterRef("pk"),
        channel=MarketingPreference.Channel.EMAIL,
    )
    return queryset.annotate(
        audience_email=Trim("primary_email"),
        audience_opted_in=Exists(preference.filter(state=MarketingPreference.State.OPTED_IN)),
        audience_opted_out=Exists(preference.filter(state=MarketingPreference.State.OPTED_OUT)),
    ).annotate(
        audience_classification=Case(
            When(
                Q(audience_email__isnull=True) | Q(audience_email=""),
                then=Value(EXCLUDED_NO_EMAIL),
            ),
            When(audience_opted_out=True, then=Value(EXCLUDED_OPTED_OUT)),
            When(audience_opted_in=True, then=Value(ELIGIBLE)),
            default=Value(EXCLUDED_CONSENT_UNKNOWN),
            output_field=CharField(),
        ),
    )


def get_audience_counts(classified_queryset):
    # PeopleDirectoryQuery applies deterministic ordering for result pages.
    # Clear it before values()/annotate() so database backends do not include
    # ordering columns in the GROUP BY and split one classification into one
    # group per Person.
    counts = {
        row["audience_classification"]: row["count"]
        for row in classified_queryset.order_by().values("audience_classification").annotate(count=Count("pk"))
    }
    eligible_count = counts.get(ELIGIBLE, 0)
    exclusion_counts = {
        EXCLUDED_OPTED_OUT: counts.get(EXCLUDED_OPTED_OUT, 0),
        EXCLUDED_CONSENT_UNKNOWN: counts.get(EXCLUDED_CONSENT_UNKNOWN, 0),
        EXCLUDED_NO_EMAIL: counts.get(EXCLUDED_NO_EMAIL, 0),
    }
    excluded_count = sum(exclusion_counts.values())
    selected_count = eligible_count + excluded_count
    return selected_count, eligible_count, excluded_count, exclusion_counts
