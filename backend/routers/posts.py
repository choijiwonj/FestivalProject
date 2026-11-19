"""축제 후기 게시판."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from core import ApiError, field, iso, new_id, now, ok, paginate, require_user
from db import Festival, Post, User, get_db
from routers.uploads import owned_upload, upload_url

router = APIRouter()


class PostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    festivalId: str
    body: str
    coverUploadId: str | None = None


class PostPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str | None = None
    coverUploadId: str | None = None


def clean_body(body: str | None) -> str:
    text = (body or "").strip()
    if not 1 <= len(text) <= 2000:
        raise ApiError(422, "INVALID_POST_BODY", "후기는 1~2000자로 입력해 주세요.", field("body", "length"))
    return text


def post_out(p: Post) -> dict:
    return {"id": p.id, "festivalId": p.festival_id, "body": p.body, "coverUrl": upload_url(p.cover_upload_id),
            "author": {"id": p.user.id, "nickname": p.user.nickname, "avatarUrl": p.user.avatar_url},
            "createdAt": iso(p.created_at), "updatedAt": iso(p.updated_at)}


def own_post(db: DB, user: User, post_id: str) -> Post:
    p = db.get(Post, post_id)
    if not p or p.user_id != user.id:  # 타인 글도 404
        raise ApiError(404, "POST_NOT_FOUND", "후기를 찾을 수 없습니다.")
    return p


def newest(stmt):
    return stmt.order_by(Post.created_at.desc(), Post.id.desc())


@router.get("/posts")
def list_posts(festivalId: str | None = None, cursor: str | None = None, limit: int = Query(20, ge=1, le=50),
               db: DB = Depends(get_db)):
    stmt = select(Post)
    if festivalId:
        stmt = stmt.where(Post.festival_id == festivalId)
    chunk, page = paginate(list(db.scalars(newest(stmt))), cursor, limit)
    return ok([post_out(p) for p in chunk], page=page)


@router.get("/posts/{post_id}")
def get_post(post_id: str, db: DB = Depends(get_db)):
    p = db.get(Post, post_id)
    if not p:
        raise ApiError(404, "POST_NOT_FOUND", "후기를 찾을 수 없습니다.")
    return ok(post_out(p))


# ponytail: 멱등 키 없음(작업을 만들지 않는 단순 저장). 중복 등록이 문제 되면 Idempotency-Key를 붙인다.
@router.post("/posts", status_code=201)
def create_post(body: PostInput, user: User = Depends(require_user), db: DB = Depends(get_db)):
    if not db.get(Festival, body.festivalId):
        raise ApiError(422, "FESTIVAL_NOT_FOUND", "축제를 찾을 수 없습니다.", field("festivalId", "not_found"))
    text = clean_body(body.body)
    if body.coverUploadId is not None:
        owned_upload(db, user, body.coverUploadId, "coverUploadId")
    p = Post(id=new_id("pst"), user_id=user.id, festival_id=body.festivalId, body=text,
             cover_upload_id=body.coverUploadId,
             created_at=now(), updated_at=now())
    db.add(p)
    db.commit()
    return ok(post_out(p), status=201)


@router.patch("/posts/{post_id}")
def patch_post(post_id: str, body: PostPatch, user: User = Depends(require_user), db: DB = Depends(get_db)):
    p = own_post(db, user, post_id)
    sent = body.model_fields_set
    if not sent:
        raise ApiError(422, "EMPTY_UPDATE", "변경할 값이 없습니다.")
    if "body" in sent:
        p.body = clean_body(body.body)
    if "coverUploadId" in sent:
        if body.coverUploadId is not None:
            owned_upload(db, user, body.coverUploadId, "coverUploadId")
        p.cover_upload_id = body.coverUploadId
    p.updated_at = now()
    db.commit()
    return ok(post_out(p))


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    db.delete(own_post(db, user, post_id))
    db.commit()


@router.get("/me/posts")
def my_posts(cursor: str | None = None, limit: int = Query(20, ge=1, le=50),
             user: User = Depends(require_user), db: DB = Depends(get_db)):
    chunk, page = paginate(list(db.scalars(newest(select(Post).where(Post.user_id == user.id)))), cursor, limit)
    return ok([{"id": p.id, "excerpt": p.body[:80], "createdAt": iso(p.created_at), "festivalId": p.festival_id,
                "coverUrl": upload_url(p.cover_upload_id)} for p in chunk], page=page)
