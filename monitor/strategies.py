from __future__ import annotations

import math
from collections import defaultdict

from .indicators import enrich, is_valid
from .models import Candle, CandidateTrade


FEE_ROUNDTRIP = 0.0024
FORWARD_START_MS = 1_788_220_800_000  # 2026-09-01T00:00:00Z


def _telemetry(candles: list[Candle], feat: dict[str, list[float]], i: int) -> tuple[float | None, float | None, float | None]:
    bar = candles[i]
    vmean = feat["volume_mean_20"][i]
    cmean = feat["count_mean_20"][i]
    volume_ratio = bar.volume / vmean if is_valid(vmean) and vmean > 0 else None
    count_ratio = bar.number_trades / cmean if is_valid(cmean) and cmean > 0 else None
    taker_share = bar.taker_buy_volume / bar.volume if bar.volume > 0 else None
    return volume_ratio, count_ratio, taker_share


def _r_multiple(side: str, entry: float, exit_price: float, risk_distance: float) -> float:
    direction = 1.0 if side == "long" else -1.0
    return (direction * (exit_price - entry) - FEE_ROUNDTRIP * entry) / risk_distance


def _long_exit(
    candles: list[Candle], entry_i: int, stop: float, target: float | None,
    last_i: int, checkpoint_i: int | None = None, checkpoint_entry: float | None = None,
) -> tuple[int | None, float | None, str]:
    through = min(last_i, len(candles) - 1)
    for i in range(entry_i, through + 1):
        bar = candles[i]
        if not bar.is_closed:
            return None, None, "open"
        if bar.open <= stop:
            return i, bar.open, "gap_stop"
        if target is not None and bar.open >= target:
            return i, bar.open, "gap_target"
        if bar.low <= stop:
            return i, stop, "stop"
        if target is not None and bar.high >= target:
            return i, target, "target"
        if checkpoint_i is not None and i == checkpoint_i and bar.close < (checkpoint_entry or 0):
            return i, bar.close, "checkpoint"
        if i == last_i:
            return i, bar.close, "time"
    return None, None, "open"


def _short_exit(candles: list[Candle], entry_i: int, stop: float, target: float, last_i: int) -> tuple[int | None, float | None, str]:
    through = min(last_i, len(candles) - 1)
    for i in range(entry_i, through + 1):
        bar = candles[i]
        if not bar.is_closed:
            return None, None, "open"
        if bar.open >= stop:
            return i, bar.open, "gap_stop"
        if bar.open <= target:
            return i, bar.open, "gap_target"
        if bar.high >= stop:
            return i, stop, "stop"
        if bar.low <= target:
            return i, target, "target"
        if i == last_i:
            return i, bar.close, "time"
    return None, None, "open"


def support_touch_events(candles: list[Candle], feat: dict[str, list[float]]) -> dict[int, int]:
    """Return the frozen, causal 4H support-interaction episodes.

    A strict 3-left/3-right pivot becomes available only after the third
    right-hand bar has closed.  The pivot establishes the level but is not an
    interaction.  Touches on concurrent levels collapse to the greatest touch
    number, and a level is exhausted after its fourth interaction.  The fourth
    interaction still participates in that timestamp's collapse; this is what
    keeps a simultaneous touch-3/touch-4 event from being misclassified as a
    qualifying B context.
    """
    levels: list[dict] = []
    events: dict[int, int] = {}
    for i, bar in enumerate(candles):
        if not bar.is_closed:
            break
        current_atr = feat["atr"][i]
        if not is_valid(current_atr):
            continue

        # Existing, already available levels interact with the current bar.
        touches_now: list[int] = []
        for level in levels:
            if not level["active"]:
                continue
            if bar.close < level["price"] - 0.35 * current_atr:
                level["active"] = False
                continue
            enters_zone = bar.low <= level["price"] + 0.25 * current_atr and bar.high >= level["price"] - 0.25 * current_atr
            if enters_zone and i - level["last_touch_i"] >= 3:
                level["touch_n"] += 1
                level["last_touch_i"] = i
                touches_now.append(level["touch_n"])
                if level["touch_n"] >= 4:
                    level["active"] = False
        if touches_now:
            # Binance candles close at xx:59:59.999; the event is knowable at
            # the following boundary, which is also B's decision-clock label.
            events[bar.close_time + 1] = max(touches_now)

        # Strict 3-left/3-right pivot confirmed causally at the close of i.
        pivot_i = i - 3
        if pivot_i >= 3:
            pivot_low = candles[pivot_i].low
            neighborhood = [candles[j].low for j in range(pivot_i - 3, pivot_i + 4) if j != pivot_i]
            if all(pivot_low < other for other in neighborhood):
                levels.append({"price": pivot_low, "touch_n": 0, "last_touch_i": pivot_i, "active": True})
    return events


