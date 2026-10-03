from django.db.models import Prefetch, Q
from django.db.models.functions import Lower
from rest_framework.pagination import PageNumberPagination

from interests.models import Interest, PersonInterest
from memberships.models import Membership
from people.models import Person
from skills.models import PersonSkill


class CommunityDirectoryPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def community_directory_queryset():
    """Return only profiles which are eligible for member-facing discovery."""
    return (
        Person.objects.filter(
            record_type=Person.RecordType.BUSINESS,
            archived_at__isnull=True,
            membership__status=Membership.Status.ACTIVE,
            user__is_active=True,
            community_profile__directory_visible=True,
        )
        .select_related(
            "user",
            "membership",
            "community_profile",
            "professional_profile",
            "professional_profile__industry",
        )
        .prefetch_related(
            Prefetch(
                "person_skills",
                queryset=PersonSkill.objects.filter(skill__is_active=True).select_related("skill"),
                to_attr="directory_skills",
            ),
            Prefetch(
                "person_interests",
                queryset=PersonInterest.objects.filter(interest__is_active=True).select_related("interest"),
                to_attr="directory_interests",
            ),
        )
        .order_by(Lower("first_name"), Lower("last_name"), "community_profile__directory_id")
    )


def directory_filter_queryset(queryset, *, industry=None, skill=None, interest=None):
    if industry:
        queryset = queryset.filter(professional_profile__industry__slug=industry)
    if skill:
        queryset = queryset.filter(person_skills__skill__slug=skill, person_skills__skill__is_active=True)
    if interest:
        queryset = queryset.filter(
            person_interests__interest__slug=interest,
            person_interests__interest__is_active=True,
        )
    return queryset.distinct()


def directory_search_queryset(queryset, query):
    if query:
        queryset = queryset.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))
    return queryset


def build_directory_professional(person):
    professional = getattr(person, "professional_profile", None)
    industry = getattr(professional, "industry", None) if professional else None
    return {
        "job_title": professional.job_title if professional else "",
        "company": professional.company if professional else "",
        "industry": {"slug": industry.slug, "label": industry.name} if industry else None,
        "career_stage": professional.career_stage if professional else None,
        "linkedin_url": professional.linkedin_url if professional else "",
    }


def build_directory_projection(person, *, include_detail=False):
    profile = person.community_profile
    photo_url = profile.photo.url if profile.photo else None
    projection = {
        "directory_id": profile.directory_id,
        "photo_url": photo_url,
        "first_name": person.first_name,
        "last_name": person.last_name,
        "location": person.location,
        "professional": build_directory_professional(person),
        "skills": [{"slug": item.skill.slug, "label": item.skill.name} for item in person.directory_skills],
        "interests": [{"slug": item.interest.slug, "label": item.interest.name} for item in person.directory_interests],
    }
    if include_detail:
        projection.update(
            {
                "bio": profile.bio,
                "contact": {
                    "email": person.primary_email if profile.email_visible else None,
                    "mobile": person.mobile if profile.mobile_visible else None,
                },
            }
        )
    return projection
