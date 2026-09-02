from django.urls import path

from data_imports.views import (
    ImportBatchDetailView,
    ImportBatchAnalyzeView,
    ImportBatchImportView,
    ImportBatchListView,
    ImportRecordListView,
    EventbriteUploadView,
    MembershipFormUploadView,
    ImportReviewDetailView,
    ImportReviewQueueView,
    ImportReviewResolveView,
)


urlpatterns = [
    path("imports/eventbrite/", EventbriteUploadView.as_view(), name="import-eventbrite-upload"),
    path("imports/membership-form/", MembershipFormUploadView.as_view(), name="import-membership-form-upload"),
    path("imports/", ImportBatchListView.as_view(), name="import-batch-list"),
    path("imports/<int:batch_id>/", ImportBatchDetailView.as_view(), name="import-batch-detail"),
    path("imports/<int:batch_id>/analyze/", ImportBatchAnalyzeView.as_view(), name="import-batch-analyze"),
    path("imports/<int:batch_id>/import/", ImportBatchImportView.as_view(), name="import-batch-import"),
    path("imports/<int:batch_id>/records/", ImportRecordListView.as_view(), name="import-record-list"),
    path("imports/<int:batch_id>/review/", ImportReviewQueueView.as_view(), name="import-review-queue"),
    path("imports/<int:batch_id>/review/<int:record_id>/", ImportReviewDetailView.as_view(), name="import-review-detail"),
    path("imports/<int:batch_id>/review/<int:record_id>/resolve/", ImportReviewResolveView.as_view(), name="import-review-resolve"),
]
