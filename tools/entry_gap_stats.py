from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BTC_DATA_MODE", "archive")

from monitor.market import fetch_15m, resample  # noqa: E402
from monitor.strategies import generate_candidates  # noqa: E402


def iso_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    start_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    fetch = fetch_15m(start_ms)
    candles_15m = fetch.candles
    candles_1h = resample(candles_15m, 1)
    candles_4h = resample(candles_15m, 4)
    print(f"source={fetch.source} bars_15m={len(candles_15m)} bars_1h={len(candles_1h)} bars_4h={len(candles_4h)}", flush=True)
    print(f"span={iso_day(candles_15m[0].open_time)} .. {iso_day(candles_15m[-1].close_time)}", flush=True)

    candidates = generate_candidates(candles_15m, candles_1h, candles_4h, min_entry_dt=0)
    per_engine: dict[str, list[int]] = {}
    for trade in candidates:
        per_engine.setdefault(trade.strategy, []).append(trade.entry_dt)

    combined = sorted(trade.entry_dt for trade in candidates)
    print(f"\ntotal_candidates={len(combined)} " + " ".join(f"{k}={len(v)}" for k, v in sorted(per_engine.items())), flush=True)

    def gap_report(label: str, entries: list[int]) -> None:
        entries = sorted(entries)
        if len(entries) < 2:
            print(f"{label}: not enough entries for a gap")
            return
        gaps = [(b - a) / 86_400_000 for a, b in zip(entries, entries[1:])]
        ranked = sorted(zip(gaps, entries, entries[1:]), reverse=True)[:5]
        avg = sum(gaps) / len(gaps)
        print(f"\n{label}: max_gap_days={ranked[0][0]:.2f} avg_gap_days={avg:.2f}")
        print("  top 5 longest gaps (days, from -> to):")
        for gap, a, b in ranked:
            print(f"    {gap:.2f}d  {iso_day(a)} -> {iso_day(b)}")

    gap_report("ALL ENGINES COMBINED", combined)
    for engine, entries in sorted(per_engine.items()):
        gap_report(f"Engine {engine}", entries)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
