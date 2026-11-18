"""ODsay static city transit routes; no verified service timetable."""
import logging
from dataclasses import dataclass
from math import ceil, isfinite
import httpx
from core import ApiError, distance_m
from routing import CarRoute

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransitRoute(CarRoute):
    segments: tuple = ()
    fare: int | None = None

    def as_leg(self, from_id, to_id, departure=None, arrival=None):
        result = super().as_leg(from_id, to_id, departure, arrival)
        result.update(mode="PUBLIC_TRANSIT", provider="ODSAY", fareKrw=self.fare,
                      segments=list(self.segments), serviceTimeStatus="UNCONFIRMED",
                      waitingTimeStatus="UNCONFIRMED", timeBasis="ESTIMATED_DURATION")
        return result


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError("Invalid transit number")
    return value


class TransitRouteSession:
    """One plan's shared, normalized ODsay route cache."""
    def __init__(self, api_key):
        self.api_key = api_key
        self.call_count = 0
        self.cache = {}

    @staticmethod
    def coordinates(point):
        return tuple(round(float(value), 5) for value in point)

    def get_route(self, origin, destination, local_only=False):
        origin_key, destination_key = self.coordinates(origin), self.coordinates(destination)
        if origin_key == destination_key:
            return TransitRoute(0, 0, (), 0)
        key = ('PUBLIC_TRANSIT', local_only, origin_key, destination_key)
        if key in self.cache:
            return self.cache[key]
        self.call_count += 1
        result = fetch_transit_route(self, origin, destination, local_only)
        self.cache[key] = result  # Cache successful routes and no-route results.
        return result

    def schedule_loader(self, _api_key, origin, destination):
        return self.get_route(origin, destination)


def get_transit_route(api_key, origin, destination, _local_only=False):
    return TransitRouteSession(api_key).get_route(origin, destination, _local_only)


def fetch_transit_route(session, origin, destination, local_only=False):
    if origin == destination:
        return TransitRoute(0, 0, (), 0)
    try:
        response = httpx.get('https://api.odsay.com/v1/api/searchPubTransPathT',
                            params={'apiKey': session.api_key, 'SX': origin[1], 'SY': origin[0],
                                    'EX': destination[1], 'EY': destination[0], 'SearchType': 0}, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Never propagate or log the request URL (or str(exc)): it contains the API key.
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        log.warning('ODsay request failed: %s status=%s', type(exc).__name__, status)
        raise ApiError(503, 'TRANSIT_PROVIDER_UNAVAILABLE', 'ODsay 연결 또는 키·권한을 확인해 주세요.', retryable=True) from None
    try:
        data = response.json()
        if 'error' in data:
            error = data['error']
            if isinstance(error, list):
                error = error[0]
            code = str(error.get('code'))
            if code in ('-98', '-99'):
                distance = distance_m(*origin, *destination)
                if distance <= 1000:
                    # Heuristic only: barriers and walkable roads are not verified.
                    meters = ceil(distance * 1.5)
                    minutes = max(1, ceil(meters / 60))
                    return TransitRoute(minutes * 60, meters, ({'mode': 'WALK',
                        'durationMinutes': minutes, 'distanceMeters': meters,
                        'estimateBasis': 'STRAIGHT_LINE_X_1_5', 'walkabilityStatus': 'UNCONFIRMED',
                        'fromCoordinates': origin, 'toCoordinates': destination},), 0)
                return None
            log.warning('ODsay error code=%s', code)
            raise ApiError(503, 'TRANSIT_PROVIDER_UNAVAILABLE', 'ODsay 오류 코드: ' + code)
        result = data['result']
        if result.get('searchType') in (1, 2):
            if local_only:
                return None
            return compose_intercity(session, origin, destination, result.get('path', []))
        routes = []
        for path in result.get('path', []):
            info = path['info']
            segments = []
            for sub in path['subPath']:
                mode = {1: 'SUBWAY', 2: 'BUS', 3: 'WALK'}.get(sub['trafficType'])
                if mode is None:
                    raise ValueError('Unsupported segment')
                segments.append({'mode': mode, 'durationMinutes': number(sub['sectionTime']),
                                 'distanceMeters': number(sub['distance']),
                                 'fromName': sub.get('startName'), 'toName': sub.get('endName'),
                                 'lines': [{k: lane[k] for k in ('busNo', 'name') if k in lane}
                                           for lane in sub.get('lane', [])]})
            if not segments:
                raise ValueError('Empty route')
            routes.append(TransitRoute(ceil(number(info['totalTime']) * 60),
                          ceil(number(info['trafficDistance']) + number(info['totalWalk'])),
                          tuple(segments), int(number(info['payment'])) if 'payment' in info else None))
        return min(routes, key=lambda r: r.duration_seconds) if routes else None
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        log.warning('ODsay invalid response: %s', type(exc).__name__)
        raise ApiError(503, 'TRANSIT_RESPONSE_INVALID', 'ODsay 경로 응답을 확인할 수 없습니다.') from None


def compose_intercity(session, origin, destination, paths):
    # MVP: inspect at most three candidates and use the first complete route.
    for path in paths[:3]:
        subs = path.get('subPath', [])
        if not subs or any(s.get('trafficType') not in (4, 5, 6) for s in subs):
            continue
        pieces = []
        previous = origin
        valid = True
        for sub in subs:
            start = (float(sub['startY']), float(sub['startX']))
            end = (float(sub['endY']), float(sub['endX']))
            for lat, lng in (start, end):
                if not isfinite(lat) or not isfinite(lng) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError('Invalid station coordinates')
            access = session.get_route(previous, start, local_only=True)
            if access is None:
                valid = False
                break
            pieces.extend(access.segments)
            pieces.append({'mode': 'TRANSFER_BUFFER', 'durationMinutes': 15, 'distanceMeters': 0,
                           'estimateBasis': 'PLANNING_ALLOWANCE', 'fromName': sub.get('startName')})
            pieces.append({'mode': {4: 'TRAIN', 5: 'EXPRESS_BUS', 6: 'INTERCITY_BUS'}[sub['trafficType']],
                           'durationMinutes': number(sub['sectionTime']), 'distanceMeters': number(sub['distance']),
                           'fromName': sub.get('startName'), 'toName': sub.get('endName'),
                           'fromCoordinates': start, 'toCoordinates': end})
            previous = end
        if not valid:
            continue
        egress = session.get_route(previous, destination, local_only=True)
        if egress is None:
            continue
        pieces.extend(egress.segments)
        # Fare stays unknown until access fares and intercity fare are reconciled.
        return TransitRoute(ceil(sum(s['durationMinutes'] for s in pieces) * 60),
                            ceil(sum(s['distanceMeters'] for s in pieces)), tuple(pieces), None)
    return None
