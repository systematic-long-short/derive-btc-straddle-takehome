from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import derivebench.runner as runner_module
from derivebench.derive import DeriveRESTClient, DeriveRESTFeed
from derivebench.runner import collect_live_ticks

from tests.conftest import FIXTURES


def fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "derive_public_responses.json").read_text())


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


def test_rest_client_retries_transient_500_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    data = fixture()
    requests: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if len(requests) == 1:
            return httpx.Response(500, json={"error": "temporary server failure"}, request=request)
        return httpx.Response(200, json=data["currencies"], request=request)

    monkeypatch.setattr("derivebench.derive.time.sleep", sleeps.append)
    monkeypatch.setattr("derivebench.derive.random.uniform", lambda _low, _high: 0.0)
    client = DeriveRESTClient(
        transport=httpx.MockTransport(handler),
        max_attempts=3,
        retry_base_delay=0.25,
        retry_jitter=0.0,
    )

    assert client.get_btc_spot() == 80000.0
    assert requests == ["/public/get_all_currencies", "/public/get_all_currencies"]
    assert sleeps == [0.25]


def test_rest_client_retries_timeout_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    data = fixture()
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("read timed out", request=request)
        return httpx.Response(200, json=data["currencies"], request=request)

    monkeypatch.setattr("derivebench.derive.time.sleep", lambda _seconds: None)
    client = DeriveRESTClient(
        transport=httpx.MockTransport(handler),
        max_attempts=2,
        retry_base_delay=0.0,
    )

    assert client.get_btc_spot() == 80000.0
    assert attempts == 2


def _feed_transport(*, fail_first_spot: bool = False, always_fail_spot: bool = False) -> httpx.MockTransport:
    data = fixture()
    spot_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal spot_calls
        method = request.url.path.rstrip("/").split("/")[-1]
        if method == "get_all_instruments":
            return httpx.Response(200, json=data["instruments"], request=request)
        if method == "get_all_currencies":
            spot_calls += 1
            if always_fail_spot or (fail_first_spot and spot_calls == 1):
                return httpx.Response(500, json={"error": "temporary spot failure"}, request=request)
            return httpx.Response(200, json=data["currencies"], request=request)
        if method == "get_tickers":
            return httpx.Response(200, json=data["tickers"], request=request)
        return httpx.Response(404, json={"error": f"unexpected method {method}"}, request=request)

    return httpx.MockTransport(handler)


def test_collect_live_ticks_skips_isolated_poll_failure_without_fabricating_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    monkeypatch.setattr(runner_module.time, "time", clock.time)
    monkeypatch.setattr(runner_module.time, "sleep", clock.sleep)
    client = DeriveRESTClient(
        transport=_feed_transport(fail_first_spot=True),
        max_attempts=1,
        retry_base_delay=0.0,
    )
    health: dict[str, Any] = {}

    ticks = collect_live_ticks(
        duration=3.0,
        poll_interval=1.0,
        max_consecutive_failures=3,
        feed=DeriveRESTFeed(client=client),
        health=health,
    )

    assert len(ticks) == 4
    assert {tick.feed_source for tick in ticks} == {"derive_rest"}
    assert health["poll_attempts"] == 5
    assert health["poll_success_count"] == 4
    assert health["poll_error_count"] == 1
    assert health["max_consecutive_poll_failures"] == 1
    assert health["poll_errors"][0]["type"] == "RuntimeError"


def test_collect_live_ticks_raises_after_too_many_consecutive_poll_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    monkeypatch.setattr(runner_module.time, "time", clock.time)
    monkeypatch.setattr(runner_module.time, "sleep", clock.sleep)
    client = DeriveRESTClient(
        transport=_feed_transport(always_fail_spot=True),
        max_attempts=1,
        retry_base_delay=0.0,
    )
    health: dict[str, Any] = {}

    with pytest.raises(RuntimeError, match="2 consecutive"):
        collect_live_ticks(
            duration=10.0,
            poll_interval=1.0,
            max_consecutive_failures=2,
            feed=DeriveRESTFeed(client=client),
            health=health,
        )

    assert health["poll_success_count"] == 0
    assert health["poll_error_count"] == 2
    assert health["max_consecutive_poll_failures"] == 2
