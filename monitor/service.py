from __future__ import annotations

import csv
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import runner


PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT_ID") else "127.0.0.1")
INTERVAL_SECONDS = 15 * 60
RUN_DELAY_SECONDS = int(os.getenv("BTC_RUN_DELAY_SECONDS", "35"))
READ_TOKEN = os.getenv("MONITOR_READ_TOKEN", "")
ALLOWED_ORIGIN = os.getenv(
    "MONITOR_ALLOWED_ORIGIN",
    "https://btc-strategy-lab-dg2609.alex-gf004.chatgpt.site",
)
RUN_LOCK = threading.Lock()
STOP = threading.Event()
SERVICE_STARTED = datetime.now(timezone.utc)


def seconds_until_next_run(now: float) -> float:
    """Wait until shortly after the next UTC 15-minute candle boundary."""
    boundary = ((int(now) // INTERVAL_SECONDS) + 1) * INTERVAL_SECONDS
    return max(1.0, boundary + RUN_DELAY_SECONDS - now)


def run_monitor() -> None:
    with RUN_LOCK:
        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        print(f"[service] monitor run started at {started}", flush=True)
        try:
            result = runner.main()
        except Exception as exc:
            print(f"[service] unexpected monitor error: {type(exc).__name__}: {exc}", flush=True)
        else:
            print(f"[service] monitor run completed with exit code {result}", flush=True)


def scheduler() -> None:
    run_monitor()
    while not STOP.wait(seconds_until_next_run(time.time())):
        run_monitor()


def read_status() -> dict:
    try:
        return json.loads(runner.STATUS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"mode": "paper", "health": "starting", "real_orders_enabled": False}


def read_events(limit: int = 200) -> list[dict]:
    try:
        with runner.EVENTS.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError:
        return []
    return rows[-limit:]


class Handler(BaseHTTPRequestHandler):
    server_version = "BTCStrategyMonitor/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[http] {self.address_string()} {format % args}", flush=True)

    def end_headers(self) -> None:
        if ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def send_json(self, status: HTTPStatus, payload: dict | list) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        if not READ_TOKEN:
            return False
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {READ_TOKEN}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_json(HTTPStatus.OK, {
                "service": "btc-strategy-monitor",
                "status": "running",
                "mode": "paper",
                "real_orders_enabled": False,
            })
            return
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {
                "error": "unauthorized",
                "detail": "A valid bearer token is required.",
            })
            return
        if self.path == "/status.json":
            payload = read_status()
            payload["service_started_utc"] = SERVICE_STARTED.isoformat().replace("+00:00", "Z")
            payload["evaluation_in_progress"] = RUN_LOCK.locked()
            self.send_json(HTTPStatus.OK, payload)
            return
        if self.path == "/events.json":
            self.send_json(HTTPStatus.OK, {"events": read_events()})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})


def main() -> int:
    runner.RUNTIME.mkdir(parents=True, exist_ok=True)
    worker = threading.Thread(target=scheduler, name="paper-monitor", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[service] listening on {HOST}:{PORT}; paper mode only", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
