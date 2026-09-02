from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from data_imports.models import ImportBatch, ImportRecord
from people.models import Person


IMPORT_BATCH_COUNT_ANNOTATIONS = {
    "total_count": Count("records", distinct=True),
    "review_required_count": Count(
        "records",
        filter=Q(records__status=ImportRecord.Status.REVIEW_REQUIRED),
        distinct=True,
    ),
    "resolved_count": Count(
        "records",
        filter=Q(records__status=ImportRecord.Status.RESOLVED),
        distinct=True,
    ),
    "invalid_count": Count(
        "records",
        filter=Q(records__status=ImportRecord.Status.INVALID),
        distinct=True,
    ),
    "committed_count": Count(
        "records",
        filter=Q(records__status=ImportRecord.Status.COMMITTED),
        distinct=True,
    ),
    "auto_match_count": Count(
        "records",
        filter=Q(
            records__resolution_method__in=(
                ImportRecord.ResolutionMethod.AUTO_MATCH,
                ImportRecord.ResolutionMethod.STAFF_MATCH,
            )
        ),
        distinct=True,
    ),
    "new_person_count": Count(
        "records",
        filter=Q(
            records__resolution_method__in=(
                ImportRecord.ResolutionMethod.NO_MATCH,
                ImportRecord.ResolutionMethod.STAFF_CREATE_NEW,
            )
        ),
        distinct=True,
    ),
}


class ImportBatchSerializer(serializers.ModelSerializer):
    total_count = serializers.IntegerField(read_only=True)
    review_required_count = serializers.IntegerField(read_only=True)
    resolved_count = serializers.IntegerField(read_only=True)
    invalid_count = serializers.IntegerField(read_only=True)
    committed_count = serializers.IntegerField(read_only=True)
    auto_match_count = serializers.IntegerField(read_only=True)
    new_person_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ImportBatch
        fields = (
            "id",
            "source_type",
            "source_filename",
            "status",
            "created_at",
            "started_at",
            "completed_at",
            "total_count",
            "review_required_count",
            "resolved_count",
            "invalid_count",
            "committed_count",
            "auto_match_count",
            "new_person_count",
        )


class ImportResultSummarySerializer(serializers.Serializer):
    processed_count = serializers.IntegerField(source="valid_processed", read_only=True)
    people_created_count = serializers.IntegerField(source="people_created", read_only=True)
    people_matched_count = serializers.IntegerField(source="people_matched", read_only=True)
    people_enriched_count = serializers.IntegerField(source="people_enriched", read_only=True)
    memberships_created_count = serializers.IntegerField(source="memberships_created", read_only=True)
    memberships_reused_count = serializers.IntegerField(source="memberships_reused", read_only=True)
    profiles_created_count = serializers.IntegerField(source="profiles_created", read_only=True)
    profiles_enriched_count = serializers.IntegerField(source="profiles_enriched", read_only=True)
    skipped_count = serializers.IntegerField(source="invalid_skipped", read_only=True)


class ImportBatchImportResponseSerializer(serializers.Serializer):
    batch = ImportBatchSerializer(read_only=True)
    result = ImportResultSummarySerializer(read_only=True)


class MembershipFormUploadSerializer(serializers.Serializer):
    max_upload_bytes = 10 * 1024 * 1024

    file = serializers.FileField(write_only=True)

    def validate_file(self, uploaded_file):
        filename = _safe_upload_filename(uploaded_file.name)
        if not filename:
            raise serializers.ValidationError("A filename is required.")
        if not filename.lower().endswith(".xlsx"):
            raise serializers.ValidationError("Only .xlsx Membership Form workbooks are supported.")
        if uploaded_file.size <= 0:
            raise serializers.ValidationError("The uploaded workbook is empty.")
        if uploaded_file.size > self.max_upload_bytes:
            raise serializers.ValidationError("The uploaded workbook exceeds the 10 MiB limit.")
        uploaded_file.name = filename
        return uploaded_file


class ImportCandidateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    primary_email = serializers.CharField(allow_blank=True, allow_null=True)
    mobile = serializers.CharField(allow_blank=True)
    record_state = serializers.ChoiceField(choices=("active", "archived"))
    matched_on = serializers.ListField(child=serializers.CharField())
    email_agreement = serializers.BooleanField(allow_null=True)
    mobile_agreement = serializers.BooleanField(allow_null=True)
    name_agreement = serializers.BooleanField(allow_null=True)
    contradiction_codes = serializers.ListField(child=serializers.CharField())


class ImportSourceSerializer(serializers.Serializer):
    first_name = serializers.CharField(allow_null=True)
    last_name = serializers.CharField(allow_null=True)
    email = serializers.CharField(allow_null=True)
    mobile = serializers.CharField(allow_null=True)
    location = serializers.CharField(allow_null=True)
    industry = serializers.CharField(allow_null=True)
    job_title = serializers.CharField(allow_null=True)
    linkedin_url = serializers.CharField(allow_null=True)


class ImportReviewBatchContextSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    source_type = serializers.CharField()
    source_filename = serializers.CharField()
    status = serializers.CharField()


class ImportValidationErrorSerializer(serializers.Serializer):
    field = serializers.CharField()
    code = serializers.CharField()
    message = serializers.CharField()


SAFE_VALIDATION_MESSAGES = {
    ("age_range", "unsupported_age_range"): "Age range is not supported.",
    ("gender", "unsupported_gender"): "Gender is not supported.",
    ("email", "invalid_email"): "Email address is not valid.",
    ("linkedin_url", "invalid_url"): "LinkedIn URL is not valid.",
}


