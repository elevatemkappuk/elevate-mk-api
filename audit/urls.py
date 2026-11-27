from django.urls import path

from audit.views import (
    LegalHoldDetailView,
    LegalHoldDirectReleaseView,
    LegalHoldListCreateView,
    LegalHoldReleaseApprovalView,
    LegalHoldReleaseCancellationView,
    LegalHoldReleaseRejectionView,
    LegalHoldReleaseRequestView,
    PrivacyAuditHistoryView,
    LegalHoldPersonTargetView,
)

urlpatterns = [
    path("people/<int:person_id>/privacy-audit-history/", PrivacyAuditHistoryView.as_view(), name="people-privacy-audit-history"),
    path("privacy/legal-holds/", LegalHoldListCreateView.as_view(), name="privacy-legal-holds"),
    path("privacy/legal-holds/person-targets/", LegalHoldPersonTargetView.as_view(), name="privacy-legal-hold-person-targets"),
    path("privacy/legal-holds/<uuid:public_id>/", LegalHoldDetailView.as_view(), name="privacy-legal-hold-detail"),
    path("privacy/legal-holds/<uuid:public_id>/release-requests/", LegalHoldReleaseRequestView.as_view(), name="privacy-legal-hold-release-request"),
    path("privacy/legal-hold-release-requests/<uuid:public_id>/approve/", LegalHoldReleaseApprovalView.as_view(), name="privacy-legal-hold-release-approve"),
    path("privacy/legal-hold-release-requests/<uuid:public_id>/cancel/", LegalHoldReleaseCancellationView.as_view(), name="privacy-legal-hold-release-cancel"),
    path("privacy/legal-hold-release-requests/<uuid:public_id>/reject/", LegalHoldReleaseRejectionView.as_view(), name="privacy-legal-hold-release-reject"),
    path("privacy/legal-holds/<uuid:public_id>/direct-release/", LegalHoldDirectReleaseView.as_view(), name="privacy-legal-hold-direct-release"),
]
