from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_DOWN


TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
DISPLAY_LEVERAGE = Decimal("5")
BTC_QTY_STEP = Decimal("0.001")
BTC_MIN_NOTIONAL = Decimal("50")

# Human-readable time-based exit rule per engine, straight from the frozen spec.
TIME_EXIT_TEXT = {
    "A": "checkpoint a las 6h (cierra si el precio no supera la entrada) y salida máxima a las 60h",
    "B": "salida máxima a las 30 velas de 4h (~5 días) si no toca stop ni objetivo",
    "C": "salida máxima a las 8h (32 velas de 15m) si no toca stop ni objetivo",
    "D": "salida máxima a las 48h si no toca stop ni objetivo",
}


def _position_sizing(row: dict) -> dict[str, Decimal | bool]:
    """Translate stop-risk dollars into a conservative, exchange-sized position."""
    entry = Decimal(str(row["entry_price"]))
    stop = Decimal(str(row["stop"]))
    risk = Decimal(str(row["planned_risk_dollars"]))
    distance = abs(entry - stop)
    if entry <= 0 or distance <= 0 or risk <= 0:
        return {"valid": False}
    theoretical_qty = risk / distance
    quantity = (theoretical_qty / BTC_QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * BTC_QTY_STEP
    notional = quantity * entry
    return {
        "valid": quantity >= BTC_QTY_STEP and notional >= BTC_MIN_NOTIONAL,
        "theoretical_qty": theoretical_qty,
        "quantity": quantity,
        "notional": notional,
        "margin": notional / DISPLAY_LEVERAGE,
        "stop_loss": quantity * distance,
    }


def _format_entry(row: dict) -> str:
    risk_pct = float(row["planned_risk_frac"]) * 100 if row["planned_risk_frac"] != "" else 0.0
    time_exit = TIME_EXIT_TEXT.get(row["strategy"], "según la regla de la estrategia")
    if row.get("target", "") != "":
        exit_plan = f"Objetivo (precio): {row['target']}\nSalida por tiempo: {time_exit}"
    else:
        exit_plan = f"Objetivo: sin precio fijo — sale por tiempo: {time_exit}"
    sizing = _position_sizing(row)
    if sizing["valid"]:
        sizing_text = (
            f"Cantidad sugerida: {sizing['quantity']:.3f} BTC (redondeada hacia abajo)\n"
            f"Valor de posición: {sizing['notional']:.2f} USDT\n"
            f"Margen estimado a {DISPLAY_LEVERAGE:.0f}x: {sizing['margin']:.2f} USDT\n"
            f"Pérdida al stop con esa cantidad: {sizing['stop_loss']:.2f} USD, sin comisiones"
        )
    else:
        sizing_text = (
            "Tamaño sugerido: inferior al mínimo operable de BTCUSDT; "
            "no abrir manualmente sin recalcular."
        )
    return (
        f"\U0001F7E2 ENTRADA {row['strategy']} ({row['side']})\n"
        f"Precio: {row['entry_price']}\n"
        f"Stop (precio): {row['stop']}\n"
        f"{exit_plan}\n"
        f"Fecha UTC: {row['event_dt']}\n"
        f"Riesgo máximo: {risk_pct:.3f}% ({row['planned_risk_dollars']} USD), sin comisiones\n"
        f"{sizing_text}\n"
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


def send_trade_alerts(new_rows: list[dict]) -> tuple[int, list[str]]:
    """Attempt Telegram delivery and report successes and retryable errors."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return 0, ["telegram_credentials_missing"] if new_rows else []

    sent = 0
    errors: list[str] = []
    for row in new_rows:
        if row["event_type"] == "ENTRY":
            error = _send_message(token, chat_id, _format_entry(row))
        elif row["event_type"] == "EXIT":
            error = _send_message(token, chat_id, _format_exit(row))
        else:
            continue
        if error is None:
            sent += 1
        else:
            errors.append(error)
    return sent, errors


def _send_message(token: str, chat_id: str, text: str) -> str | None:
    url = TELEGRAM_API.format(token=token)
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"Telegram alert failed: {exc}", file=sys.stderr)
        return f"{type(exc).__name__}: {exc}"
