from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BTC_DATA_MODE", "auto")

from monitor.market import fetch_15m, resample  # noqa: E402
from monitor.strategies import _long_exit  # noqa: E402


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def main() -> int:
    start_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    fetch = fetch_15m(start_ms)
    candles_15m = fetch.candles
    candles_4h_closed = resample(candles_15m, 4, include_partial=False)
    candles_4h_partial = resample(candles_15m, 4, include_partial=True)
    print(f"source={fetch.source}")
    print(f"last 15m candle: open={iso(candles_15m[-1].open_time)} close_time={iso(candles_15m[-1].close_time)} is_closed={candles_15m[-1].is_closed} close_price={candles_15m[-1].close}")

    entry_dt = int(datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    target = 83863.95916895
    stop = 78876.36055403

    print("\n4h candles (closed only) from entry onward:")
    entry_i = None
    for i, bar in enumerate(candles_4h_closed):
        if bar.open_time < entry_dt:
            continue
        if entry_i is None:
            entry_i = i
        touched_target = bar.high >= target
        touched_stop = bar.low <= stop
        print(f"  i={i} open={iso(bar.open_time)} O={bar.open} H={bar.high} L={bar.low} C={bar.close} "
              f"is_closed={bar.is_closed} touched_target={touched_target} touched_stop={touched_stop}")

    print("\n4h candles (with partial tail) from entry onward:")
    for i, bar in enumerate(candles_4h_partial):
        if bar.open_time < entry_dt:
            continue
        touched_target = bar.high >= target
        touched_stop = bar.low <= stop
        print(f"  i={i} open={iso(bar.open_time)} O={bar.open} H={bar.high} L={bar.low} C={bar.close} "
              f"is_closed={bar.is_closed} touched_target={touched_target} touched_stop={touched_stop}")

    if entry_i is not None:
        exit_i, exit_price, reason = _long_exit(candles_4h_closed, entry_i, stop, target, last_i=len(candles_4h_closed) - 1)
        print(f"\n_long_exit() on closed-only series -> exit_i={exit_i} exit_price={exit_price} reason={reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
