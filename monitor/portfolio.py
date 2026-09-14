from __future__ import annotations

import hashlib

from .models import CandidateTrade, OpenAllocation, PortfolioResult, iso


START_EQUITY = 800.0
GLOBAL_CAP = 0.02
AC_CAP = 0.0125
TARGETS = {"A": 0.0075, "B": 0.005, "C": 0.0075, "D": 0.005}
PRIORITY = {"C": 0, "D": 1, "B": 2, "A": 3}


def _event_id(event_type: str, trade: CandidateTrade) -> str:
    # An ENTRY keeps the same identity when the position later acquires an exit.
    # EXIT includes the actual exit availability timestamp.
    exit_component = (trade.exit_dt or 0) if event_type == "EXIT" else 0
    raw = f"v2|{event_type}|{trade.strategy}|{trade.signal_dt}|{trade.entry_dt}|{exit_component}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _base_event(event_type: str, trade: CandidateTrade) -> dict:
    return {
        "event_id": _event_id(event_type, trade),
        "event_dt": iso(trade.entry_dt if event_type in ("ENTRY", "SKIP") else trade.exit_dt),
        "event_type": event_type,
        "strategy": trade.strategy,
        "side": trade.side,
        "signal_dt": iso(trade.signal_dt),
        "entry_dt": iso(trade.entry_dt),
        "exit_dt": iso(trade.exit_dt),
        "entry_price": round(trade.entry, 8),
        "exit_price": round(trade.exit, 8) if trade.exit is not None else "",
        "stop": round(trade.stop, 8),
        "target": round(trade.target, 8) if trade.target is not None else "",
        "R": round(trade.r_multiple, 10) if trade.r_multiple is not None else "",
        "planned_risk_frac": "",
        "planned_risk_dollars": "",
        "equity_after": "",
        "sr_context": str(trade.sr_context).lower(),
        "status": "paper",
        "reason": trade.reason,
        "volume_ratio": round(trade.volume_ratio, 6) if trade.volume_ratio is not None else "",
        "count_ratio": round(trade.count_ratio, 6) if trade.count_ratio is not None else "",
        "taker_share": round(trade.taker_share, 6) if trade.taker_share is not None else "",
    }


def allocate_portfolio(candidates: list[CandidateTrade], start_equity: float = START_EQUITY) -> PortfolioResult:
    """Replay the frozen risk manager over deterministic candidate trades."""
    equity = start_equity
    open_allocations: dict[tuple[str, int], OpenAllocation] = {}
    events: list[dict] = []

    timeline: dict[int, dict[str, list[CandidateTrade]]] = {}
    for trade in candidates:
        timeline.setdefault(trade.entry_dt, {"entries": [], "exits": []})["entries"].append(trade)
        if trade.exit_dt is not None:
            timeline.setdefault(trade.exit_dt, {"entries": [], "exits": []})["exits"].append(trade)

    for timestamp in sorted(timeline):
        item = timeline[timestamp]
        # Realized exits release their original dollar risk before same-time decisions.
        for trade in sorted(item["exits"], key=lambda t: PRIORITY[t.strategy]):
            key = (trade.strategy, trade.entry_dt)
            allocation = open_allocations.pop(key, None)
            if allocation is None or not allocation.accepted:
                continue
            pnl = allocation.planned_risk_dollars * float(trade.r_multiple or 0.0)
            equity += pnl
            event = _base_event("EXIT", trade)
            event.update({
                "planned_risk_frac": round(allocation.planned_risk_fraction, 8),
                "planned_risk_dollars": round(allocation.planned_risk_dollars, 8),
                "equity_after": round(equity, 8),
            })
            events.append(event)

        for trade in sorted(item["entries"], key=lambda t: PRIORITY[t.strategy]):
            target = 0.00625 if trade.strategy == "B" and trade.sr_context else TARGETS[trade.strategy]
            current_open_risk = sum(a.planned_risk_dollars for a in open_allocations.values() if a.accepted)
            ac_open_risk = sum(a.planned_risk_dollars for a in open_allocations.values() if a.accepted and a.trade.strategy in ("A", "C"))
            desired = target * equity
            global_room = max(0.0, GLOBAL_CAP * equity - current_open_risk)
            ac_room = max(0.0, AC_CAP * equity - ac_open_risk) if trade.strategy in ("A", "C") else float("inf")
            risk_dollars = min(desired, global_room, ac_room)
            key = (trade.strategy, trade.entry_dt)
            if risk_dollars <= 1e-10:
                open_allocations[key] = OpenAllocation(trade, 0.0, 0.0, accepted=False)
                event = _base_event("SKIP", trade)
                event.update({"planned_risk_frac": 0.0, "planned_risk_dollars": 0.0, "equity_after": round(equity, 8), "reason": "risk_capacity_zero"})
                events.append(event)
                continue
            actual_fraction = risk_dollars / equity if equity > 0 else 0.0
            allocation = OpenAllocation(trade, actual_fraction, risk_dollars, accepted=True)
            open_allocations[key] = allocation
            event = _base_event("ENTRY", trade)
            event.update({
                "planned_risk_frac": round(actual_fraction, 8),
                "planned_risk_dollars": round(risk_dollars, 8),
                "equity_after": round(equity, 8),
            })
            events.append(event)

    open_positions: list[dict] = []
    for allocation in open_allocations.values():
        if not allocation.accepted or allocation.trade.exit_dt is not None:
            continue
        trade = allocation.trade
        direction = 1 if trade.side == "long" else -1
        current_r = None
        if trade.current_price is not None and trade.risk_distance > 0:
            current_r = (direction * (trade.current_price - trade.entry) - 0.0024 * trade.entry) / trade.risk_distance
        open_positions.append({
            "strategy": trade.strategy,
            "side": trade.side,
            "entry_dt": iso(trade.entry_dt),
            "entry_price": round(trade.entry, 8),
            "stop": round(trade.stop, 8),
            "target": round(trade.target, 8) if trade.target is not None else None,
            "planned_risk_frac": round(allocation.planned_risk_fraction, 8),
            "planned_risk_dollars": round(allocation.planned_risk_dollars, 8),
            "current_R": round(current_r, 6) if current_r is not None else None,
            "sr_context": trade.sr_context,
        })
    return PortfolioResult(realized_equity=equity, events=sorted(events, key=lambda e: (e["event_dt"], e["event_type"])), open_positions=open_positions)
