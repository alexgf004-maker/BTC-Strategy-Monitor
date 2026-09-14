from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


UTC = timezone.utc


def iso(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Candle:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    number_trades: int
    taker_buy_volume: float


@dataclass
class CandidateTrade:
    strategy: str
    side: str
    signal_i: int
    entry_i: int
    exit_i: Optional[int]
    signal_dt: int
    entry_dt: int
    exit_dt: Optional[int]
    entry: float
    exit: Optional[float]
    stop: float
    target: Optional[float]
    risk_distance: float
    r_multiple: Optional[float]
    reason: str = "open"
    sr_context: bool = False
    volume_ratio: Optional[float] = None
    count_ratio: Optional[float] = None
    taker_share: Optional[float] = None
    current_price: Optional[float] = None


@dataclass
class OpenAllocation:
    trade: CandidateTrade
    planned_risk_fraction: float
    planned_risk_dollars: float
    accepted: bool = True


@dataclass
class PortfolioResult:
    realized_equity: float
    events: list[dict] = field(default_factory=list)
    open_positions: list[dict] = field(default_factory=list)
