from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

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


def fetch_15m(start_ms: int, end_ms: int | None = None) -> list[Candle]:
    """Fetch closed USD-M Futures candles. Never falls back to another market."""
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
            if close_time >= now_ms:
                continue
            rows.append(Candle(
                open_time=int(item[0]), close_time=close_time,
                open=float(item[1]), high=float(item[2]), low=float(item[3]), close=float(item[4]),
                volume=float(item[5]), number_trades=int(item[8]), taker_buy_volume=float(item[9]),
            ))
        next_cursor = int(batch[-1][0]) + INTERVAL_MS
        if next_cursor <= cursor or len(batch) < 1500:
            break
        cursor = next_cursor

    unique = {bar.open_time: bar for bar in rows}
    candles = [unique[key] for key in sorted(unique)]
    if len(candles) < 800:
        raise DataUnavailable(f"Only {len(candles)} closed 15m candles were returned; at least 800 are required.")
    return candles


def resample(candles: list[Candle], hours: int) -> list[Candle]:
    bucket_ms = hours * 60 * 60 * 1000
    expected = hours * 4
    grouped: dict[int, list[Candle]] = defaultdict(list)
    for candle in candles:
        bucket = candle.open_time - candle.open_time % bucket_ms
        grouped[bucket].append(candle)

    result: list[Candle] = []
    for start in sorted(grouped):
        bars = sorted(grouped[start], key=lambda b: b.open_time)
        expected_times = [start + i * INTERVAL_MS for i in range(expected)]
        if len(bars) != expected or [b.open_time for b in bars] != expected_times:
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
        ))
    return result
