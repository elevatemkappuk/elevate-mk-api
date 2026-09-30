from django.contrib import admin

from community.models import CommunityAccountInvitation


@admin.register(CommunityAccountInvitation)
class CommunityAccountInvitationAdmin(admin.ModelAdmin):
    list_display = ("person", "intended_email", "created_at", "expires_at", "used_at", "revoked_at", "superseded_at")
    list_filter = ("used_at", "revoked_at", "superseded_at")
    search_fields = ("intended_email", "person__first_name", "person__last_name")
    readonly_fields = ("public_id", "token_hash", "created_at", "updated_at")
