from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .market import DataUnavailable, fetch_15m, resample
from .models import iso
from .notify import send_trade_alerts
from .portfolio import allocate_portfolio
from .strategies import generate_candidates


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("BTC_RUNTIME_DIR", str(ROOT / "runtime"))).expanduser()
SPEC = ROOT / "strategy_suite_v1_FINAL.json"
STATUS = RUNTIME / "status.json"
EVENTS = RUNTIME / "forward_events.csv"
NEW_EVENTS = RUNTIME / "new_events.md"
EXPECTED_SPEC_SHA = "5c601cfb71993c09bd6c512108cb20c5a85f785d2800298a014f77afad29c908"
FIELDNAMES = [
    "event_id", "event_dt", "event_type", "strategy", "side", "signal_dt", "entry_dt", "exit_dt",
    "entry_price", "exit_price", "stop", "target", "R", "planned_risk_frac", "planned_risk_dollars",
    "equity_after", "sr_context", "status", "reason", "volume_ratio", "count_ratio", "taker_share",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def existing_event_ids() -> set[str]:
    if not EVENTS.exists():
        return set()
    with EVENTS.open(newline="", encoding="utf-8") as handle:
        return {row["event_id"] for row in csv.DictReader(handle) if row.get("event_id")}


def write_events(rows: list[dict]) -> None:
    with EVENTS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def write_notification(new_rows: list[dict], data_through: str) -> None:
    if not new_rows:
        NEW_EVENTS.unlink(missing_ok=True)
        return
    entries = sum(row["event_type"] == "ENTRY" for row in new_rows)
    exits = sum(row["event_type"] == "EXIT" for row in new_rows)
    title = f"Paper BTC: {entries} entrada(s), {exits} salida(s)"
    lines = [f"# {title}", "", f"Datos cerrados hasta **{data_through}**.", "", "|Evento|Motor|Lado|Fecha UTC|Precio|R|Riesgo|", "|---|---|---|---|---:|---:|---:|"]
    for row in new_rows:
        price = row["entry_price"] if row["event_type"] in ("ENTRY", "SKIP") else row["exit_price"]
        lines.append(f"|{row['event_type']}|{row['strategy']}|{row['side']}|{row['event_dt']}|{price}|{row['R']}|{row['planned_risk_frac']}|")
    lines.extend(["", "> Simulación paper. No se ejecutó ninguna orden real."])
    NEW_EVENTS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def should_persist_status(old: dict, new_event_count: int, now: datetime) -> bool:
    if new_event_count:
        return True
    if old.get("health") not in ("ok", "delayed_official_archive"):
        return True
    previous = old.get("last_success_utc")
    if not previous:
        return True
    try:
        last = datetime.fromisoformat(previous.replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - last >= timedelta(hours=1)


def persist_failure(message: str) -> None:
    old = read_json(STATUS, {})
    already_degraded = old.get("health") == "data_unavailable"
    payload = {**old, "mode": "paper", "health": "data_unavailable", "last_attempt_utc": utc_now().isoformat().replace("+00:00", "Z"), "error": message}
    STATUS.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not already_degraded:
        NEW_EVENTS.write_text(f"# Paper BTC: fuente de datos no disponible\n\nEl monitor falló de forma cerrada y no generó señales.\n\n`{message}`\n", encoding="utf-8")


def main() -> int:
    RUNTIME.mkdir(exist_ok=True)
    actual_sha = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    if actual_sha != EXPECTED_SPEC_SHA:
        message = f"Frozen spec SHA mismatch: expected {EXPECTED_SPEC_SHA}, got {actual_sha}"
        persist_failure(message)
        print(message, file=sys.stderr)
        return 2

    now = utc_now()
    # Fixed causal warm-up. It precedes forward OOS by 123 days.
    warmup_start = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    old_status = read_json(STATUS, {})
    old_ids = existing_event_ids()
    try:
        market_fetch = fetch_15m(warmup_start)
        candles_15m = market_fetch.candles
        candles_1h = resample(candles_15m, 1)
        candles_4h = resample(candles_15m, 4)
        candidates = generate_candidates(candles_15m, candles_1h, candles_4h)
        portfolio = allocate_portfolio(candidates)
    except (DataUnavailable, OSError, ValueError) as exc:
        persist_failure(str(exc))
        print(f"Monitor stopped safely: {exc}", file=sys.stderr)
        return 1

    write_events(portfolio.events)
    new_rows = [row for row in portfolio.events if row["event_id"] not in old_ids]
    data_through = iso(candles_15m[-1].close_time)
    write_notification(new_rows, data_through)
    send_trade_alerts(new_rows)

    status = {
        "suite_id": "BTCUSDT_Strategy_Suite_v1.0_FINAL",
        "mode": "paper",
        "health": "ok" if market_fetch.source == "binance_futures_realtime" else "delayed_official_archive",
        "market": "Binance USD-M Futures BTCUSDT",
        "data_source": market_fetch.source,
        "data_warning": market_fetch.warning,
        "data_through_utc": data_through,
        "last_success_utc": now.isoformat().replace("+00:00", "Z"),
        "realized_equity": round(portfolio.realized_equity, 8),
        "start_equity": 800.0,
        "open_positions": portfolio.open_positions,
        "event_count": len(portfolio.events),
        "new_event_count": len(new_rows),
        "bars": {"15m": len(candles_15m), "1h": len(candles_1h), "4h": len(candles_4h)},
        "implementation_status": "spec_reconstruction_unverified_against_missing_canonical_dataset",
        "spec_sha256": actual_sha,
        "real_orders_enabled": False,
    }
    if should_persist_status(old_status, len(new_rows), now):
        STATUS.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (RUNTIME / "run_result.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
