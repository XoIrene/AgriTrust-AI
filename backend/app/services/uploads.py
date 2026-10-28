"""Store a farmer's own photograph of their farm.

An insurer deciding on a drought policy wants to see the field, so the farmer can
upload one from the insurance screen. That makes this an endpoint anyone with an
account can hit, so the rules are narrow and the checks are on the bytes rather
than on what the client says:

* the name on disk is always generated here, never taken from the upload, so a
  filename can never escape the upload directory or overwrite anything;
* the type is decided by the file's magic bytes, not its ``Content-Type`` or its
  extension, because both are whatever the caller felt like sending;
* the size is capped while reading, so an oversized file is refused without ever
  being held in memory;
* SVG is deliberately not accepted: it is a document that runs script, and this
  file is served back from the same origin as the app.
"""
from __future__ import annotations

import logging
import secrets
from typing import Final

from fastapi import UploadFile

from app.config import settings
from app.i18n import translate

logger = logging.getLogger("agritrust.uploads")

#: generous for a phone photo, small enough that a hundred farms' worth is sane
MAX_PHOTO_BYTES: Final[int] = 5 * 1024 * 1024

#: extension and magic bytes for each type we accept, in preference order
IMAGE_TYPES: Final[tuple[tuple[str, bytes], ...]] = (
    ("jpg", b"\xff\xd8\xff"),
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("webp", b"RIFF"),
)

UPLOAD_SUBDIR: Final[str] = "uploads/farms"


class UploadRejected(Exception):
    """The upload was refused. ``key`` is a translation key, not a sentence."""

    def __init__(self, key: str, status_code: int = 422) -> None:
        super().__init__(key)
        self.key = key
        self.status_code = status_code


def upload_dir():
    """The directory holding farm photos, created on first use."""
    path = settings.frontend_dir / "static" / UPLOAD_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def photo_url(filename: str | None) -> str | None:
    """The public URL for a stored photo, or None when there is not one.

    Only ever echoes back the fixed prefix plus a name this module generated, so
    no caller-supplied text can reach the response.
    """
    if not filename:
        return None
    return f"/static/{UPLOAD_SUBDIR}/{filename}"


def _sniff(head: bytes) -> str | None:
    """The image type from the file's own leading bytes, or None if unknown."""
    for ext, magic in IMAGE_TYPES:
        if ext == "webp":
            # RIFF....WEBP: the container alone is not enough to call it an image
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return ext
            continue
        if head.startswith(magic):
            return ext
    return None


async def save_farm_photo(farm_id: str, upload: UploadFile) -> str:
    """Store an uploaded farm photo and return the generated filename.

    Replaces nothing: the caller keeps the previous filename until this succeeds,
    so a rejected upload never costs the farmer the photo they already had.
    """
    # read one byte past the cap, so "too big" is decided without buffering the
    # whole thing
    data = await upload.read(MAX_PHOTO_BYTES + 1)
    if not data:
        raise UploadRejected("upload.photo_empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise UploadRejected("upload.photo_too_large")

    ext = _sniff(data[:16])
    if ext is None:
        raise UploadRejected("upload.photo_not_an_image")

    # generated, and the farm id in it is one of our own 32-char hex ids
    name = f"{farm_id}_{secrets.token_hex(8)}.{ext}"
    target = upload_dir() / name
    target.write_bytes(data)
    logger.info("stored farm photo %s for farm %s (%d bytes)", name, farm_id, len(data))
    return name


def delete_photo(filename: str | None) -> None:
    """Remove a stored photo, ignoring one that is already gone.

    The name is checked against the pattern this module writes, so a corrupt or
    hand-edited database value cannot turn a delete into a path walk.
    """
    if not filename or not filename.endswith(tuple(f".{ext}" for ext, _ in IMAGE_TYPES)):
        return
    if "_" not in filename or not filename.split("_")[-1].split(".")[0].isalnum():
        return
    target = upload_dir() / filename
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("could not remove farm photo %s", filename, exc_info=True)


def rejected_detail(exc: UploadRejected, lang: str | None = None) -> str:
    """The translated reason, for an error response."""
    return translate(exc.key, lang)
