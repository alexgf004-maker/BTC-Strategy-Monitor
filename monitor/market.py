from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .models import Candle


INTERVAL_MS = 15 * 60 * 1000
ENDPOINTS = (
    "https://fapi.binance.com/fapi/v1/klines",
    "https://fapi1.binance.com/fapi/v1/klines",
    "https://fapi2.binance.com/fapi/v1/klines",
    "https://fapi3.binance.com/fapi/v1/klines",
)


class DataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketFetch:
    candles: list[Candle]
    source: str
    warning: str | None = None


def _request(endpoint: str, start_ms: int, end_ms: int) -> list:
    params = urllib.parse.urlencode({
        "symbol": "BTCUSDT",
        "interval": "15m",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1500,
    })
    request = urllib.request.Request(
        f"{endpoint}?{params}",
        headers={"User-Agent": "BTC-Strategy-Monitor/1.0 paper-only"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_15m_realtime(start_ms: int, end_ms: int | None = None) -> list[Candle]:
    """Fetch closed USD-M Futures candles from the real-time API."""
    now_ms = int(time.time() * 1000)
    end_ms = min(end_ms or now_ms, now_ms)
    cursor = start_ms
    rows: list[Candle] = []
    endpoint_errors: list[str] = []
    selected_endpoint: str | None = None

    while cursor < end_ms:
        batch = None
        endpoints = (selected_endpoint,) if selected_endpoint else ENDPOINTS
        for endpoint in endpoints:
            try:
                batch = _request(endpoint, cursor, end_ms)
                selected_endpoint = endpoint
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                endpoint_errors.append(f"{endpoint}: {type(exc).__name__}")
        if batch is None and selected_endpoint:
            selected_endpoint = None
            continue
        if batch is None:
            raise DataUnavailable("No Binance USD-M Futures endpoint was reachable: " + "; ".join(endpoint_errors[-8:]))
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            close_time = int(item[6])
            rows.append(Candle(
                open_time=int(item[0]), close_time=close_time,
                open=float(item[1]), high=float(item[2]), low=float(item[3]), close=float(item[4]),
                volume=float(item[5]), number_trades=int(item[8]), taker_buy_volume=float(item[9]),
                is_closed=close_time < now_ms,
            ))
        next_cursor = int(batch[-1][0]) + INTERVAL_MS
        if next_cursor <= cursor or len(batch) < 1500:
            break
        cursor = next_cursor

    unique = {bar.open_time: bar for bar in rows}
    candles = [unique[key] for key in sorted(unique)]
    if len(candles) < 800:
        raise DataUnavailable(f"Only {len(candles)} 15m candles were returned; at least 800 are required.")
    return candles


def _normalize_timestamp(raw: str) -> int:
    value = int(raw)
    # Binance archive timestamps from 2025 onward may be microseconds.
    return value // 1000 if value > 100_000_000_000_000 else value


def _parse_archive(payload: bytes) -> list[Candle]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise DataUnavailable("Binance Vision archive contained no CSV file.")
        text = archive.read(names[0]).decode("utf-8")
    rows: list[Candle] = []
    for item in csv.reader(io.StringIO(text)):
        if not item or not item[0].strip().isdigit():
            continue
        rows.append(Candle(
            open_time=_normalize_timestamp(item[0]), close_time=_normalize_timestamp(item[6]),
            open=float(item[1]), high=float(item[2]), low=float(item[3]), close=float(item[4]),
            volume=float(item[5]), number_trades=int(item[8]), taker_buy_volume=float(item[9]),
        ))
    return rows


def _download_archive(url: str) -> list[Candle]:
    request = urllib.request.Request(url, headers={"User-Agent": "BTC-Strategy-Monitor/1.0 paper-only"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return _parse_archive(response.read())


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def _daily_url(value: date) -> str:
    stamp = value.isoformat()
    return f"https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/15m/BTCUSDT-15m-{stamp}.zip"


def _monthly_url(value: date) -> str:
    stamp = value.strftime("%Y-%m")
    return f"https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-{stamp}.zip"


def fetch_15m_archive(start_ms: int, end_ms: int | None = None) -> list[Candle]:
    """Fetch exact official Futures candles from Binance Vision.

    The archive is delayed and is used only when GitHub's US runner cannot reach
    the real-time Futures API. It never substitutes spot or another exchange.
    """
    now = datetime.now(timezone.utc)
    requested_end = datetime.fromtimestamp((end_ms or int(now.timestamp() * 1000)) / 1000, tz=timezone.utc)
    last_available_day = min(requested_end.date(), now.date() - timedelta(days=1))
    start_day = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
    if start_day > last_available_day:
        raise DataUnavailable("No completed Binance Vision daily archive is available yet.")

    rows: list[Candle] = []
    cursor = start_day
    current_month = _month_start(last_available_day)
    while _month_start(cursor) < current_month:
        month = _month_start(cursor)
        next_month = _next_month(month)
        try:
            rows.extend(_download_archive(_monthly_url(month)))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, zipfile.BadZipFile, DataUnavailable):
            day = max(cursor, month)
            while day < next_month and day <= last_available_day:
                rows.extend(_download_archive(_daily_url(day)))
                day += timedelta(days=1)
        cursor = next_month

    cursor = max(cursor, current_month)
    while cursor <= last_available_day:
        try:
            rows.extend(_download_archive(_daily_url(cursor)))
        except urllib.error.HTTPError as exc:
            # Binance Vision commonly publishes yesterday's file later in UTC.
            # Missing trailing days are acceptable; an internal candle gap is not.
            if exc.code != 404:
                raise
        cursor += timedelta(days=1)

    limit_end = int((datetime.combine(last_available_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)).timestamp() * 1000)
    unique = {bar.open_time: bar for bar in rows if start_ms <= bar.open_time < limit_end}
    candles = [unique[key] for key in sorted(unique)]
    if len(candles) < 800:
        raise DataUnavailable(f"Only {len(candles)} archived candles were returned; at least 800 are required.")
    for previous, current in zip(candles, candles[1:]):
        if current.open_time - previous.open_time != INTERVAL_MS:
            raise DataUnavailable(f"Archived series has an internal gap after {previous.open_time}.")
    return candles


def fetch_15m(start_ms: int, end_ms: int | None = None) -> MarketFetch:
    """Prefer real time and fail over only to Binance's official delayed archive."""
    if os.getenv("BTC_DATA_MODE", "auto").lower() == "archive":
        return MarketFetch(
            fetch_15m_archive(start_ms, end_ms),
            "binance_vision_official_delayed",
            "Archive mode was explicitly selected.",
        )
    try:
        return MarketFetch(fetch_15m_realtime(start_ms, end_ms), "binance_futures_realtime")
    except DataUnavailable as realtime_error:
        try:
            candles = fetch_15m_archive(start_ms, end_ms)
        except Exception as archive_error:
            raise DataUnavailable(f"Realtime unavailable ({realtime_error}); official archive also failed ({archive_error})") from archive_error
        return MarketFetch(candles, "binance_vision_official_delayed", str(realtime_error))


def resample(candles: list[Candle], hours: int, include_partial: bool = False) -> list[Candle]:
    bucket_ms = hours * 60 * 60 * 1000
    expected = hours * 4
    grouped: dict[int, list[Candle]] = defaultdict(list)
    for candle in candles:
        bucket = candle.open_time - candle.open_time % bucket_ms
        grouped[bucket].append(candle)

    result: list[Candle] = []
    starts = sorted(grouped)
    for start in starts:
        bars = sorted(grouped[start], key=lambda b: b.open_time)
        expected_times = [start + i * INTERVAL_MS for i in range(expected)]
        actual_times = [b.open_time for b in bars]
        complete = len(bars) == expected and actual_times == expected_times and all(b.is_closed for b in bars)
        partial_tail = (
            include_partial
            and start == starts[-1]
            and 0 < len(bars) <= expected
            and actual_times == expected_times[:len(bars)]
            and not complete
        )
        if not complete and not partial_tail:
            continue
        result.append(Candle(
            open_time=start,
            close_time=bars[-1].close_time,
            open=bars[0].open,
            high=max(b.high for b in bars),
            low=min(b.low for b in bars),
            close=bars[-1].close,
            volume=sum(b.volume for b in bars),
            number_trades=sum(b.number_trades for b in bars),
            taker_buy_volume=sum(b.taker_buy_volume for b in bars),
            is_closed=complete,
        ))
    return result
