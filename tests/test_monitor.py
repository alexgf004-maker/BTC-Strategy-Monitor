from __future__ import annotations

import hashlib
import io
import json
import math
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from monitor.indicators import zscore_prior
from monitor.market import _parse_archive, fetch_15m_realtime, resample
from monitor.models import Candle, CandidateTrade
from monitor.notify import _format_entry, send_trade_alerts
from monitor.portfolio import allocate_portfolio
from monitor import runner
from monitor.service import seconds_until_next_run
from monitor.strategies import FORWARD_START_MS, _long_exit, generate_a, support_touch_events


ROOT = Path(__file__).resolve().parents[1]
FIFTEEN = 15 * 60 * 1000
HOUR = 60 * 60 * 1000


def candle(t: int, price: float = 100.0, count: int = 100, interval: int = FIFTEEN) -> Candle:
    return Candle(t, t + interval - 1, price - 0.1, price + 0.3, price - 0.3, price, 1000.0, count, 550.0)


def candidate(strategy: str, entry_dt: int, exit_dt: int | None = None, r: float | None = None) -> CandidateTrade:
    return CandidateTrade(
        strategy=strategy, side="short" if strategy == "D" else "long",
        signal_i=1, entry_i=2, exit_i=3 if exit_dt else None,
        signal_dt=entry_dt - HOUR, entry_dt=entry_dt, exit_dt=exit_dt,
        entry=100.0, exit=101.0 if exit_dt else None,
        stop=98.0 if strategy != "D" else 102.0, target=104.0,
        risk_distance=2.0, r_multiple=r, current_price=101.0,
    )


class FrozenSpecTests(unittest.TestCase):
    def test_frozen_spec_hash(self):
        digest = hashlib.sha256((ROOT / "strategy_suite_v1_FINAL.json").read_bytes()).hexdigest()
        self.assertEqual(digest, "5c601cfb71993c09bd6c512108cb20c5a85f785d2800298a014f77afad29c908")

    def test_no_order_or_key_code_paths(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "monitor").glob("*.py"))
        forbidden = ["/fapi/v1/order", "newClientOrderId", "BINANCE_API_KEY", "BINANCE_API_SECRET"]
        for token in forbidden:
            self.assertNotIn(token, source)

    def test_forward_start_is_exact(self):
        expected = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(FORWARD_START_MS, expected)

    def test_persistent_service_runs_after_next_candle_close(self):
        self.assertEqual(seconds_until_next_run(900.0), 935.0)
        self.assertEqual(seconds_until_next_run(1799.0), 36.0)


