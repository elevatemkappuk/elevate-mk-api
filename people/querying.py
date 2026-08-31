from django.db.models import Exists, F, OuterRef, Q, Value
from django.db.models.functions import Concat, Trim

from interests.models import PersonInterest
from people.models import Person
from skills.models import PersonSkill
from tags.models import PersonTag


class PeopleDirectoryQuery:
    """Compose the explicit V1 CRM directory filters at the database boundary."""

    ORDERING_MAP = {
        "name": ("first_name", "last_name", "id"),
        "-name": ("-first_name", "-last_name", "-id"),
        # Retain pre-directory sort names for current frontend URL compatibility.
        "first_name": ("first_name", "last_name", "id"),
        "-first_name": ("-first_name", "last_name", "id"),
        "last_name": ("last_name", "first_name", "id"),
        "-last_name": ("-last_name", "first_name", "id"),
        "created_at": ("created_at", "id"),
        "-created_at": ("-created_at", "-id"),
        "updated_at": ("updated_at", "id"),
        "-updated_at": ("-updated_at", "-id"),
    }

    def __init__(self, queryset, params):
        self.queryset = queryset
        self.params = params

    def apply(self):
        queryset = self.apply_record_state(self.queryset)
        queryset = self.apply_search(queryset)
        queryset = self.apply_relationship(queryset)
        queryset = self.apply_locations(queryset)
        queryset = self.apply_professional_filters(queryset)
        queryset = self.apply_classification_filters(queryset)
        return self.apply_ordering(queryset)

    def apply_record_state(self, queryset):
        state = self.params["record_state"]
        if state == "archived":
            return queryset.archived_business()
        if state == "all":
            return queryset.business()
        return queryset.active_business()

    def apply_search(self, queryset):
        query = self.params.get("q", "").strip()
        if not query:
            return queryset
        return queryset.annotate(full_name=Concat("first_name", Value(" "), "last_name")).filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(primary_email__icontains=query)
            | Q(mobile__icontains=query)
            | Q(full_name__icontains=query)
            | Q(professional_profile__job_title__icontains=query)
            | Q(professional_profile__company__icontains=query)
        )

    def apply_relationship(self, queryset):
        relationships = self.params.get("relationship", [])
        if not relationships:
            return queryset
        condition = Q()
        if "CONTACT" in relationships:
            condition |= Q(membership__isnull=True)
        if "ACTIVE_MEMBER" in relationships:
            condition |= Q(membership__status="ACTIVE")
        if "FORMER_MEMBER" in relationships:
            condition |= Q(membership__status="FORMER")
        return queryset.filter(condition)

    def apply_locations(self, queryset):
        locations = self.params.get("location", [])
        if not locations:
            return queryset
        condition = Q()
        for location in locations:
            condition |= Q(directory_location__iexact=location.strip())
        return queryset.annotate(directory_location=Trim("location")).filter(condition)

    def apply_professional_filters(self, queryset):
        industries = self.params.get("industry", [])
        career_stages = self.params.get("career_stage", [])
        if industries:
            queryset = queryset.filter(professional_profile__industry_id__in=industries)
        if career_stages:
            queryset = queryset.filter(professional_profile__career_stage__in=career_stages)
        return queryset

    def apply_classification_filters(self, queryset):
        interests = self.params.get("interest", [])
        if interests:
            queryset = queryset.filter(Exists(PersonInterest.objects.filter(
                person_id=OuterRef("pk"), interest_id__in=interests, interest__is_active=True,
            )))
        skills = self.params.get("skill", [])
        if skills:
            queryset = queryset.filter(Exists(PersonSkill.objects.filter(
                person_id=OuterRef("pk"), skill_id__in=skills, skill__is_active=True,
            )))
        tags = self.params.get("tag", [])
        if tags:
            queryset = queryset.filter(Exists(PersonTag.objects.filter(
                person_id=OuterRef("pk"), tag_id__in=tags, is_active=True, tag__is_active=True,
            )))
        return queryset

    def apply_ordering(self, queryset):
        ordering = self.params["ordering"]
        if ordering == "membership_joined_at":
            return queryset.order_by(F("membership__joined_at").asc(nulls_last=True), "id")
        if ordering == "-membership_joined_at":
            return queryset.order_by(F("membership__joined_at").desc(nulls_last=True), "-id")
        return queryset.order_by(*self.ORDERING_MAP[ordering])
