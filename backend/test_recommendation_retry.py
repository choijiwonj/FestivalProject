import httpx
import recommendations


def test_transient_failure_retries(monkeypatch):
    statuses = iter([503, 200])
    delays = []
    monkeypatch.setattr(recommendations.time, "sleep", delays.append)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(next(statuses)))
    assert recommendations.post_with_retry("https://example.test").status_code == 200
    assert delays == [1]


def test_auth_failure_does_not_retry(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(403))
    monkeypatch.setattr(recommendations.time, "sleep", lambda _: (_ for _ in ()).throw(AssertionError()))
    assert recommendations.post_with_retry("https://example.test").status_code == 403
