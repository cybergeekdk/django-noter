from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}
ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp"]
CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}


def validate_image_file(file):
    """Størrelse og faktisk filformat (ikke kun filendelsen). SVG afvises bevidst."""
    if file.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"Billedet er for stort (maks. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).", code="too_large"
        )
    try:
        file.seek(0)
        with Image.open(file) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ValidationError("Filen er ikke et gyldigt billede.", code="invalid_image")
    finally:
        file.seek(0)
    if image_format not in ALLOWED_FORMATS:
        raise ValidationError("Kun JPEG, PNG, GIF og WEBP understøttes.", code="bad_format")