class CausalityTests(unittest.TestCase):
    def test_zscore_excludes_current_value(self):
        values = [1.0, 2.0, 3.0, 100.0]
        result = zscore_prior(values, window=3, min_periods=3)
        expected = (100.0 - 2.0) / math.sqrt(2.0 / 3.0)
        self.assertAlmostEqual(result[3], expected)

    def test_zscore_prior_supports_sample_ddof(self):
        values = [1.0, 2.0, 3.0, 100.0]
        result = zscore_prior(values, window=3, min_periods=3, ddof=1)
        expected = (100.0 - 2.0) / math.sqrt(2.0 / 2.0)
        self.assertAlmostEqual(result[3], expected)

    def test_resample_requires_complete_left_labeled_bucket(self):
        start = FORWARD_START_MS
        bars = [candle(start + i * FIFTEEN, 100 + i) for i in range(5)]
        result = resample(bars, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].open_time, start)
        self.assertEqual(result[0].open, bars[0].open)
        self.assertEqual(result[0].close, bars[3].close)

    def test_resample_exposes_only_the_partial_tail_as_an_entry_stub(self):
        start = FORWARD_START_MS
        bars = [candle(start + i * FIFTEEN, 100 + i) for i in range(4)]
        bars[-1] = replace(bars[-1], is_closed=False)
        self.assertEqual(resample(bars, 1), [])
        result = resample(bars, 1, include_partial=True)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].is_closed)
        self.assertEqual(result[0].open_time, start)
        self.assertEqual(result[0].open, bars[0].open)

    def test_partial_entry_bar_cannot_close_a_trade_early(self):
        bar = replace(candle(FORWARD_START_MS, 100.0), high=200.0, low=50.0, is_closed=False)
        exit_i, exit_price, reason = _long_exit([bar], 0, 98.0, 104.0, 0)
        self.assertIsNone(exit_i)
        self.assertIsNone(exit_price)
        self.assertEqual(reason, "open")

    def test_support_catalog_counts_interactions_and_ignores_partial_bar(self):
        lows = [103.0, 102.0, 101.0, 100.0, 101.0, 102.0, 103.0]
        bars = [
            Candle(i * HOUR, (i + 1) * HOUR - 1, low + 0.5, low + 1.0, low, low + 0.5, 1.0, 1, 0.5)
            for i, low in enumerate(lows)
        ]
        for i in range(7, 20):
            touch = i in (7, 10, 13, 16, 19)
            low = 100.1 if touch else 101.0
            bars.append(Candle(
                i * HOUR, (i + 1) * HOUR - 1, low + 0.4, low + 0.6, low, low + 0.4,
                1.0, 1, 0.5, is_closed=i != 19,
            ))
        events = support_touch_events(bars, {"atr": [1.0] * len(bars)})
        self.assertEqual(events[8 * HOUR], 1)
        self.assertEqual(events[11 * HOUR], 2)
        self.assertEqual(events[14 * HOUR], 3)
        self.assertEqual(events[17 * HOUR], 4)
        self.assertNotIn(20 * HOUR, events)

    def test_engine_a_enters_on_next_bar(self):
        bars = []
        for i in range(240):
            price = 100.0
            count = 100 + i % 7
            if i == 205:
                price = 102.0
                count = 3000
            elif i > 205:
                price = 102.2
            bar = candle(FORWARD_START_MS + i * HOUR, price, count, HOUR)
            bars.append(Candle(bar.open_time, bar.close_time, price - 0.2, price + 0.4, price - 0.4, price, bar.volume, count, bar.taker_buy_volume))
        trades = generate_a(bars)
        self.assertTrue(trades)
        self.assertEqual(trades[0].entry_i, trades[0].signal_i + 1)
        self.assertGreater(trades[0].entry_dt, trades[0].signal_dt)

    def test_binance_archive_microseconds_are_normalized(self):
        csv_line = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_base,taker_quote,ignore\n1788220800000000,100,102,99,101,12,1788221699999000,0,45,7,0,0\n"
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("BTCUSDT-15m-test.csv", csv_line)
        rows = _parse_archive(payload.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].open_time, 1788220800000)
        self.assertEqual(rows[0].close_time, 1788221699999)

    @patch("monitor.market._request")
    @patch("monitor.market.time.time")
    def test_realtime_fetch_keeps_one_current_kline_for_next_open(self, now, request):
        start = FORWARD_START_MS
        now_ms = start + 800 * FIFTEEN + 30_000
        now.return_value = now_ms / 1000
        request.return_value = [
            [
                start + i * FIFTEEN, "100", "101", "99", "100.5", "10",
                start + (i + 1) * FIFTEEN - 1, "0", 100, "5", "0", "0",
            ]
            for i in range(801)
        ]
        rows = fetch_15m_realtime(start, now_ms)
        self.assertEqual(len(rows), 801)
        self.assertTrue(rows[-2].is_closed)
        self.assertFalse(rows[-1].is_closed)


class RiskManagerTests(unittest.TestCase):
    def test_ac_shared_cap(self):
        t = FORWARD_START_MS
        result = allocate_portfolio([candidate("A", t), candidate("C", t)])
        entries = [e for e in result.events if e["event_type"] == "ENTRY"]
        fractions = {e["strategy"]: float(e["planned_risk_frac"]) for e in entries}
        self.assertAlmostEqual(fractions["C"], 0.0075)
        self.assertAlmostEqual(fractions["A"], 0.005)
        self.assertLessEqual(sum(fractions.values()), 0.0125 + 1e-12)

    def test_global_cap_scales_last_priority(self):
        t = FORWARD_START_MS
        result = allocate_portfolio([candidate(x, t) for x in ("A", "B", "C", "D")])
        entries = [e for e in result.events if e["event_type"] == "ENTRY"]
        total = sum(float(e["planned_risk_dollars"]) for e in entries)
        self.assertLessEqual(total, 800 * 0.02 + 1e-8)
        a = next(e for e in entries if e["strategy"] == "A")
        self.assertAlmostEqual(float(a["planned_risk_frac"]), 0.0025)

    def test_exit_updates_realized_equity(self):
        t = FORWARD_START_MS
        result = allocate_portfolio([candidate("B", t, t + HOUR, 1.5)])
        self.assertAlmostEqual(result.realized_equity, 806.0)

    def test_events_carry_stop_and_target(self):
        t = FORWARD_START_MS
        result = allocate_portfolio([candidate("B", t)])
        entry = next(e for e in result.events if e["event_type"] == "ENTRY")
        self.assertEqual(entry["stop"], 98.0)
        self.assertEqual(entry["target"], 104.0)

    def test_entry_event_id_does_not_change_when_exit_becomes_known(self):
        t = FORWARD_START_MS
        open_result = allocate_portfolio([candidate("B", t)])
        closed_result = allocate_portfolio([candidate("B", t, t + HOUR, 1.5)])
        open_entry = next(e for e in open_result.events if e["event_type"] == "ENTRY")
        closed_entry = next(e for e in closed_result.events if e["event_type"] == "ENTRY")
        self.assertEqual(open_entry["event_id"], closed_entry["event_id"])


