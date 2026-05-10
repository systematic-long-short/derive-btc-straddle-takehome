"""Derive public REST parsing, package selection, and replay helpers."""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx

from derivebench.model import Tick

DERIVE_PUBLIC_URL = "https://api.lyra.finance/public"
TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TRANSIENT_API_ERROR_CODES = {408, 429, 500, 502, 503, 504, -32000, -32002, -32603}
TRANSIENT_API_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporary",
    "temporarily",
    "rate limit",
    "too many",
    "server",
    "internal",
    "unavailable",
    "try again",
    "gateway",
    "overloaded",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


class DeriveTransientError(RuntimeError):
    """A Derive public API failure that is worth retrying briefly."""


def _is_transient_status(status_code: int) -> bool:
    return status_code in TRANSIENT_HTTP_STATUS_CODES or status_code >= 500


def _api_error_text(error: Any) -> str:
    if isinstance(error, Mapping):
        for key in ("message", "reason", "detail", "error"):
            value = error.get(key)
            if value:
                return str(value)
        return str(dict(error))
    return str(error)


def _is_transient_api_error(error: Any) -> bool:
    if isinstance(error, Mapping):
        code = error.get("code") or error.get("status") or error.get("status_code")
        try:
            if int(code) in TRANSIENT_API_ERROR_CODES:
                return True
        except (TypeError, ValueError):
            pass
    text = _api_error_text(error).lower()
    return any(marker in text for marker in TRANSIENT_API_ERROR_MARKERS)


@dataclass(frozen=True, slots=True)
class Instrument:
    name: str
    is_active: bool
    expiry_ts: float
    strike: float
    option_type: str
    base_currency: str = "BTC"
    quote_currency: str = "USDC"
    minimum_amount: float = 0.0
    amount_step: float = 0.0
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0
    base_fee: float = 0.0

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "Instrument":
        details = raw.get("option_details") or {}
        option_type = str(details.get("option_type") or raw.get("option_type") or "").upper()
        if option_type in {"CALL", "C"}:
            option_type = "C"
        elif option_type in {"PUT", "P"}:
            option_type = "P"
        return cls(
            name=str(raw.get("instrument_name") or raw.get("name") or ""),
            is_active=bool(raw.get("is_active", True)),
            expiry_ts=_f(details.get("expiry") or raw.get("expiry")),
            strike=_f(details.get("strike") or raw.get("strike")),
            option_type=option_type,
            base_currency=str(raw.get("base_currency") or "BTC"),
            quote_currency=str(raw.get("quote_currency") or "USDC"),
            minimum_amount=_f(raw.get("minimum_amount")),
            amount_step=_f(raw.get("amount_step")),
            maker_fee_rate=_f(raw.get("maker_fee_rate")),
            taker_fee_rate=_f(raw.get("taker_fee_rate")),
            base_fee=_f(raw.get("base_fee")),
        )

    @property
    def expiry_date(self) -> str:
        return datetime.fromtimestamp(self.expiry_ts, tz=timezone.utc).strftime("%Y%m%d")


@dataclass(frozen=True, slots=True)
class Ticker:
    instrument_name: str
    ts_ms: float
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    index: float
    mark: float
    implied_vol: float = 0.0
    raw: Mapping[str, Any] | None = None

    @classmethod
    def from_api(cls, name: str, raw: Mapping[str, Any]) -> "Ticker":
        pricing = raw.get("option_pricing") or {}
        bid = _f(raw.get("b"))
        ask = _f(raw.get("a"))
        mark = _f(raw.get("M"), default=(bid + ask) / 2.0 if bid and ask else 0.0)
        return cls(
            instrument_name=name,
            ts_ms=_f(raw.get("t")),
            bid=bid,
            ask=ask,
            bid_size=_f(raw.get("B")),
            ask_size=_f(raw.get("A")),
            index=_f(raw.get("I")),
            mark=mark,
            implied_vol=_f(pricing.get("i")),
            raw=dict(raw),
        )

    @property
    def mid(self) -> float:
        if self.bid > 0.0 and self.ask > 0.0:
            return (self.bid + self.ask) / 2.0
        return self.mark

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        if mid <= 0.0:
            return math.inf
        return max(0.0, self.ask - self.bid) / mid


