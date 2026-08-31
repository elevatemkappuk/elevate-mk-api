from django.contrib import admin

from data_imports.models import ImportBatch, ImportRecord


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "source_type", "source_filename", "status", "started_at", "completed_at", "created_by", "created_at")
    list_filter = ("source_type", "status", "created_at")
    search_fields = ("source_filename", "source_fingerprint", "created_by__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ImportRecord)
class ImportRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "batch", "source_row_identifier", "status", "outcome", "resolved_person", "resolution_method", "reviewed_by", "committed_at")
    list_filter = ("status", "outcome", "resolution_method", "batch__source_type")
    search_fields = ("source_row_identifier", "source_fingerprint", "resolved_person__primary_email", "reviewed_by__email")
    autocomplete_fields = ("batch", "resolved_person", "reviewed_by")
    readonly_fields = ("created_at", "updated_at")
