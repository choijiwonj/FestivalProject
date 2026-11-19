import httpx
import pytest
import transit
from core import ApiError
from transit import get_transit_route


def test_intercity_connects_access_transfer_and_egress(monkeypatch):
    calls = []
    class FakeSession:
      def get_route(self, start, end, local_only=False):
        calls.append((start, end, local_only))
        return transit.TransitRoute(300, 400, ({'mode': 'WALK', 'durationMinutes': 5, 'distanceMeters': 400},), 0)
    path = {'subPath': [{'trafficType': 4, 'startY': 36, 'startX': 127, 'endY': 37, 'endX': 127,
                        'sectionTime': 60, 'distance': 100000}]}
    route = transit.compose_intercity(FakeSession(), (35,127), (38,127), [path])
    assert route.duration_minutes == 85
    assert [s['mode'] for s in route.segments] == ['WALK','TRANSFER_BUFFER','TRAIN','WALK']
    assert len(calls) == 2 and all(c[2] for c in calls)
    class MissingSession:
      def get_route(self, *args, **kwargs): return None
    assert transit.compose_intercity(MissingSession(), (35,127), (38,127), [path]) is None


def test_plan_session_caches_rounded_coordinates(monkeypatch):
    calls = []
    body = {'result': {'searchType': 0, 'path': [{'info': {'totalTime': 5, 'trafficDistance': 100, 'totalWalk': 0, 'payment': 0},
            'subPath': [{'trafficType': 3, 'sectionTime': 5, 'distance': 100}]}]}}
    def get(*args, **kwargs):
        calls.append(True)
        return httpx.Response(200, json=body, request=httpx.Request('GET', 'https://example.test'))
    monkeypatch.setattr(httpx, 'get', get)
    session = transit.TransitRouteSession('secret')
    first = session.get_route((37.000001,127.000001), (37.1,127.1))
    cached = session.get_route((37.000002,127.000002), (37.1,127.1))
    second = session.get_route((38,127), (38.1,127.1))
    assert first is cached and second is not None
    assert len(calls) == session.call_count == 2


def test_transit_segments_and_unconfirmed_times(monkeypatch):
    body = {'result': {'searchType': 0, 'path': [{'info': {'totalTime': 25, 'trafficDistance': 4000, 'totalWalk': 300, 'payment': 1500},
            'subPath': [{'trafficType': 3, 'sectionTime': 5, 'distance': 300},
                        {'trafficType': 2, 'sectionTime': 20, 'distance': 4000, 'startName': 'A', 'endName': 'B', 'lane': [{'busNo': '100'}]}]}]}}
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(200, json=body, request=httpx.Request('GET', 'https://example.test')))
    route = get_transit_route('secret', (37,127), (37.1,127.1))
    leg = route.as_leg('a','b')
    assert leg['mode'] == 'PUBLIC_TRANSIT' and leg['durationMinutes'] == 25
    assert leg['distanceMeters'] == 4300 and leg['serviceTimeStatus'] == 'UNCONFIRMED'
    assert [s['mode'] for s in leg['segments']] == ['WALK','BUS']


def test_provider_error_does_not_leak_key(monkeypatch):
    def fail(*a, **k):
        raise httpx.ConnectError('https://example.test?apiKey=secret')
    monkeypatch.setattr(httpx, 'get', fail)
    with pytest.raises(ApiError) as error:
        get_transit_route('secret', (37,127), (37.1,127.1))
    assert 'secret' not in error.value.message
