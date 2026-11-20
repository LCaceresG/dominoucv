"""Publicaciones de redes sociales (Instagram @dominoucv) guardadas en la base de datos."""
import base64
import binascii
import re
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SocialPost
from .schemas import SocialPostCreate, SocialPostUpdate

INSTAGRAM_HANDLE = "dominoucv"
INSTAGRAM_URL = f"https://www.instagram.com/{INSTAGRAM_HANDLE}/"
MAX_IMAGE_BYTES = 3_000_000
_DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$")


def decode_image(data_url: str) -> tuple[bytes, str]:
    """Valida y decodifica una imagen enviada como data URL. Solo jpeg, png o webp, hasta 3 MB."""
    m = _DATA_URL.match(data_url.strip())
    if not m:
        raise HTTPException(422, "La imagen debe ser JPG, PNG o WEBP")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "No se pudo leer la imagen") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "La imagen pesa más de 3 MB")
    kind = m.group(1)
    ok = (
        (kind == "image/jpeg" and raw[:3] == b"\xff\xd8\xff")
        or (kind == "image/png" and raw[:8] == b"\x89PNG\r\n\x1a\n")
        or (kind == "image/webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
    )
    if not ok:
        raise HTTPException(422, "El archivo no es una imagen válida")
    return raw, kind


def post_dict(p: SocialPost) -> dict:
    return {
        "id": p.id,
        "posted_on": p.posted_on.isoformat(),
        "title": p.title,
        "summary": p.summary,
        "link": p.link,
        "has_image": p.image_type is not None,
        "image_url": f"/api/social/{p.id}/image?v={p.image_rev}" if p.image_type else None,
    }


def list_posts(db: Session, limit: int = 60) -> list[SocialPost]:
    return list(
        db.scalars(select(SocialPost).order_by(SocialPost.posted_on.desc(), SocialPost.id.desc()).limit(limit)).all()
    )


def get_post(db: Session, post_id: int) -> SocialPost:
    p = db.get(SocialPost, post_id)
    if p is None:
        raise HTTPException(404, "Publicación no encontrada")
    return p


def create_post(db: Session, data: SocialPostCreate) -> SocialPost:
    p = SocialPost(posted_on=data.posted_on or date.today(), title=data.title, summary=data.summary, link=data.link)
    if data.image_data:
        p.image, p.image_type = decode_image(data.image_data)
        p.image_rev = 1
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def update_post(db: Session, p: SocialPost, data: SocialPostUpdate) -> SocialPost:
    fields = data.model_fields_set
    if "posted_on" in fields and data.posted_on:
        p.posted_on = data.posted_on
    if "title" in fields:
        p.title = (data.title or "").strip() or None
    if "summary" in fields and data.summary:
        p.summary = data.summary.strip()
    if "link" in fields:
        p.link = data.link
    if data.image_data:
        p.image, p.image_type = decode_image(data.image_data)
        p.image_rev += 1
    elif data.remove_image:
        p.image = p.image_type = None
        p.image_rev += 1
    db.commit()
    db.refresh(p)
    return p