class TelegramAlertTests(unittest.TestCase):
    def _rows(self):
        return [
            {"event_id": "entry-1", "event_type": "ENTRY", "strategy": "B", "side": "long", "entry_price": 100.0,
             "stop": 97.5, "target": 103.75,
             "event_dt": "2026-09-01T00:00:00Z", "planned_risk_frac": 0.005, "planned_risk_dollars": 4.0},
            {"event_id": "exit-1", "event_type": "EXIT", "strategy": "A", "side": "long", "entry_price": 100.0,
             "exit_price": 101.5, "R": 0.75, "equity_after": 806.0, "reason": "target",
             "event_dt": "2026-09-01T01:00:00Z", "planned_risk_frac": "", "planned_risk_dollars": ""},
            {"event_id": "skip-1", "event_type": "SKIP", "strategy": "A", "side": "long", "entry_price": 100.0,
             "event_dt": "2026-09-01T02:00:00Z", "planned_risk_frac": 0.0, "planned_risk_dollars": 0.0},
        ]

    @patch.dict("os.environ", {}, clear=True)
    @patch("monitor.notify.urllib.request.urlopen")
    def test_no_alert_without_credentials(self, urlopen):
        sent, errors = send_trade_alerts(self._rows())
        self.assertEqual(sent, 0)
        self.assertEqual(errors, ["telegram_credentials_missing"])
        urlopen.assert_not_called()

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}, clear=True)
    @patch("monitor.notify.urllib.request.urlopen")
    def test_alert_sent_for_entries_and_exits_but_not_skips(self, urlopen):
        sent, errors = send_trade_alerts(self._rows())
        self.assertEqual(sent, 2)
        self.assertEqual(errors, [])
        self.assertEqual(urlopen.call_count, 2)

    def test_outbox_retries_then_marks_alert_sent(self):
        rows = self._rows()[:1]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runner, "ALERT_OUTBOX", Path(directory) / "outbox.json"), \
             patch("monitor.runner.send_trade_alerts", side_effect=[(0, ["timeout"]), (1, [])]):
            first = runner.deliver_alert_outbox(rows)
            second = runner.deliver_alert_outbox([])
            payload = json.loads(runner.ALERT_OUTBOX.read_text(encoding="utf-8"))
        self.assertEqual(first["pending_count"], 1)
        self.assertEqual(second["pending_count"], 0)
        self.assertEqual(payload["items"][0]["attempts"], 2)
        self.assertEqual(payload["items"][0]["status"], "sent")

    def test_entry_message_states_price_target_when_present(self):
        message = _format_entry({
            "strategy": "B", "side": "long", "entry_price": 100.0, "stop": 97.5, "target": 103.75,
            "event_dt": "2026-09-01T00:00:00Z", "planned_risk_frac": 0.005, "planned_risk_dollars": 4.0,
        })
        self.assertIn("Stop (precio): 97.5", message)
        self.assertIn("Objetivo (precio): 103.75", message)

    def test_entry_message_states_time_exit_when_no_target(self):
        message = _format_entry({
            "strategy": "A", "side": "long", "entry_price": 100.0, "stop": 98.5, "target": "",
            "event_dt": "2026-09-01T00:00:00Z", "planned_risk_frac": 0.005, "planned_risk_dollars": 4.0,
        })
        self.assertIn("sin precio fijo", message)
        self.assertIn("checkpoint a las 6h", message)


if __name__ == "__main__":
    unittest.main()
