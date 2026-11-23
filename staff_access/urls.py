from django.urls import path

from staff_access.views import (
    StaffInvitationCancelView,
    StaffInvitationCreateView,
    StaffInvitationRedeemView,
    StaffEmailChangeConfirmView,
    StaffEmailChangeRequestView,
)


urlpatterns = [
    path("staff/invitations/", StaffInvitationCreateView.as_view(), name="staff-invitation-create"),
    path("staff/invitations/<uuid:invitation_id>/cancel/", StaffInvitationCancelView.as_view(), name="staff-invitation-cancel"),
    path("staff/invitations/<uuid:invitation_id>/<str:token>/redeem/", StaffInvitationRedeemView.as_view(), name="staff-invitation-redeem"),
    path("staff/email-change/", StaffEmailChangeRequestView.as_view(), name="staff-email-change-request"),
    path("staff/email-change/<int:request_id>/confirm/", StaffEmailChangeConfirmView.as_view(), name="staff-email-change-confirm"),
]
