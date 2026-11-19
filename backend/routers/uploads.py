"""사진 업로드·조회 (프로필 사진, 후기 표지)."""
from io import BytesIO

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session as DB

from core import ApiError, field, new_id, now, ok, require_user
from db import Upload, User, get_db

router = APIRouter()
MAX_BYTES = 4 * 1024 * 1024  # Vercel 함수 요청 본문 한도(약 4.5MB) 아래로 둔다
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
        raise ApiError(413, "UPLOAD_TOO_LARGE", "사진은 4MB 이하로 올려 주세요.", field("file", "too_large"))
    # 헤더(Content-Type)는 믿지 않고 실제로 열어 형식을 확인한 뒤, 다시 저장해 EXIF(위치 정보 등)를 지운다.
    try:
        img = Image.open(BytesIO(data))
        fmt = "JPEG" if img.format == "MPO" else img.format  # MPO: 휴대폰 카메라 JPEG(여러 장 묶음), 첫 장만 저장
        pixels = img.width * img.height
        if fmt in TYPES and pixels <= MAX_PIXELS:
            img = ImageOps.exif_transpose(img)  # 여기서 실제로 디코딩한다
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        fmt = None  # Pillow는 손상 파일마다 다른 예외를 낸다
    if fmt not in TYPES:
        raise ApiError(422, "UPLOAD_INVALID_TYPE", "JPG, PNG, WEBP 사진만 올릴 수 있습니다.",
                       field("file", "invalid_type"))
    if pixels > MAX_PIXELS:
        raise ApiError(413, "UPLOAD_TOO_LARGE", "사진 해상도가 너무 큽니다. 4000만 화소 이하로 줄여 올려 주세요.",
                       field("file", "too_large"))
    out = BytesIO()
    img.save(out, fmt, quality=90)  # 다시 저장하다 실패하면 서버 오류(500)로 남긴다
    # 압축률이 높은 원본은 재인코딩 후 커질 수 있으므로 최종 응답 용량도 제한한다.
    while out.tell() > MAX_BYTES:
        width, height = img.size
        if max(width, height) <= 1:
            raise ApiError(413, "UPLOAD_TOO_LARGE", "사진 크기를 줄여 다시 올려 주세요.")
        img.thumbnail((max(1, int(width * 0.75)), max(1, int(height * 0.75))), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, fmt, quality=90)
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