@dataclass(frozen=True, slots=True)
class PackageQuote:
    call: Instrument
    put: Instrument
    call_ticker: Ticker
    put_ticker: Ticker
    reason: str

    @property
    def package_id(self) -> str:
        return f"{self.call.expiry_date}:{self.call.name}|{self.put.name}"

    @property
    def bid(self) -> float:
        return self.call_ticker.bid + self.put_ticker.bid

    @property
    def ask(self) -> float:
        return self.call_ticker.ask + self.put_ticker.ask

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def mark(self) -> float:
        return self.call_ticker.mark + self.put_ticker.mark


def parse_instruments(response: Mapping[str, Any]) -> list[Instrument]:
    items = (response.get("result") or {}).get("instruments") or []
    return [Instrument.from_api(item) for item in items if isinstance(item, Mapping)]


def parse_tickers(response: Mapping[str, Any]) -> dict[str, Ticker]:
    raw = (response.get("result") or {}).get("tickers") or {}
    if isinstance(raw, list):
        pairs = [(str(item.get("instrument_name") or item.get("instrument") or ""), item) for item in raw]
    else:
        pairs = list(raw.items())
    return {
        name: Ticker.from_api(name, item)
        for name, item in pairs
        if name and isinstance(item, Mapping)
    }


def parse_btc_spot(response: Mapping[str, Any]) -> float:
    result = response.get("result") or {}
    currencies = result.get("currencies") if isinstance(result, Mapping) else result
    currencies = currencies or result
    if isinstance(currencies, Mapping):
        btc = currencies.get("BTC") or currencies.get("btc") or currencies
    else:
        btc = next(
            (
                c
                for c in currencies
                if isinstance(c, Mapping)
                and str(c.get("currency") or c.get("currency_name") or "").upper() == "BTC"
            ),
            {},
        )
    return _f((btc or {}).get("spot_price") or (btc or {}).get("spot"))


def _is_liquid(ticker: Ticker, *, min_size: float, max_spread_pct: float) -> bool:
    return (
        ticker.bid > 0.0
        and ticker.ask >= ticker.bid
        and ticker.bid_size >= min_size
        and ticker.ask_size >= min_size
        and ticker.spread_pct <= max_spread_pct
    )


def _candidate_pairs(
    instruments: Iterable[Instrument],
    tickers: Mapping[str, Ticker],
    spot: float,
    *,
    min_size: float,
    max_spread_pct: float,
) -> Iterable[PackageQuote]:
    active = [i for i in instruments if i.is_active and i.base_currency == "BTC" and i.name in tickers]
    expiries = sorted({i.expiry_ts for i in active if i.expiry_ts > 0.0})
    for expiry in expiries:
        calls = sorted(
            [i for i in active if i.expiry_ts == expiry and i.option_type == "C" and i.strike > spot],
            key=lambda i: i.strike,
        )
        puts = sorted(
            [i for i in active if i.expiry_ts == expiry and i.option_type == "P" and i.strike < spot],
            key=lambda i: i.strike,
            reverse=True,
        )
        liquid_calls = [i for i in calls if _is_liquid(tickers[i.name], min_size=min_size, max_spread_pct=max_spread_pct)]
        liquid_puts = [i for i in puts if _is_liquid(tickers[i.name], min_size=min_size, max_spread_pct=max_spread_pct)]
        if liquid_calls and liquid_puts:
            yield PackageQuote(liquid_calls[0], liquid_puts[0], tickers[liquid_calls[0].name], tickers[liquid_puts[0].name], "nearest_liquid_same_expiry")