def _has_sr_context(decision_dt: int, events: dict[int, int]) -> bool:
    window_start = decision_dt - 12 * 60 * 60 * 1000
    return any(window_start <= event_dt <= decision_dt and touch_n in (2, 3) for event_dt, touch_n in events.items())


def generate_a(candles: list[Candle], min_entry_dt: int = FORWARD_START_MS) -> list[CandidateTrade]:
    feat = enrich(candles)
    trades: list[CandidateTrade] = []
    # The 168-bar activity feature is valid after 72 prior observations.  The
    # EMA200 is intentionally allowed to warm up from its causal seed, matching
    # the authoritative PBME tradebook from the beginning of the dataset.
    i = 72
    while i < len(candles) - 1:
        bar = candles[i]
        ret3 = bar.close / candles[i - 3].close - 1
        signal = (
            ret3 > 0.015 and is_valid(feat["ntr_z_168"][i]) and feat["ntr_z_168"][i] > 3.9362322227
            and bar.close > feat["ema200"][i] and bar.close > bar.open
        )
        if not signal:
            i += 1
            continue
        entry_i = i + 1
        entry = candles[entry_i].open
        atr = feat["atr"][i]
        if not is_valid(atr) or atr <= 0:
            i += 1
            continue
        stop = entry - 1.5 * atr
        exit_i, exit_price, reason = _long_exit(candles, entry_i, stop, None, entry_i + 59, entry_i + 5, entry)
        vr, cr, ts = _telemetry(candles, feat, i)
        trade = CandidateTrade("A", "long", i, entry_i, exit_i, bar.close_time, candles[entry_i].open_time,
                               candles[exit_i].close_time if exit_i is not None else None, entry, exit_price, stop, None,
                               entry - stop, _r_multiple("long", entry, exit_price, entry - stop) if exit_price is not None else None,
                               reason, False, vr, cr, ts, candles[-1].close)
        if trade.entry_dt >= min_entry_dt:
            trades.append(trade)
        if exit_i is None:
            break
        i = exit_i + 1
    return trades


def generate_b(candles: list[Candle], min_entry_dt: int = FORWARD_START_MS) -> list[CandidateTrade]:
    feat = enrich(candles)
    sr_events = support_touch_events(candles, feat)
    trades: list[CandidateTrade] = []
    i = 50
    while i < len(candles) - 1:
        bar = candles[i]
        prev = candles[i - 1]
        needed = [feat["atr"][i], feat["atr"][i - 1], feat["ema10"][i], feat["ema10"][i - 1]]
        signal = (
            all(is_valid(x) for x in needed)
            and bar.close > feat["ema50"][i] and feat["ema20"][i] > feat["ema50"][i]
            and prev.low < feat["ema10"][i - 1] - 0.25 * feat["atr"][i - 1]
            and bar.close > feat["ema10"][i] and bar.close > bar.open
        )
        if not signal:
            i += 1
            continue
        entry_i = i + 1
        entry = candles[entry_i].open
        structural_low = min(candles[j].low for j in range(max(0, i - 3), i + 1)) - 0.25 * feat["atr"][i]
        d_struct = (entry - structural_low) / feat["atr"][i]
        # Exact training-only Q60 cutoff recorded by the research audit.  The
        # frozen JSON rounds this value for display, but the tradebook was
        # generated with the full-precision threshold.
        if d_struct > 1.702619084409112:
            i += 1
            continue
        stop = entry - 2.5 * feat["atr"][i]
        target = entry + 3.75 * feat["atr"][i]
        exit_i, exit_price, reason = _long_exit(candles, entry_i, stop, target, entry_i + 29)
        vr, cr, ts = _telemetry(candles, feat, i)
        decision_dt = candles[entry_i].open_time
        context = _has_sr_context(decision_dt, sr_events)
        trade = CandidateTrade("B", "long", i, entry_i, exit_i, bar.close_time, decision_dt,
                               candles[exit_i].close_time if exit_i is not None else None, entry, exit_price, stop, target,
                               entry - stop, _r_multiple("long", entry, exit_price, entry - stop) if exit_price is not None else None,
                               reason, context, vr, cr, ts, candles[-1].close)
        if trade.entry_dt >= min_entry_dt:
            trades.append(trade)
        if exit_i is None:
            break
        # An exit frees the engine before the close decision on that same bar.
        i = exit_i
    return trades


