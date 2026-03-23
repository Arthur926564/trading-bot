"""
execution/order_manager.py

Translates Signals into actual broker orders via Alpaca REST API.

Responsibilities:
  1. Position sizing   — how many shares given signal strength + portfolio size
  2. Risk checks       — don't exceed per-symbol or total exposure limits
  3. Order placement   — submit to Alpaca (market or limit)
  4. State tracking    — avoid double-sending, track open positions

This is the most critical layer. Bugs here cost real money.
"""

import os
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from datetime import time as dtime

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

from strategy.signal import Signal, Direction

if TYPE_CHECKING:
    from monitoring.portfolio import Portfolio

log = logging.getLogger(__name__)

ALPACA_BASE = "https://paper-api.alpaca.markets"   # switch to live when ready


@dataclass
class OrderRecord:
    """Audit log entry — every order we send gets recorded."""
    timestamp: datetime
    symbol: str
    side: str
    qty: int
    order_type: str
    signal_strength: float
    order_id: str
    status: str
    reason: str


class OrderManager:
    def __init__(
        self,
        portfolio: "Portfolio",
        max_position_pct: float = 0.20,   # max 20% of portfolio in one symbol
        max_total_exposure: float = 0.90,  # max 90% of portfolio deployed
    ):
        self.portfolio = portfolio
        self.max_position_pct = max_position_pct
        self.max_total_exposure = max_total_exposure

        self._api = tradeapi.REST(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url=ALPACA_BASE,
        )

        self._order_log: list[OrderRecord] = []
        self._last_signal: dict[str, Direction] = {}   # avoid re-sending same direction

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def _is_market_open(self) -> bool:
        clock = self._api.get_clock()
        return clock.is_open


    async def handle_signal(self, signal: Signal) -> None:
        """
        Processes one signal: runs risk checks, sizes the order, submits it.
        Silently skips if the signal would violate risk rules.
        """

        if not self._is_market_open():
            log.info("[SKIP] Market is closed")
            return
        symbol = signal.symbol
        direction = signal.direction

        # Skip if we're already positioned in this direction
        if self._last_signal.get(symbol) == direction:
            return

        # --- Risk checks ---
        if not self._passes_risk(signal):
            log.warning("[RISK BLOCK] %s %s — risk check failed", direction.value, symbol)
            return

        # --- Position sizing ---
        qty = self._compute_qty(signal)
        if qty == 0:
            log.warning("[SKIP] qty=0 for %s", signal.symbol)
            return

        # --- Determine order side and whether to close existing position first ---
        current = self._last_signal.get(symbol, Direction.FLAT)
        if current != Direction.FLAT and current != direction:
            # Flip: close existing position first
            await self._submit_order(symbol, self._opposite_side(current), qty, signal)

        if direction == Direction.FLAT:
            log.info("[FLAT] No new order for %s", symbol)
            self._last_signal[symbol] = direction
            return

        side = "buy" if direction == Direction.LONG else "sell"
        await self._submit_order(symbol, side, qty, signal)
        self._last_signal[symbol] = direction

    # ------------------------------------------------------------------
    # Risk checks
    # ------------------------------------------------------------------

    def _passes_risk(self, signal: Signal) -> bool:
        """Returns False if the signal would push us past risk limits."""
        portfolio_value = self.portfolio.total_value()
        current_exposure = self.portfolio.total_exposure()

        # Don't exceed total deployment limit
        if current_exposure / portfolio_value > self.max_total_exposure:
            return False

        # Don't exceed per-symbol limit
        symbol_value = self.portfolio.position_value(signal.symbol)
        if symbol_value / portfolio_value > self.max_position_pct:
            return False

        return True

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------
    def _compute_qty(self, signal: Signal) -> int:
        portfolio_value = self.portfolio.total_value()
        price = self.portfolio.current_price(signal.symbol)

        # Portfolio doesn't know the price yet — fetch it from Alpaca
        if price == 0:
            try:
                trade = self._api.get_latest_trade(signal.symbol)
                price = trade.price
                log.info("[PRICE] %s fetched from Alpaca: $%.2f", signal.symbol, price)
            except Exception as e:
                log.warning("[PRICE] Could not get price for %s: %s", signal.symbol, e)
                return 0

        target_allocation = signal.strength * self.max_position_pct * portfolio_value
        qty = int(target_allocation / price)
        log.info("[SIZING] %s target=$%.0f price=$%.2f qty=%d",
                 signal.symbol, target_allocation, price, qty)
        return max(qty, 1) if signal.strength > 0.3 else 0


    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    async def _submit_order(
        self, symbol: str, side: str, qty: int, signal: Signal
    ) -> None:
        """
        Submits a market order and records it in the audit log.

        Consider switching to limit orders for less liquid instruments:
          order = self._api.submit_order(
              symbol=symbol, qty=qty, side=side,
              type="limit", time_in_force="day",
              limit_price=round(price * 1.001, 2),  # 0.1% above market for buys
          )
        """
        try:
            order = self._api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type="market",
                time_in_force="day",
            )
            log.info("[ORDER] %s %d %s @ market | id=%s | %s",
                     side.upper(), qty, symbol, order.id, signal.reason)

            self._order_log.append(OrderRecord(
                timestamp=datetime.now(),
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="market",
                signal_strength=signal.strength,
                order_id=order.id,
                status="submitted",
                reason=signal.reason,
            ))

        except APIError as e:
            log.error("[ORDER FAILED] %s %s: %s", side, symbol, e)

    @staticmethod
    def _opposite_side(direction: Direction) -> str:
        return "sell" if direction == Direction.LONG else "buy"

    def get_order_log(self) -> list[OrderRecord]:
        return list(self._order_log)
