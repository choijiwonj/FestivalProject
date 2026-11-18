"""프로필·여행 기본 설정 (G01 F20, G07 F22~F24)."""
from typing import Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DB
from sqlalchemy.orm.exc import StaleDataError

from core import ApiError, field, iso, now, ok, require_user, verify_location
from db import User, UserSettings, get_db
from routers.auth import user_out
from routers.catalog import Interest

router = APIRouter()


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nickname: str | None = None
    avatarMediaId: str | None = None


class TravelDefaultsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    originLocationId: str | None = None
    transport: Literal["CAR", "PUBLIC_TRANSIT"] | None = None
    travelers: int | None = Field(None, ge=1, le=20)
    interests: list[Interest] | None = None
    style: Literal["RELAXED", "PACKED"] | None = None
    lodgingPreference: Literal["HOTEL", "PENSION", "GUESTHOUSE", "ANY", "SELF_ARRANGED"] | None = None


class PrivacyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    saveOrigin: bool


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    travelDefaults: TravelDefaultsPatch | None = None
    privacy: PrivacyPatch | None = None


def settings_out(s: UserSettings) -> dict:
    return {"version": s.version,
            "travelDefaults": {"origin": s.origin, "transport": s.transport, "travelers": s.travelers,
                               "interests": s.interests or [], "style": s.style,
                               "lodgingPreference": s.lodging_preference},
            "privacy": {"saveOrigin": s.save_origin}, "updatedAt": iso(s.updated_at)}


def load_settings(db: DB, user: User) -> UserSettings:
    s = db.get(UserSettings, user.id)
    if not s:  # 설정 없는 회원도 404 대신 기본 구조
        s = UserSettings(user_id=user.id, interests=[])
        db.add(s)
        db.commit()
    return s


def save(db: DB) -> None:
    try:
        db.commit()
    except StaleDataError:  # 다른 탭이 먼저 저장함
        db.rollback()
        raise ApiError(409, "VERSION_CONFLICT", "다른 곳에서 설정이 바뀌었습니다. 최신 설정을 확인해 주세요.")


@router.get("/me")
def me(user: User = Depends(require_user)):
    return ok(user_out(user))


@router.patch("/me")
def patch_me(body: ProfilePatch, user: User = Depends(require_user), db: DB = Depends(get_db)):
    sent = body.model_fields_set
    if not sent:
        raise ApiError(422, "EMPTY_UPDATE", "변경할 값이 없습니다.")
    if "nickname" in sent:
        nick = (body.nickname or "").strip()
        if not 2 <= len(nick) <= 20:
            raise ApiError(422, "INVALID_NICKNAME", "닉네임은 2~20자로 입력해 주세요.", field("nickname", "length"))
        user.nickname = nick
    if "avatarMediaId" in sent:
        # ponytail: 사진 업로드(media API) 연결 전이라 초기화(null)만 허용.
        if body.avatarMediaId is not None:
            raise ApiError(422, "MEDIA_INVALID", "사진 업로드는 아직 준비 중입니다.", field("avatarMediaId", "not_ready"))
        user.avatar_url = None
    db.commit()
    return ok(user_out(user))


@router.get("/me/settings")
def get_settings(user: User = Depends(require_user), db: DB = Depends(get_db)):
    return ok(settings_out(load_settings(db, user)))


@router.patch("/me/settings")
def patch_settings(body: SettingsPatch, user: User = Depends(require_user), db: DB = Depends(get_db)):
    s = load_settings(db, user)
    if body.version != s.version:
        raise ApiError(409, "VERSION_CONFLICT", "다른 곳에서 설정이 바뀌었습니다. 최신 설정을 확인해 주세요.")
    td, pv = body.travelDefaults, body.privacy
    td_sent = td.model_fields_set if td else set()
    if not td_sent and not (pv and pv.model_fields_set):
        raise ApiError(422, "EMPTY_UPDATE", "변경할 값이 없습니다.")

    save_origin = pv.saveOrigin if pv else s.save_origin
    if "originLocationId" in td_sent:
        if td.originLocationId is None:
            s.origin = None
        elif not save_origin:
            raise ApiError(422, "ORIGIN_STORAGE_DISABLED", "기본 출발지 저장을 먼저 켜 주세요.",
                           field("travelDefaults.originLocationId", "storage_disabled"))
        else:
            loc = verify_location(td.originLocationId)
            if not loc:
                raise ApiError(422, "LOCATION_NOT_FOUND", "출발지를 검색 결과에서 다시 선택해 주세요.",
                               field("travelDefaults.originLocationId", "not_found"))
            s.origin = loc
    if "interests" in td_sent:
        if td.interests is None:
            raise ApiError(422, "INVALID_SETTING_VALUE", "관심사는 빈 목록으로 초기화해 주세요.",
                           field("travelDefaults.interests", "null"))
        s.interests = list(dict.fromkeys(td.interests))
    for name, col in (("transport", "transport"), ("travelers", "travelers"), ("style", "style"),
                      ("lodgingPreference", "lodging_preference")):
        if name in td_sent:
            setattr(s, col, getattr(td, name))
    s.save_origin = save_origin
    if not save_origin:  # 끄면 기본 출발지도 함께 삭제 (B07: 계획 속 출발지는 그대로)
        s.origin = None
    s.updated_at = now()
    save(db)
    return ok(settings_out(s))


@router.delete("/me/settings/origin")
def delete_origin(if_match: str | None = Header(None, alias="If-Match"), user: User = Depends(require_user),
                  db: DB = Depends(get_db)):
    if not if_match:
        raise ApiError(400, "VERSION_REQUIRED", "설정 버전(If-Match)이 필요합니다.")
    s = load_settings(db, user)
    if if_match.strip('" ') != str(s.version):
        raise ApiError(409, "VERSION_CONFLICT", "다른 곳에서 설정이 바뀌었습니다. 최신 설정을 확인해 주세요.")
    if s.origin is not None:  # 이미 없으면 변경 없이 200
        s.origin, s.updated_at = None, now()
        save(db)
    return ok(settings_out(s))