def select_otm_package(
    instruments: Iterable[Instrument],
    tickers: Mapping[str, Ticker],
    spot: float,
    *,
    min_size: float = 0.001,
    max_spread_pct: float = 0.35,
) -> PackageQuote:
    for quote in _candidate_pairs(
        instruments,
        tickers,
        spot,
        min_size=min_size,
        max_spread_pct=max_spread_pct,
    ):
        return quote
    raise ValueError(
        "no liquid BTC OTM call/put package available from Derive public data "
        f"(min_size={min_size}, max_spread_pct={max_spread_pct})"
    )


def tick_from_package(
    package: PackageQuote,
    *,
    spot: float,
    ts: float | None = None,
    btc_recent: Iterable[float] = (),
    package_mid_recent: Iterable[float] = (),
    feed_source: str = "derive_rest",
) -> Tick:
    now = float(ts if ts is not None else time.time())
    call = package.call_ticker
    put = package.put_ticker
    exchange_ts = max(call.ts_ms, put.ts_ms) / 1000.0 if max(call.ts_ms, put.ts_ms) > 0 else now
    spread = max(0.0, package.ask - package.bid)
    mid = package.mid
    return Tick(
        ts=now,
        exchange_ts=exchange_ts,
        package_id=package.package_id,
        feed_source=feed_source,
        expiry_ts=package.call.expiry_ts,
        expiry_date=package.call.expiry_date,
        time_to_expiry=max(0.0, package.call.expiry_ts - now),
        btc_spot=spot,
        btc_index=call.index or put.index or spot,
        call_name=package.call.name,
        put_name=package.put.name,
        call_strike=package.call.strike,
        put_strike=package.put.strike,
        call_bid=call.bid,
        call_ask=call.ask,
        call_mid=call.mid,
        call_mark=call.mark,
        put_bid=put.bid,
        put_ask=put.ask,
        put_mid=put.mid,
        put_mark=put.mark,
        package_bid=package.bid,
        package_ask=package.ask,
        package_mid=mid,
        package_mark=package.mark,
        package_spread=spread,
        package_spread_pct=(spread / mid) if mid > 0.0 else math.inf,
        call_bid_size=call.bid_size,
        call_ask_size=call.ask_size,
        put_bid_size=put.bid_size,
        put_ask_size=put.ask_size,
        min_leg_size=min(call.bid_size, call.ask_size, put.bid_size, put.ask_size),
        liquidity_ok=package.reason == "nearest_liquid_same_expiry",
        btc_recent=tuple(btc_recent),
        package_mid_recent=tuple(package_mid_recent),
        selection_reason=package.reason,
    )


def tick_to_dict(tick: Tick) -> dict[str, Any]:
    return asdict(tick)


def tick_from_dict(raw: Mapping[str, Any]) -> Tick:
    fields = Tick.__dataclass_fields__
    kwargs = {name: raw[name] for name in fields if name in raw}
    return Tick(**kwargs)


def load_replay_ticks(path: Path | str, *, duration: float | None = None) -> list[Tick]:
    payload = json.loads(Path(path).read_text())
    ticks = [tick_from_dict(item) for item in payload.get("ticks", [])]
    if duration is not None and ticks:
        start = ticks[0].ts
        ticks = [tick for tick in ticks if tick.ts - start <= duration]
    if not ticks:
        raise ValueError(f"replay file has no ticks: {path}")
    return ticks


