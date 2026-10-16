import os

from django.core.exceptions import ValidationError

from guides.validators import MAX_UPLOAD_BYTES, validate_image_file

ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp", "pdf"]


def validate_attachment(file):
    """Billeder (jpg/png/gif/webp) eller PDF. Indholdet tjekkes, ikke kun filendelsen."""
    ext = os.path.splitext(file.name)[1].lower().lstrip(".")
    if ext == "pdf":
        if file.size > MAX_UPLOAD_BYTES:
            raise ValidationError(f"Filen er for stor (maks. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).", code="too_large")
        file.seek(0)
        header = file.read(1024)
        file.seek(0)
        if b"%PDF-" not in header:
            raise ValidationError("Filen er ikke en gyldig PDF.", code="invalid_pdf")
    else:
        validate_image_file(file)
