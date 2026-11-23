from django.test import SimpleTestCase

from config.storage import StorageConfigurationError, build_storage_config


class StorageConfigurationTests(SimpleTestCase):
    def test_local_storage_keeps_filesystem_default_and_whitenoise_staticfiles(self):
        storage = build_storage_config(use_s3=False)

        self.assertEqual(
            storage["default"]["BACKEND"],
            "django.core.files.storage.FileSystemStorage",
        )
        self.assertEqual(
            storage["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )

    def test_s3_storage_is_private_signed_and_keeps_whitenoise_staticfiles(self):
        storage = build_storage_config(
            use_s3=True,
            bucket_name="elevate-mk-assets-staging",
            region_name="eu-west-2",
            access_key_id="access-key",
            secret_access_key="secret-key",
            querystring_expire=600,
        )

        self.assertEqual(storage["default"]["BACKEND"], "storages.backends.s3.S3Storage")
        self.assertEqual(storage["default"]["OPTIONS"]["bucket_name"], "elevate-mk-assets-staging")
        self.assertEqual(storage["default"]["OPTIONS"]["region_name"], "eu-west-2")
        self.assertIsNone(storage["default"]["OPTIONS"]["default_acl"])
        self.assertTrue(storage["default"]["OPTIONS"]["querystring_auth"])
        self.assertEqual(storage["default"]["OPTIONS"]["querystring_expire"], 600)
        self.assertEqual(storage["default"]["OPTIONS"]["addressing_style"], "virtual")
        self.assertEqual(
            storage["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )

    def test_s3_storage_accepts_path_addressing_style(self):
        storage = build_storage_config(
            use_s3=True,
            bucket_name="elevate-mk-assets-staging",
            region_name="eu-west-2",
            access_key_id="access-key",
            secret_access_key="secret-key",
            addressing_style="path",
        )

        self.assertEqual(storage["default"]["OPTIONS"]["addressing_style"], "path")

    def test_s3_storage_rejects_unknown_addressing_style(self):
        with self.assertRaisesMessage(
            StorageConfigurationError,
            "AWS_S3_ADDRESSING_STYLE must be one of: path, virtual",
        ):
            build_storage_config(
                use_s3=True,
                bucket_name="elevate-mk-assets-staging",
                region_name="eu-west-2",
                access_key_id="access-key",
                secret_access_key="secret-key",
                addressing_style="regional",
            )

    def test_enabled_s3_storage_fails_when_required_configuration_is_missing(self):
        with self.assertRaisesRegex(StorageConfigurationError, "AWS_SECRET_ACCESS_KEY"):
            build_storage_config(
                use_s3=True,
                bucket_name="elevate-mk-assets-staging",
                region_name="eu-west-2",
                access_key_id="access-key",
            )
