import io
import uuid
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps


PROFILE_PHOTO_PREFIX = "community/profile-photos/"
PROFILE_PHOTO_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PROFILE_PHOTO_MAX_PIXELS = 25_000_000
PROFILE_PHOTO_MAX_EDGE = 1024
PROFILE_PHOTO_SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}


class ProfilePhotoValidationError(ValueError):
    """Raised for a safe, user-facing profile photo validation failure."""


@dataclass(frozen=True)
class NormalizedProfilePhoto:
    filename: str
    content: bytes


def community_profile_photo_upload_to(instance, filename):
    extension = ".png" if filename.lower().endswith(".png") else ".jpg"
    return f"{PROFILE_PHOTO_PREFIX}{uuid.uuid4().hex}{extension}"


def normalize_profile_photo(uploaded_file):
    if uploaded_file.size > PROFILE_PHOTO_MAX_UPLOAD_BYTES:
        raise ProfilePhotoValidationError("Profile photo must be 5 MiB or smaller.")

    try:
        uploaded_file.seek(0)
        raw = uploaded_file.read()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                _validate_decoded_image(image)
                image.verify()

            with Image.open(io.BytesIO(raw)) as image:
                _validate_decoded_image(image)
                normalized = ImageOps.exif_transpose(image)
                if max(normalized.size) > PROFILE_PHOTO_MAX_EDGE:
                    normalized.thumbnail(
                        (PROFILE_PHOTO_MAX_EDGE, PROFILE_PHOTO_MAX_EDGE),
                        Image.Resampling.LANCZOS,
                    )
                has_transparency = _has_meaningful_transparency(normalized)
                output = io.BytesIO()
                if has_transparency:
                    normalized.convert("RGBA").save(output, format="PNG", optimize=True)
                    extension = "png"
                else:
                    normalized.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
                    extension = "jpg"
    except ProfilePhotoValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ProfilePhotoValidationError("Profile photo dimensions are too large.")
    except (OSError, SyntaxError, ValueError):
        raise ProfilePhotoValidationError("Upload a valid JPEG, PNG or WebP image.")

    return NormalizedProfilePhoto(
        filename=f"profile-photo.{extension}",
        content=output.getvalue(),
    )


def _validate_decoded_image(image):
    if image.format not in PROFILE_PHOTO_SUPPORTED_FORMATS:
        raise ProfilePhotoValidationError("Upload a valid JPEG, PNG or WebP image.")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
        raise ProfilePhotoValidationError("Animated profile photos are not supported.")
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > PROFILE_PHOTO_MAX_PIXELS:
        raise ProfilePhotoValidationError("Profile photo dimensions must be 25 megapixels or smaller.")


def _has_meaningful_transparency(image):
    if image.mode in {"RGBA", "LA", "PA"}:
        alpha = image.convert("RGBA").getchannel("A")
        return alpha.getextrema()[0] < 255
    return "transparency" in image.info
