"""사진 업로드·조회 (프로필 사진, 후기 표지)."""
from io import BytesIO

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps
from sqlalchemy.orm import Session as DB

from core import ApiError, field, new_id, now, ok, require_user
from db import Upload, User, get_db

router = APIRouter()
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000  # 압축 폭탄(작은 파일, 거대한 해상도) 방지
TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def upload_url(upload_id: str | None) -> str | None:
    return f"/api/v1/uploads/{upload_id}" if upload_id else None


def owned_upload(db: DB, user: User, upload_id: str, path: str) -> Upload:
    u = db.get(Upload, upload_id)
    if not u or u.user_id != user.id:
        raise ApiError(422, "MEDIA_INVALID", "사진을 찾을 수 없습니다. 다시 올려 주세요.", field(path, "not_found"))
    return u


@router.post("/uploads", status_code=201)
def create_upload(file: UploadFile, user: User = Depends(require_user), db: DB = Depends(get_db)):
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ApiError(413, "UPLOAD_TOO_LARGE", "사진은 5MB 이하로 올려 주세요.", field("file", "too_large"))
    # 헤더(Content-Type)는 믿지 않고 실제로 열어 형식을 확인한 뒤, 다시 저장해 EXIF(위치 정보 등)를 지운다.
    try:
        img = Image.open(BytesIO(data))
        fmt = img.format
        if fmt not in TYPES or img.width * img.height > MAX_PIXELS:
            raise ValueError(fmt)
        out = BytesIO()
        ImageOps.exif_transpose(img).save(out, fmt, quality=90)
    except Exception:  # Pillow는 손상 파일마다 다른 예외를 낸다
        raise ApiError(422, "UPLOAD_INVALID_TYPE", "JPG, PNG, WEBP 사진만 올릴 수 있습니다.",
                       field("file", "invalid_type")) from None
    content = out.getvalue()
    u = Upload(id=new_id("upl"), user_id=user.id, content_type=TYPES[fmt], size=len(content), content=content,
               created_at=now())
    db.add(u)
    db.commit()
    return ok({"id": u.id, "url": upload_url(u.id), "contentType": u.content_type, "size": u.size}, status=201)


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str, db: DB = Depends(get_db)):
    u = db.get(Upload, upload_id)
    if not u:
        raise ApiError(404, "UPLOAD_NOT_FOUND", "사진을 찾을 수 없습니다.")
    # 내용이 바뀌지 않는 공개 사진이라 오래 캐시한다(미들웨어는 setdefault라 이 값이 남는다).
    return Response(u.content, media_type=u.content_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff"})
