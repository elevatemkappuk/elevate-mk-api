from django.contrib import admin

from community.models import CommunityAccountInvitation, CommunityProfile


@admin.register(CommunityProfile)
class CommunityProfileAdmin(admin.ModelAdmin):
    list_display = ("person", "person_preexisted_community", "review_acknowledged_at", "created_at", "updated_at")
    search_fields = ("person__first_name", "person__last_name", "person__primary_email")
    readonly_fields = ("person_preexisted_community", "created_at", "updated_at")


@admin.register(CommunityAccountInvitation)
class CommunityAccountInvitationAdmin(admin.ModelAdmin):
    list_display = ("person", "intended_email", "created_at", "expires_at", "used_at", "revoked_at", "superseded_at")
    list_filter = ("used_at", "revoked_at", "superseded_at")
    search_fields = ("intended_email", "person__first_name", "person__last_name")
    readonly_fields = ("public_id", "token_hash", "created_at", "updated_at")
