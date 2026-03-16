from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from supabase import create_client

from app.core.config import settings

_IMAGE_EXTENSIONS_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

def _pick_extension(filename: str | None, content_type: str) -> str:
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix:
            return suffix

    return _IMAGE_EXTENSIONS_BY_CONTENT_TYPE.get(content_type, ".bin")


def _get_supabase_client():
    try:
        return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage is not configured",
        ) from exc


def upload_listing_image(
    *,
    listing_id: int,
    filename: str | None,
    content_type: str,
    content: bytes,
) -> str:
    object_path = f"{listing_id}/{uuid4().hex}{_pick_extension(filename, content_type)}"
    supabase = _get_supabase_client()

    try:
        supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
            path=object_path,
            file=content,
            file_options={
                "content-type": content_type,
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Image upload failed",
        ) from exc

    return supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).get_public_url(
        object_path
    )
