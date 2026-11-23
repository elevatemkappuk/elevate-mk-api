from memberships.models import Membership


def user_is_active_mobile_member(user):
    """Return whether a user is eligible for mobile member authentication."""
    if not user or not user.is_active or not user.person_id:
        return False

    if user.person.archived_at is not None:
        return False

    return Membership.objects.filter(
        person_id=user.person_id,
        status=Membership.Status.ACTIVE,
    ).exists()