class DeriveRESTClient:
    def __init__(
        self,
        base_url: str = DERIVE_PUBLIC_URL,
        timeout: float = 10.0,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        retry_base_delay: float = 0.25,
        retry_max_delay: float = 4.0,
        retry_jitter: float = 0.2,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("pass either client or transport, not both")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout, transport=transport)
        self.max_attempts = max_attempts
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.retry_max_delay = max(0.0, retry_max_delay)
        self.retry_jitter = max(0.0, retry_jitter)

    def _retry_delay(self, attempt: int) -> float:
        base = min(self.retry_max_delay, self.retry_base_delay * (2 ** max(0, attempt - 1)))
        if base <= 0.0 or self.retry_jitter <= 0.0:
            return base
        return base + random.uniform(0.0, base * self.retry_jitter)

    def post(self, method: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.post(f"{self.base_url}/{method}", json=dict(payload))
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if _is_transient_status(status_code):
                        raise DeriveTransientError(f"Derive {method} HTTP {status_code}") from exc
                    raise
                try:
                    raw = response.json()
                except ValueError as exc:
                    raise DeriveTransientError(f"Derive {method} returned invalid JSON") from exc
                if not isinstance(raw, Mapping):
                    raise DeriveTransientError(f"Derive {method} returned non-object JSON")
                data = dict(raw)
                error = data.get("error")
                if error:
                    if _is_transient_api_error(error):
                        raise DeriveTransientError(f"Derive {method} temporary API error: {_api_error_text(error)}")
                    raise RuntimeError(f"Derive {method} error: {error}")
                return data
            except (DeriveTransientError, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise RuntimeError(f"Derive {method} failed after {attempt} attempts: {exc}") from exc
                delay = self._retry_delay(attempt)
                if delay > 0.0:
                    time.sleep(delay)
        raise RuntimeError(f"Derive {method} failed after {self.max_attempts} attempts: {last_error}")

    def get_all_instruments(self, *, currency: str = "BTC", instrument_type: str = "option") -> list[Instrument]:
        instruments: list[Instrument] = []
        page = 1
        while True:
            data = self.post(
                "get_all_instruments",
                {
                    "expired": False,
                    "instrument_type": instrument_type,
                    "currency": currency,
                    "page": page,
                    "page_size": 250,
                },
            )
            batch = parse_instruments(data)
            instruments.extend(batch)
            result = data.get("result") or {}
            if not batch or len(batch) < 250 or not result.get("pagination"):
                break
            page += 1
            if page > 20:
                break
        return instruments

    def get_btc_spot(self) -> float:
        return parse_btc_spot(self.post("get_all_currencies", {}))

    def get_tickers(self, expiry_date: str, *, currency: str = "BTC") -> dict[str, Ticker]:
        return parse_tickers(
            self.post(
                "get_tickers",
                {"currency": currency, "instrument_type": "option", "expiry_date": expiry_date},
            )
        )


class DeriveRESTFeed:
    def __init__(
        self,
        client: DeriveRESTClient | None = None,
        *,
        min_size: float = 0.001,
        max_spread_pct: float = 0.35,
    ) -> None:
        self.client = client or DeriveRESTClient()
        self.min_size = min_size
        self.max_spread_pct = max_spread_pct
        self._instruments: list[Instrument] = []
        self._btc_recent: list[float] = []
        self._package_recent: list[float] = []

    def refresh_instruments(self) -> None:
        self._instruments = self.client.get_all_instruments()

    def poll(self) -> Tick:
        if not self._instruments:
            self.refresh_instruments()
        spot = self.client.get_btc_spot()
        expiries = sorted({i.expiry_date for i in self._instruments if i.is_active})
        last_error: Exception | None = None
        for expiry in expiries[:8]:
            try:
                tickers = self.client.get_tickers(expiry)
                package = select_otm_package(
                    self._instruments,
                    tickers,
                    spot,
                    min_size=self.min_size,
                    max_spread_pct=self.max_spread_pct,
                )
                tick = tick_from_package(
                    package,
                    spot=spot,
                    btc_recent=self._btc_recent[-120:],
                    package_mid_recent=self._package_recent[-120:],
                )
                self._btc_recent.append(tick.btc_spot)
                self._package_recent.append(tick.package_mid)
                return tick
            except Exception as exc:  # try the next active expiry
                last_error = exc
        raise RuntimeError(f"could not build liquid Derive BTC OTM package: {last_error}")
