#!/usr/bin/env python3
"""Reproduce the canonical historical engine audit without bundling market data.

Usage:
    python tools/validate_historical.py /path/to/BTCUSDT_Futures_15m_2020-01_to_2026-08.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitor.market import resample
from monitor.models import Candle
from monitor.portfolio import allocate_portfolio
from monitor.strategies import _r_multiple, generate_candidates


EXPECTED_DATA_SHA256 = "58a901afdeddcc36a69c6b600d68e243321b1e76e64ef89255fb486132e8c052"
EXPECTED_BARS = {"15m": 233_760, "1h": 58_440, "4h": 14_610}
EXPECTED_TRADES = {"A": 141, "B": 220, "C": 100, "D": 93}
EXPECTED_SR_B = 25
EXPECTED_CAUSAL_BASE_EQUITY = 3102.7781938877774
EXPECTED_CAUSAL_SR_EQUITY = 3175.3760692316546


def load_candles(path: Path) -> list[Candle]:
    rows: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for item in csv.DictReader(handle):
            rows.append(Candle(
                open_time=int(item["open_time"]),
                close_time=int(item["close_time"]),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item["volume"]),
                number_trades=int(item["count"]),
                taker_buy_volume=float(item["taker_buy_volume"]),
            ))
    return rows


def close_at_cutoff(candidates, series_by_strategy):
    result = []
    for trade in candidates:
        if trade.exit_i is not None:
            result.append(replace(trade, exit_dt=trade.exit_dt + 1))
            continue
        series = series_by_strategy[trade.strategy]
        price = series[-1].close
        result.append(replace(
            trade,
            exit_i=len(series) - 1,
            exit_dt=series[-1].close_time + 1,
            exit=price,
            r_multiple=_r_multiple(trade.side, trade.entry, price, trade.risk_distance),
            reason="cutoff_mark",
        ))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()

    digest = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    candles_15m = load_candles(args.dataset)
    candles_1h = resample(candles_15m, 1)
    candles_4h = resample(candles_15m, 4)
    candidates = generate_candidates(candles_15m, candles_1h, candles_4h, min_entry_dt=0)
    counts = dict(sorted(Counter(trade.strategy for trade in candidates).items()))
    sr_count = sum(trade.strategy == "B" and trade.sr_context for trade in candidates)

    closed = close_at_cutoff(candidates, {"A": candles_1h, "B": candles_4h, "C": candles_15m, "D": candles_1h})
    base = allocate_portfolio([replace(trade, sr_context=False) for trade in closed]).realized_equity
    with_sr = allocate_portfolio(closed).realized_equity
    bars = {"15m": len(candles_15m), "1h": len(candles_1h), "4h": len(candles_4h)}

    checks = {
        "dataset_sha256": digest == EXPECTED_DATA_SHA256,
        "bar_counts": bars == EXPECTED_BARS,
        "candidate_counts": counts == EXPECTED_TRADES,
        "B_support_contexts": sr_count == EXPECTED_SR_B,
        "causal_base_equity": abs(base - EXPECTED_CAUSAL_BASE_EQUITY) < 1e-9,
        "causal_SR_equity": abs(with_sr - EXPECTED_CAUSAL_SR_EQUITY) < 1e-9,
    }
    payload = {
        "passed": all(checks.values()),
        "checks": checks,
        "dataset_sha256": digest,
        "bars": bars,
        "candidate_counts": counts,
        "B_support_contexts": sr_count,
        "causal_closed_bar_replay": {"base_equity": base, "with_B_SR_0625_equity": with_sr},
        "note": "The frozen 3136.14/3210.73 figures use the legacy sequential same-timestamp research replay; see validation/historical_audit.json.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
