from __future__ import annotations

import math
import statistics

from .models import Candle


NAN = float("nan")


def is_valid(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def rolling_mean_prior(values: list[float], window: int, min_periods: int) -> list[float]:
    out = [NAN] * len(values)
    for i in range(len(values)):
        start = max(0, i - window)
        sample = values[start:i]
        if len(sample) >= min_periods:
            out[i] = sum(sample) / len(sample)
    return out


def rolling_median_prior(values: list[float], window: int, min_periods: int) -> list[float]:
    out = [NAN] * len(values)
    for i in range(len(values)):
        sample = values[max(0, i - window):i]
        if len(sample) >= min_periods:
            out[i] = statistics.median(sample)
    return out


def zscore_prior(values: list[float], window: int, min_periods: int) -> list[float]:
    """Population z-score against prior values; current value is excluded."""
    out = [NAN] * len(values)
    for i, value in enumerate(values):
        sample = values[max(0, i - window):i]
        if len(sample) < min_periods:
            continue
        mean = sum(sample) / len(sample)
        variance = sum((x - mean) ** 2 for x in sample) / len(sample)
        std = math.sqrt(variance)
        if std > 0:
            out[i] = (value - mean) / std
    return out


def wilder_atr(candles: list[Candle], period: int = 14) -> list[float]:
    tr: list[float] = []
    for i, bar in enumerate(candles):
        if i == 0:
            tr.append(bar.high - bar.low)
        else:
            prev_close = candles[i - 1].close
            tr.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
    out = [NAN] * len(candles)
    if len(tr) < period:
        return out
    out[period - 1] = sum(tr[:period]) / period
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def wilder_adx(candles: list[Candle], period: int = 14) -> list[float]:
    size = len(candles)
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for i in range(1, size):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )

    atr_sum = [NAN] * size
    plus_sum = [NAN] * size
    minus_sum = [NAN] * size
    if size <= period:
        return [NAN] * size
    atr_sum[period] = sum(tr[1:period + 1])
    plus_sum[period] = sum(plus_dm[1:period + 1])
    minus_sum[period] = sum(minus_dm[1:period + 1])
    dx = [NAN] * size
    for i in range(period, size):
        if i > period:
            atr_sum[i] = atr_sum[i - 1] - atr_sum[i - 1] / period + tr[i]
            plus_sum[i] = plus_sum[i - 1] - plus_sum[i - 1] / period + plus_dm[i]
            minus_sum[i] = minus_sum[i - 1] - minus_sum[i - 1] / period + minus_dm[i]
        if atr_sum[i] <= 0:
            continue
        plus_di = 100 * plus_sum[i] / atr_sum[i]
        minus_di = 100 * minus_sum[i] / atr_sum[i]
        denom = plus_di + minus_di
        if denom > 0:
            dx[i] = 100 * abs(plus_di - minus_di) / denom

    out = [NAN] * size
    first = 2 * period - 1
    initial = [x for x in dx[period:first + 1] if is_valid(x)]
    if len(initial) == period:
        out[first] = sum(initial) / period
        for i in range(first + 1, size):
            if is_valid(dx[i]):
                out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def obv(candles: list[Candle]) -> list[float]:
    out = [0.0] * len(candles)
    for i in range(1, len(candles)):
        direction = 1 if candles[i].close > candles[i - 1].close else -1 if candles[i].close < candles[i - 1].close else 0
        out[i] = out[i - 1] + direction * candles[i].volume
    return out


def normalized_obv_slope(candles: list[Candle], span: int = 16) -> list[float]:
    values = obv(candles)
    out = [NAN] * len(candles)
    for i in range(span, len(candles)):
        volumes = [b.volume for b in candles[i - span + 1:i + 1]]
        mean_volume = sum(volumes) / span
        if mean_volume > 0:
            out[i] = (values[i] - values[i - span]) / mean_volume
    return out


def enrich(candles: list[Candle]) -> dict[str, list[float]]:
    closes = [b.close for b in candles]
    counts = [float(b.number_trades) for b in candles]
    volumes = [b.volume for b in candles]
    atr = wilder_atr(candles, 14)
    atrp = [atr[i] / closes[i] if is_valid(atr[i]) and closes[i] else NAN for i in range(len(candles))]
    median_atrp = rolling_median_prior(atrp, 168, 72)
    compression = [atrp[i] / median_atrp[i] if is_valid(atrp[i]) and is_valid(median_atrp[i]) and median_atrp[i] else NAN for i in range(len(candles))]
    tr_ratio = []
    for i, bar in enumerate(candles):
        if i == 0 or not is_valid(atr[i]) or atr[i] <= 0:
            tr_ratio.append(NAN)
        else:
            true_range = max(bar.high - bar.low, abs(bar.high - candles[i - 1].close), abs(bar.low - candles[i - 1].close))
            tr_ratio.append(true_range / atr[i])
    return {
        "ema10": ema(closes, 10),
        "ema20": ema(closes, 20),
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "atr": atr,
        "adx": wilder_adx(candles, 14),
        "ntr_z_168": zscore_prior(counts, 168, 72),
        "ntr_z_672": zscore_prior(counts, 672, 288),
        "volume_mean_20": rolling_mean_prior(volumes, 20, 10),
        "count_mean_20": rolling_mean_prior(counts, 20, 10),
        "compression": compression,
        "tr_ratio": tr_ratio,
        "obv_slope16": normalized_obv_slope(candles, 16),
    }
