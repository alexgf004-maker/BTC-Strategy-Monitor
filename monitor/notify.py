from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _format_entry(row: dict) -> str:
    risk_pct = float(row["planned_risk_frac"]) * 100 if row["planned_risk_frac"] != "" else 0.0
    return (
        f"\U0001F7E2 ENTRADA {row['strategy']} ({row['side']})\n"
        f"Precio: {row['entry_price']}\n"
        f"Fecha UTC: {row['event_dt']}\n"
        f"Riesgo: {risk_pct:.3f}% ({row['planned_risk_dollars']} USD)\n"
        "Simulación paper. No se ejecutó ninguna orden real."
    )


def send_entry_alerts(new_rows: list[dict]) -> None:
    """Best-effort Telegram alert for new paper ENTRY events. Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    entries = [row for row in new_rows if row["event_type"] == "ENTRY"]
    for row in entries:
        _send_message(token, chat_id, _format_entry(row))


def _send_message(token: str, chat_id: str, text: str) -> None:
    url = TELEGRAM_API.format(token=token)
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"Telegram alert failed: {exc}", file=sys.stderr)
