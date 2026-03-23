"""
monitoring/portfolio.py

Tracks positions, cash, P&L, and basic risk metrics in-process.
Reconciles against broker state periodically to catch drift.

This is your source of truth for risk checks during a session.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    qty: int                # positive = long, negative = short
    avg_entry_price: float
    current_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * (self.current_price - self.avg_entry_price)

    @property
    def unrealized_pnl_pct(self) -> float:
        cost = abs(self.qty * self.avg_entry_price)
        return self.unrealized_pnl / cost if cost > 0 else 0.0


class Portfolio:
    def __init__(self, initial_cash: float = 10_000):
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._realized_pnl: float = 0.0
        self._peak_value: float = initial_cash   # for drawdown calculation

        self._history: list[dict] = []           # periodic snapshots

    # ------------------------------------------------------------------
    # Price updates (called every bar)
    # ------------------------------------------------------------------

    def update_prices(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            if symbol in self._positions:
                self._positions[symbol].current_price = price
        self._update_peak()

    def current_price(self, symbol: str) -> float:
        pos = self._positions.get(symbol)
        return pos.current_price if pos else 0.0

    # ------------------------------------------------------------------
    # Position management (called by OrderManager after fills)
    # ------------------------------------------------------------------

    def apply_fill(self, symbol: str, qty: int, price: float, side: str) -> None:
        """
        Updates cash and positions when an order is filled.
        qty is always positive; side is 'buy' or 'sell'.
        """
        cost = qty * price

        if side == "buy":
            self._cash -= cost
            if symbol in self._positions:
                pos = self._positions[symbol]
                total_qty = pos.qty + qty
                pos.avg_entry_price = (
                    (pos.qty * pos.avg_entry_price + cost) / total_qty
                )
                pos.qty = total_qty
            else:
                self._positions[symbol] = Position(
                    symbol=symbol, qty=qty, avg_entry_price=price, current_price=price
                )

        elif side == "sell":
            self._cash += cost
            if symbol in self._positions:
                pos = self._positions[symbol]
                # Record realized P&L on the shares being sold
                realized = qty * (price - pos.avg_entry_price)
                self._realized_pnl += realized
                pos.qty -= qty
                if pos.qty == 0:
                    del self._positions[symbol]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def total_value(self) -> float:
        """Cash + mark-to-market value of all positions."""
        return self._cash + sum(p.market_value for p in self._positions.values())

    def total_exposure(self) -> float:
        """Total long market value (absolute)."""
        return sum(abs(p.market_value) for p in self._positions.values())

    def position_value(self, symbol: str) -> float:
        pos = self._positions.get(symbol)
        return abs(pos.market_value) if pos else 0.0

    def drawdown(self) -> float:
        """Current drawdown from peak (0.0 = at peak, 0.1 = 10% below peak)."""
        v = self.total_value()
        return (self._peak_value - v) / self._peak_value if self._peak_value > 0 else 0.0

    def _update_peak(self) -> None:
        v = self.total_value()
        if v > self._peak_value:
            self._peak_value = v

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_status(self) -> None:
        total = self.total_value()
        dd = self.drawdown()

        log.info("=" * 60)
        log.info("Portfolio snapshot  %s", datetime.now().strftime("%H:%M:%S"))
        log.info("  Total value:      $%.2f", total)
        log.info("  Cash:             $%.2f", self._cash)
        log.info("  Realized P&L:     $%.2f", self._realized_pnl)
        log.info("  Drawdown:         %.2f%%", dd * 100)

        for symbol, pos in self._positions.items():
            log.info(
                "  %-6s  qty=%-4d  entry=$%.2f  now=$%.2f  uPnL=$%.2f (%.1f%%)",
                symbol, pos.qty, pos.avg_entry_price, pos.current_price,
                pos.unrealized_pnl, pos.unrealized_pnl_pct * 100,
            )
        log.info("=" * 60)

        # Save snapshot for later analysis
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "total_value": total,
            "cash": self._cash,
            "drawdown": dd,
            "realized_pnl": self._realized_pnl,
        })
