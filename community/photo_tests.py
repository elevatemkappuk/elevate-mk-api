import io
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import storages
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from brevo_marketing.jobs import PERSON_PROFILE_SYNC
from community.models import CommunityProfile
from community.photos import (
    PROFILE_PHOTO_MAX_PIXELS,
    normalize_profile_photo,
)
from community.services import upload_community_profile_photo
from external_references.models import ExternalPersonSyncJob
from memberships.models import Membership
from people.models import Person


class TestMemoryStorage(Storage):
    files = {}

    def _open(self, name, mode="rb"):
        return ContentFile(self.files[name], name=name)

    def _save(self, name, content):
        content.seek(0)
        self.files[name] = content.read()
        return name

    def delete(self, name):
        self.files.pop(name, None)

    def exists(self, name):
        return name in self.files

    def url(self, name):
        return f"/media/{name}"

    def size(self, name):
        return len(self.files[name])


class CommunityProfilePhotoTests(TestCase):
    photo_url = "/api/v1/community/profile/photo/"
    profile_url = "/api/v1/community/profile/"

    def setUp(self):
        self.storage_override = override_settings(
            MEDIA_URL="/media/",
            STORAGES={
                "default": {
                    "BACKEND": "community.photo_tests.TestMemoryStorage",
                },
                "staticfiles": {
                    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
                },
            },
        )
        self.storage_override.enable()
        storages._storages.clear()
        TestMemoryStorage.files.clear()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(storages._storages.clear)
        self.addCleanup(TestMemoryStorage.files.clear)

        self.client = APIClient()
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            location="Milton Keynes",
            primary_email="photo@example.com",
        )
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email="photo@example.com",
            password="Strong-password-123!",
            person=self.person,
        )
        self.client.force_authenticate(user=self.user)

    def image_upload(self, image_format="JPEG", size=(100, 80), filename="avatar.jpg", **save_options):
        transparent = save_options.pop("transparent", False)
        image = Image.new("RGBA" if image_format == "PNG" and transparent else "RGB", size, (40, 120, 80, 0) if transparent else (40, 120, 80))
        output = io.BytesIO()
        image.save(output, format=image_format, **save_options)
        return SimpleUploadedFile(filename, output.getvalue(), content_type=f"image/{image_format.lower()}")

    def post_photo(self, upload):
        return self.client.post(self.photo_url, {"photo": upload}, format="multipart")

    def test_uploads_jpeg_and_projects_ephemeral_photo_url_without_brevo_or_completion_change(self):
        before = self.client.get(self.profile_url).data

        response = self.post_photo(self.image_upload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["community"]["photo_url"].startswith("/media/community/profile-photos/"))
        profile = CommunityProfile.objects.get(person=self.person)
        self.assertTrue(profile.photo.name.startswith("community/profile-photos/"))
        self.assertNotIn("avatar", profile.photo.name)
        self.assertEqual(response.data["completion"], before["completion"])
        self.assertFalse(ExternalPersonSyncJob.objects.filter(person=self.person, job_type=PERSON_PROFILE_SYNC).exists())
        event = AuditEvent.objects.filter(entity_type="CommunityProfile", entity_id=str(CommunityProfile.objects.get(person=self.person).id)).first()
        self.assertEqual(event.action, AuditEvent.Action.PERSON_UPDATED)
        self.assertIn("photo_uploaded", event.changes)

    def test_uploads_png_and_preserves_transparency(self):
        response = self.post_photo(self.image_upload("PNG", filename="avatar.png", transparent=True))

        self.assertEqual(response.status_code, 200)
        profile = CommunityProfile.objects.get(person=self.person)
        with profile.photo.storage.open(profile.photo.name, "rb") as stored_file:
            with Image.open(stored_file) as stored:
                self.assertEqual(stored.format, "PNG")
                self.assertEqual(stored.getchannel("A").getextrema()[0], 0)

    def test_uploads_webp(self):
        response = self.post_photo(self.image_upload("WEBP", filename="avatar.webp"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(CommunityProfile.objects.get(person=self.person).photo.name.endswith(".jpg"))

    def test_normalization_applies_orientation_strips_metadata_and_resizes_without_upscaling(self):
        image = Image.new("RGB", (1200, 600), "green")
        exif = Image.Exif()
        exif[274] = 6
        output = io.BytesIO()
        image.save(output, format="JPEG", exif=exif)
        normalized = normalize_profile_photo(SimpleUploadedFile("original.jpeg", output.getvalue()))

        with Image.open(io.BytesIO(normalized.content)) as stored:
            self.assertEqual(stored.size, (512, 1024))
            self.assertEqual(stored.getexif(), {})
        self.assertEqual(normalized.filename, "profile-photo.jpg")

        small = normalize_profile_photo(self.image_upload(size=(80, 60)))
        with Image.open(io.BytesIO(small.content)) as stored:
            self.assertEqual(stored.size, (80, 60))

    def test_decoded_dimension_limit_is_25_megapixels(self):
        self.assertEqual(PROFILE_PHOTO_MAX_PIXELS, 25_000_000)
        from types import SimpleNamespace
        from community.photos import ProfilePhotoValidationError, _validate_decoded_image

        with self.assertRaises(ProfilePhotoValidationError):
            _validate_decoded_image(SimpleNamespace(format="JPEG", size=(6000, 4500), is_animated=False, n_frames=1, info={}))

    def test_rejects_animation_unsupported_formats_and_malformed_uploads_without_replacing_existing_photo(self):
        self.assertEqual(self.post_photo(self.image_upload()).status_code, 200)
        original_name = CommunityProfile.objects.get(person=self.person).photo.name

        cases = [
            SimpleUploadedFile("avatar.gif", b"GIF89a", content_type="image/gif"),
            SimpleUploadedFile("avatar.svg", b"<svg></svg>", content_type="image/svg+xml"),
            SimpleUploadedFile("avatar.jpg", b"not-an-image", content_type="image/jpeg"),
        ]
        for upload in cases:
            response = self.post_photo(upload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(CommunityProfile.objects.get(person=self.person).photo.name, original_name)

    def test_rejects_upload_over_5_mib_without_replacing_existing_photo(self):
        self.assertEqual(self.post_photo(self.image_upload()).status_code, 200)
        original_name = CommunityProfile.objects.get(person=self.person).photo.name
        oversized = SimpleUploadedFile("avatar.jpg", b"x" * (5 * 1024 * 1024 + 1), content_type="image/jpeg")

        response = self.post_photo(oversized)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(CommunityProfile.objects.get(person=self.person).photo.name, original_name)

    def test_replacement_removes_old_object_only_after_new_reference_is_committed(self):
        self.assertEqual(self.post_photo(self.image_upload()).status_code, 200)
        profile = CommunityProfile.objects.get(person=self.person)
        old_name = profile.photo.name

        with self.captureOnCommitCallbacks(execute=True):
            response = self.post_photo(self.image_upload(filename="replacement.jpg"))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertNotEqual(profile.photo.name, old_name)
        self.assertFalse(profile.photo.storage.exists(old_name))
        self.assertTrue(profile.photo.storage.exists(profile.photo.name))
        self.assertIn("photo_replaced", AuditEvent.objects.filter(entity_type="CommunityProfile").first().changes)

    def test_removal_is_idempotent_and_returns_empty_photo_projection(self):
        self.assertEqual(self.post_photo(self.image_upload()).status_code, 200)
        stored_name = CommunityProfile.objects.get(person=self.person).photo.name

        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.delete(self.photo_url)
        second = self.client.delete(self.photo_url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIsNone(first.data["community"]["photo_url"])
        self.assertIsNone(second.data["community"]["photo_url"])
        self.assertFalse(CommunityProfile.objects.get(person=self.person).photo)
        self.assertNotIn(stored_name, TestMemoryStorage.files)

    def test_storage_failure_does_not_change_existing_reference(self):
        self.assertEqual(self.post_photo(self.image_upload()).status_code, 200)
        original_name = CommunityProfile.objects.get(person=self.person).photo.name

        with mock.patch.object(TestMemoryStorage, "_save", side_effect=OSError("storage unavailable")):
            with self.assertRaises(OSError):
                upload_community_profile_photo(
                    person_id=self.person.id,
                    uploaded_file=self.image_upload(filename="replacement.jpg"),
                    request=mock.Mock(user=self.user, headers={}, META={}),
                )

        self.assertEqual(CommunityProfile.objects.get(person=self.person).photo.name, original_name)

    def test_db_failure_after_storage_save_cleans_new_object_and_keeps_existing_reference(self):
        CommunityProfile.objects.create(person=self.person)
        with mock.patch.object(CommunityProfile, "save", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                upload_community_profile_photo(
                    person_id=self.person.id,
                    uploaded_file=self.image_upload(),
                    request=mock.Mock(user=self.user, headers={}, META={}),
                )

        profile = CommunityProfile.objects.get(person=self.person)
        self.assertFalse(profile.photo.name)
