"""Read-only dashboard projections. Membership growth measures historical joining."""
from datetime import date, datetime, time

from django.db.models import Count, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from data_imports.models import ImportBatch
from memberships.models import Membership
from people.models import Person


def month_start(index):
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def dashboard_projection():
    today = timezone.localdate()
    current = today.year * 12 + today.month - 1
    months = [month_start(index) for index in range(current - 5, current + 1)]
    end = month_start(current + 1)
    tz = timezone.get_current_timezone()
    start_datetime = timezone.make_aware(datetime.combine(months[0], time.min), tz)
    end_datetime = timezone.make_aware(datetime.combine(end, time.min), tz)
    people = Person.objects.active_business().order_by()
    overview = people.aggregate(
        total_people=Count("id"),
        active_members=Count("id", filter=Q(membership__status=Membership.Status.ACTIVE)),
        contacts=Count("id", filter=Q(membership__isnull=True)),
        former_members=Count("id", filter=Q(membership__status=Membership.Status.FORMER)),
    )
    people_months = people.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).annotate(
        month=TruncMonth("created_at", tzinfo=tz)
    ).values("month").annotate(count=Count("id"))
    member_months = Membership.objects.filter(
        person__record_type=Person.RecordType.BUSINESS,
        joined_at__gte=months[0], joined_at__lt=end,
    ).order_by().annotate(month=TruncMonth("joined_at")).values("month").annotate(count=Count("id"))

    def series(rows):
        counts = {row["month"].strftime("%Y-%m"): row["count"] for row in rows}
        return [{"month": month.strftime("%Y-%m"), "count": counts.get(month.strftime("%Y-%m"), 0)} for month in months]

    locations = list(people.exclude(location__isnull=True).exclude(location__regex=r"^\s*$")
                     .values(label=F("location")).annotate(count=Count("id")).order_by("-count", "label")[:5])
    industries = people.filter(professional_profile__industry__isnull=False).values(
        "professional_profile__industry_id", label=F("professional_profile__industry__name")
    ).annotate(count=Count("id")).order_by("-count", "label", "professional_profile__industry_id")[:5]
    age_counts = dict(people.values("age_range").annotate(count=Count("id")).values_list("age_range", "count"))
    return {
        "overview": overview,
        "growth": {"people_by_month": series(people_months), "members_by_month": series(member_months)},
        "community_profile": {
            "top_locations": locations,
            "top_industries": [{"id": row["professional_profile__industry_id"], "label": row["label"], "count": row["count"]} for row in industries],
            "age_ranges": [{"value": value, "label": label, "count": age_counts.get(value, 0)} for value, label in Person.AgeRange.choices],
        },
        "attention": {
            "imports_needing_review": ImportBatch.objects.filter(status=ImportBatch.Status.READY_FOR_REVIEW).count(),
            "archived_people": Person.objects.archived_business().count(),
        },
    }
