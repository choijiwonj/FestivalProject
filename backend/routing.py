"""카카오모빌리티 자동차 길찾기."""

import logging
from dataclasses import dataclass
from datetime import datetime
from math import ceil

import httpx

from core import ApiError, iso, now

DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CarRoute:
    duration_seconds: int
    distance_meters: int

    @property
    def duration_minutes(self) -> int:
        return ceil(self.duration_seconds / 60)

    def as_leg(self, from_id: str, to_id: str, departure: datetime | None = None,
               arrival: datetime | None = None) -> dict:
        return {
            "fromStopId": from_id, "toStopId": to_id, "mode": "CAR",
            "departureAt": iso(departure), "arrivalAt": iso(arrival),
            "durationMinutes": self.duration_minutes, "distanceMeters": self.distance_meters,
            "fareKrw": None, "status": "ESTIMATED", "provider": "KAKAO_MOBILITY", "retrievedAt": iso(now()),
        }


def get_car_route(api_key: str, origin: tuple[float, float], destination: tuple[float, float]) -> CarRoute | None:
    """위도·경도 쌍을 받아 조회 시점의 예상 시간과 거리를 반환한다."""
    try:
        response = httpx.get(
            DIRECTIONS_URL,
            params={
                "origin": f"{origin[1]},{origin[0]}",
                "destination": f"{destination[1]},{destination[0]}",
                "summary": "true",
            },
            headers={"Authorization": f"KakaoAK {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        log.warning("Kakao Mobility directions timeout")
        raise ApiError(503, "TIMEOUT", "자동차 길찾기 응답 시간이 초과되었습니다.", retryable=True) from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        log.warning("Kakao Mobility directions HTTP status=%s", status)
        if status == 429:
            raise ApiError(429, "RATE_LIMITED", "자동차 길찾기 호출 한도에 도달했습니다.", retryable=True) from None
        if status in (401, 403):
            raise ApiError(503, "PROVIDER_UNAVAILABLE", "자동차 길찾기 키나 앱 권한을 확인해 주세요.") from None
        raise ApiError(503, "PROVIDER_UNAVAILABLE", "자동차 길찾기를 호출할 수 없습니다.",
                       retryable=status >= 500) from None
    except httpx.RequestError as exc:
        log.warning("Kakao Mobility directions connection failure: %s", type(exc).__name__)
        raise ApiError(503, "PROVIDER_UNAVAILABLE", "자동차 길찾기에 연결할 수 없습니다.", retryable=True) from None

    try:
        routes = response.json()["routes"]
        if routes == []:
            return None
        route = routes[0]
        result_code = route["result_code"]
        if type(result_code) is not int:
            raise ValueError("Invalid route result code")
        if result_code != 0:
            return None
        summary = route["summary"]
        duration, distance = summary["duration"], summary["distance"]
        if type(duration) is not int or type(distance) is not int or min(duration, distance) < 0:
            raise ValueError("Invalid route duration or distance")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("Kakao Mobility directions invalid response: %s", type(exc).__name__)
        raise ApiError(503, "PROVIDER_UNAVAILABLE", "자동차 길찾기 응답을 확인할 수 없습니다.",
                       retryable=True) from None

    return CarRoute(duration_seconds=duration, distance_meters=distance)
