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


def _format_exit(row: dict) -> str:
    r_multiple = float(row["R"]) if row["R"] != "" else 0.0
    icon = "✅" if r_multiple >= 0 else "❌"
    return (
        f"{icon} SALIDA {row['strategy']} ({row['side']})\n"
        f"Entrada: {row['entry_price']} → Salida: {row['exit_price']}\n"
        f"Resultado: {r_multiple:.3f}R ({row['reason']})\n"
        f"Equity: {row['equity_after']} USD\n"
        f"Fecha UTC: {row['event_dt']}\n"
        "Simulación paper. No se ejecutó ninguna orden real."
    )


def send_trade_alerts(new_rows: list[dict]) -> None:
    """Best-effort Telegram alert for new paper ENTRY/EXIT events. Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    for row in new_rows:
        if row["event_type"] == "ENTRY":
            _send_message(token, chat_id, _format_entry(row))
        elif row["event_type"] == "EXIT":
            _send_message(token, chat_id, _format_exit(row))


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
