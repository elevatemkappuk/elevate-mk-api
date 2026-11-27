from accounts.models import User


def test_actor():
    """Return an authorized superuser for test-only lifecycle setup."""
    actor = User.objects.filter(email="test-lifecycle-actor@example.invalid").first()
    if actor is None:
        actor = User.objects.create_superuser(
            email="test-lifecycle-actor@example.invalid",
            password="testpass123",
            person_first_name="Test",
            person_last_name="Lifecycle Actor",
        )
    if not actor.is_active or not actor.is_superuser:
        actor.is_active = True
        actor.is_staff = True
        actor.is_superuser = True
        actor.save(update_fields=["is_active", "is_staff", "is_superuser"])
    return actor
