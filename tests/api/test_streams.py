"""The polling SSE streams share one budget per client address and in all (``api/streams.py``)."""

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from shimpaku.api import demo, streams
from shimpaku.api.app import create_app
from shimpaku.api.ratelimit import RateLimiter


def _status(c, path):
    with c.stream("GET", path) as r:
        return r.status_code


def _until(fn, s=10.0):
    t = time.time() + s
    while time.time() < t:
        if fn():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture()
def visitor():
    return TestClient(create_app(demo, auth_mode="local", public_view=True, limiter=RateLimiter({})))


def test_caps_count_per_address_and_in_all():
    caps = streams.StreamCaps(per_ip=2, total=3)
    a1, a2 = caps.acquire("a"), caps.acquire("a")
    with pytest.raises(HTTPException) as e:
        caps.acquire("a")
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "60"
    b1 = caps.acquire("b")
    with pytest.raises(HTTPException):
        caps.acquire("c")                      # the total is reached
    caps.acquire("c", counted=False)()         # the operator is never counted
    a1(); a1()                                 # releasing twice gives back one slot
    assert caps.open == {"a": 1, "b": 1}
    a2(); b1()
    assert caps.open == {}


def test_streams_share_one_budget_and_give_slots_back(visitor, monkeypatch):
    monkeypatch.setattr(streams, "PER_IP", 1)
    with visitor.stream("GET", "/api/stream/sle?site=campus") as r1:
        assert r1.status_code == 200
        # any other polling stream from the same address is refused while the first is open
        assert _status(visitor, "/api/stream/actions") == 429
        assert _status(visitor, "/api/live/stream/traffic") == 429
    _until(lambda: _status(visitor, "/api/stream/actions") == 200)