class ImportReviewRecordSerializer(serializers.ModelSerializer):
    source = serializers.SerializerMethodField()
    candidates = serializers.SerializerMethodField()
    validation_errors = serializers.SerializerMethodField()

    class Meta:
        model = ImportRecord
        fields = (
            "id",
            "batch_id",
            "source_row_identifier",
            "status",
            "resolution_reason",
            "source",
            "candidates",
            "validation_errors",
        )

    @extend_schema_field(ImportSourceSerializer)
    def get_source(self, record) -> dict:
        source = record.normalized_data or {}
        return {
            field: source.get(field)
            for field in (
                "first_name",
                "last_name",
                "email",
                "mobile",
                "location",
                "industry",
                "job_title",
                "linkedin_url",
            )
        }

    @extend_schema_field(ImportCandidateSerializer(many=True))
    def get_candidates(self, record) -> list[dict]:
        candidate_snapshots = [candidate for candidate in record.match_candidates if isinstance(candidate, dict)]
        candidate_ids = [candidate.get("person_id") for candidate in candidate_snapshots if isinstance(candidate.get("person_id"), int)]
        people_by_id = self.context.get("candidate_people")
        if people_by_id is None:
            people_by_id = Person.objects.business().in_bulk(candidate_ids)
        candidates = []
        for snapshot in candidate_snapshots:
            person = people_by_id.get(snapshot.get("person_id"))
            if person is None:
                continue
            candidates.append(
                {
                    "id": person.id,
                    "first_name": person.first_name,
                    "last_name": person.last_name,
                    "primary_email": person.primary_email,
                    "mobile": person.mobile,
                    "record_state": "archived" if person.archived_at else "active",
                    "matched_on": [_evidence_code(value) for value in snapshot.get("matched_on", [])],
                    "email_agreement": snapshot.get("email_agreement"),
                    "mobile_agreement": snapshot.get("mobile_agreement"),
                    "name_agreement": snapshot.get("name_agreement"),
                    "contradiction_codes": snapshot.get("contradiction_codes", []),
                }
            )
        return candidates

    @extend_schema_field(ImportValidationErrorSerializer(many=True))
    def get_validation_errors(self, record) -> list[dict]:
        errors = []
        for error in record.validation_errors or []:
            if not isinstance(error, dict):
                continue
            field = error.get("field")
            code = error.get("code")
            message = SAFE_VALIDATION_MESSAGES.get((field, code))
            if message:
                errors.append({"field": field, "code": code, "message": message})
        return errors


class ImportReviewDetailSerializer(ImportReviewRecordSerializer):
    batch = serializers.SerializerMethodField()

    class Meta(ImportReviewRecordSerializer.Meta):
        fields = ("batch",) + ImportReviewRecordSerializer.Meta.fields

    @extend_schema_field(ImportReviewBatchContextSerializer)
    def get_batch(self, record) -> dict:
        return {
            "id": record.batch_id,
            "source_type": record.batch.source_type,
            "source_filename": record.batch.source_filename,
            "status": record.batch.status,
        }


class ImportResolvedPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    primary_email = serializers.CharField(allow_blank=True, allow_null=True)
    mobile = serializers.CharField(allow_blank=True)
    record_state = serializers.ChoiceField(choices=("active", "archived"))


class ImportRecordPreviewSerializer(ImportReviewRecordSerializer):
    resolved_person = serializers.SerializerMethodField()

    class Meta(ImportReviewRecordSerializer.Meta):
        fields = (
            "id",
            "source_row_identifier",
            "status",
            "resolution_method",
            "resolution_reason",
            "resolved_person",
            "source",
            "validation_errors",
            "reviewed_at",
            "committed_at",
        )

    @extend_schema_field(ImportResolvedPersonSerializer(allow_null=True))
    def get_resolved_person(self, record) -> dict | None:
        person = record.resolved_person
        if person is None:
            return None
        return {
            "id": person.id,
            "first_name": person.first_name,
            "last_name": person.last_name,
            "primary_email": person.primary_email,
            "mobile": person.mobile,
            "record_state": "archived" if person.archived_at else "active",
        }


class StrictSerializer(serializers.Serializer):
    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        return attrs


class ImportRecordResolutionSerializer(StrictSerializer):
    SAME_PERSON = "SAME_PERSON"
    DIFFERENT_PERSON = "DIFFERENT_PERSON"

    resolution = serializers.ChoiceField(choices=(SAME_PERSON, DIFFERENT_PERSON))
    person_id = serializers.IntegerField(required=False, min_value=1)
    confirm_identity_override = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["resolution"] == self.SAME_PERSON and "person_id" not in attrs:
            raise serializers.ValidationError({"person_id": ["This field is required for SAME_PERSON."]})
        if attrs["resolution"] == self.DIFFERENT_PERSON and "person_id" in attrs:
            raise serializers.ValidationError({"person_id": ["This field is not allowed for DIFFERENT_PERSON."]})
        if attrs["resolution"] == self.SAME_PERSON and attrs.get("confirm_identity_override"):
            raise serializers.ValidationError({"confirm_identity_override": ["This field is only allowed for DIFFERENT_PERSON."]})
        return attrs


class PaginatedImportReviewQueueSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ImportReviewRecordSerializer(many=True)


class PaginatedImportRecordPreviewSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ImportRecordPreviewSerializer(many=True)


def _evidence_code(value):
    return {"email": "EXACT_EMAIL", "mobile": "EXACT_MOBILE"}.get(value, value)


def candidate_people_for_records(records):
    candidate_ids = {
        candidate.get("person_id")
        for record in records
        for candidate in record.match_candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("person_id"), int)
    }
    return Person.objects.business().in_bulk(candidate_ids)


def _safe_upload_filename(filename):
    return str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
