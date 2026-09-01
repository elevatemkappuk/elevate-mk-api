from django.urls import path

from data_imports.views import (
    ImportBatchDetailView,
    ImportBatchListView,
    ImportRecordListView,
    MembershipFormUploadView,
    ImportReviewDetailView,
    ImportReviewQueueView,
    ImportReviewResolveView,
)


urlpatterns = [
    path("imports/membership-form/", MembershipFormUploadView.as_view(), name="import-membership-form-upload"),
    path("imports/", ImportBatchListView.as_view(), name="import-batch-list"),
    path("imports/<int:batch_id>/", ImportBatchDetailView.as_view(), name="import-batch-detail"),
    path("imports/<int:batch_id>/records/", ImportRecordListView.as_view(), name="import-record-list"),
    path("imports/<int:batch_id>/review/", ImportReviewQueueView.as_view(), name="import-review-queue"),
    path("imports/<int:batch_id>/review/<int:record_id>/", ImportReviewDetailView.as_view(), name="import-review-detail"),
    path("imports/<int:batch_id>/review/<int:record_id>/resolve/", ImportReviewResolveView.as_view(), name="import-review-resolve"),
]
