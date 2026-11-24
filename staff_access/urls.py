from django.urls import path

from staff_access.views import (
    StaffInvitationCancelView,
    StaffInvitationCreateView,
    StaffInvitationRedeemView,
    StaffInvitationResendView,
    StaffInvitationRoleUpdateView,
    StaffEmailChangeConfirmView,
    StaffEmailChangeRequestView,
    StaffOffboardView,
    StaffReactivateView,
    StaffEffectiveAccessView,
)


urlpatterns = [
    path("staff/access/me/", StaffEffectiveAccessView.as_view(), name="staff-effective-access-me"),
    path("staff/invitations/", StaffInvitationCreateView.as_view(), name="staff-invitation-create"),
    path("staff/invitations/<uuid:invitation_id>/cancel/", StaffInvitationCancelView.as_view(), name="staff-invitation-cancel"),
    path("staff/invitations/<uuid:invitation_id>/resend/", StaffInvitationResendView.as_view(), name="staff-invitation-resend"),
    path("staff/invitations/<uuid:invitation_id>/roles/", StaffInvitationRoleUpdateView.as_view(), name="staff-invitation-role-update"),
    path("staff/invitations/<uuid:invitation_id>/<str:token>/redeem/", StaffInvitationRedeemView.as_view(), name="staff-invitation-redeem"),
    path("staff/email-change/", StaffEmailChangeRequestView.as_view(), name="staff-email-change-request"),
    path("staff/email-change/<int:request_id>/confirm/", StaffEmailChangeConfirmView.as_view(), name="staff-email-change-confirm"),
    path("staff/offboard/", StaffOffboardView.as_view(), name="staff-offboard"),
    path("staff/reactivate/", StaffReactivateView.as_view(), name="staff-reactivate"),
]