def generate_c(candles: list[Candle], min_entry_dt: int = FORWARD_START_MS) -> list[CandidateTrade]:
    feat = enrich(candles)
    trades: list[CandidateTrade] = []
    i = 672
    while i < len(candles) - 1:
        bar = candles[i]
        close_location = (bar.close - bar.low) / (bar.high - bar.low) if bar.high > bar.low else 0.0
        prior_activity = any(is_valid(feat["ntr_z_672"][j]) and feat["ntr_z_672"][j] > 1 for j in range(i - 4, i))
        taker_share = bar.taker_buy_volume / bar.volume if bar.volume else 0.0
        signal = (
            is_valid(feat["ntr_z_672"][i]) and feat["ntr_z_672"][i] > 5
            and is_valid(feat["adx"][i]) and feat["adx"][i] > 28
            and close_location > 0.65
            and is_valid(feat["obv_slope16"][i]) and feat["obv_slope16"][i] > 4
            and prior_activity and taker_share > 0.52
        )
        if not signal:
            i += 1
            continue
        entry_i = i + 1
        entry = candles[entry_i].open
        atr = feat["atr"][i]
        if not is_valid(atr) or atr <= 0:
            i += 1
            continue
        stop = entry - 3.5 * atr
        target = entry + 10.5 * atr
        exit_i, exit_price, reason = _long_exit(candles, entry_i, stop, target, entry_i + 31)
        vr, cr, ts = _telemetry(candles, feat, i)
        trade = CandidateTrade("C", "long", i, entry_i, exit_i, bar.close_time, candles[entry_i].open_time,
                               candles[exit_i].close_time if exit_i is not None else None, entry, exit_price, stop, target,
                               entry - stop, _r_multiple("long", entry, exit_price, entry - stop) if exit_price is not None else None,
                               reason, False, vr, cr, ts, candles[-1].close)
        if trade.entry_dt >= min_entry_dt:
            trades.append(trade)
        if exit_i is None:
            break
        i = exit_i  # C explicitly permits a signal on the prior trade's exit bar.
    return trades


def generate_d(candles: list[Candle], min_entry_dt: int = FORWARD_START_MS) -> list[CandidateTrade]:
    feat = enrich(candles)
    trades: list[CandidateTrade] = []
    i = 170
    while i < len(candles) - 1:
        bar = candles[i]
        signal = (
            is_valid(feat["compression"][i - 2]) and feat["compression"][i - 2] < 0.70
            and is_valid(feat["tr_ratio"][i - 1]) and feat["tr_ratio"][i - 1] > 1.25
            and is_valid(feat["tr_ratio"][i]) and feat["tr_ratio"][i] > 1.25
            and is_valid(feat["ntr_z_168"][i]) and feat["ntr_z_168"][i] > 1
            and bar.close < bar.open
        )
        if not signal:
            i += 1
            continue
        entry_i = i + 1
        entry = candles[entry_i].open
        atr = feat["atr"][i]
        if not is_valid(atr) or atr <= 0:
            i += 1
            continue
        stop = entry + 2 * atr
        target = entry - 4 * atr
        exit_i, exit_price, reason = _short_exit(candles, entry_i, stop, target, entry_i + 47)
        vr, cr, ts = _telemetry(candles, feat, i)
        trade = CandidateTrade("D", "short", i, entry_i, exit_i, bar.close_time, candles[entry_i].open_time,
                               candles[exit_i].close_time if exit_i is not None else None, entry, exit_price, stop, target,
                               stop - entry, _r_multiple("short", entry, exit_price, stop - entry) if exit_price is not None else None,
                               reason, False, vr, cr, ts, candles[-1].close)
        if trade.entry_dt >= min_entry_dt:
            trades.append(trade)
        if exit_i is None:
            break
        # As with the authoritative D tradebook, the exit bar may produce the
        # next close-based signal after the prior position has been released.
        i = exit_i
    return trades


def generate_candidates(
    candles_15m: list[Candle], candles_1h: list[Candle], candles_4h: list[Candle],
    min_entry_dt: int = FORWARD_START_MS,
) -> list[CandidateTrade]:
    candidates = (
        generate_a(candles_1h, min_entry_dt)
        + generate_b(candles_4h, min_entry_dt)
        + generate_c(candles_15m, min_entry_dt)
        + generate_d(candles_1h, min_entry_dt)
    )
    return sorted(candidates, key=lambda t: (t.entry_dt, {"C": 0, "D": 1, "B": 2, "A": 3}[t.strategy]))
