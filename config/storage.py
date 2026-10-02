"""Build the backend storage configuration for local and deployed environments."""


class StorageConfigurationError(RuntimeError):
    """Raised when explicitly enabled S3 storage is incompletely configured."""


SUPPORTED_S3_ADDRESSING_STYLES = {"virtual", "path"}


def build_storage_config(
    *,
    use_s3,
    bucket_name="",
    region_name="",
    access_key_id="",
    secret_access_key="",
    querystring_expire=900,
    addressing_style="virtual",
):
    """Return Django STORAGES configuration without contacting the provider."""
    staticfiles = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }

    if not use_s3:
        return {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": staticfiles,
        }

    if addressing_style not in SUPPORTED_S3_ADDRESSING_STYLES:
        raise StorageConfigurationError(
            "AWS_S3_ADDRESSING_STYLE must be one of: "
            + ", ".join(sorted(SUPPORTED_S3_ADDRESSING_STYLES))
        )

    missing = [
        name
        for name, value in (
            ("AWS_STORAGE_BUCKET_NAME", bucket_name),
            ("AWS_S3_REGION_NAME", region_name),
            ("AWS_ACCESS_KEY_ID", access_key_id),
            ("AWS_SECRET_ACCESS_KEY", secret_access_key),
        )
        if not str(value).strip()
    ]
    if missing:
        raise StorageConfigurationError(
            "USE_S3_STORAGE is enabled but required settings are missing: "
            + ", ".join(missing)
        )

    return {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": bucket_name,
                "region_name": region_name,
                "access_key": access_key_id,
                "secret_key": secret_access_key,
                "default_acl": None,
                "querystring_auth": True,
                "querystring_expire": querystring_expire,
                "file_overwrite": False,
                "addressing_style": addressing_style,
            },
        },
        "staticfiles": staticfiles,
    }
