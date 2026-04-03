"""
execution/order_manager.py
"""

import os
import time
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

from strategy.signal import Signal, Direction

if TYPE_CHECKING:
    from monitoring.portfolio import Portfolio

log = logging.getLogger(__name__)

ALPACA_BASE = "https://paper-api.alpaca.markets"


@dataclass
class OrderRecord:
    timestamp:       datetime
    symbol:          str
    side:            str
    qty:             int
    order_type:      str
    signal_strength: float
    order_id:        str
    status:          str
    reason:          str


class OrderManager:
    def __init__(
        self,
        portfolio:           "Portfolio",
        max_position_pct:    float = 0.20,
        max_total_exposure:  float = 0.90,
    ):
        self.portfolio           = portfolio
        self.max_position_pct    = max_position_pct
        self.max_total_exposure  = max_total_exposure

        self._api = tradeapi.REST(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url=ALPACA_BASE,
        )
        self._order_log:   list[OrderRecord]      = []
        self._last_signal: dict[str, Direction]   = {}

    # ── Market hours ──────────────────────────────────────────────────────
    def _is_market_open(self) -> bool:
        now = time.time()
        if hasattr(self, "_clock_cache") and now - self._clock_cache_time < 60:
            return self._clock_cache
        clock = self._api.get_clock()
        self._clock_cache      = clock.is_open
        self._clock_cache_time = now
        log.info("[CLOCK] is_open=%s next_open=%s next_close=%s",
                 clock.is_open, clock.next_open, clock.next_close)
        return clock.is_open

    # ── Main entry point ──────────────────────────────────────────────────
    async def handle_signal(self, signal: Signal, qty_override: int = None) -> None:
        if not self._is_market_open():
            log.info("[SKIP] Market closed — %s %s", signal.symbol, signal.direction.value)
            return

        symbol    = signal.symbol
        direction = signal.direction

        # Deduplicate — skip if already in this direction
        if self._last_signal.get(symbol) == direction:
            log.debug("[SKIP] Already in %s for %s", direction.value, symbol)
            return

        # ── FLAT: close position unconditionally (no risk check) ──────────
        if direction == Direction.FLAT:
            try:
                position = self._api.get_position(symbol)
                qty      = abs(int(position.qty))
                if qty > 0:
                    await self._submit_order(symbol, "sell", qty, signal)
                    log.info("[CLOSE] %s qty=%d | %s", symbol, qty, signal.reason)
                else:
                    log.info("[FLAT] No open position for %s", symbol)
            except APIError as e:
                # Position doesn't exist on Alpaca — already closed
                log.info("[FLAT] No Alpaca position for %s: %s", symbol, e)
            self._last_signal[symbol] = direction
            return

        # ── Risk check (entries only) ─────────────────────────────────────
        if not self._passes_risk(signal):
            log.warning("[RISK BLOCK] %s %s", symbol, direction.value)
            return

        # ── LONG: size and buy ────────────────────────────────────────────
        qty = qty_override if qty_override is not None else self._compute_qty(signal)
        if qty == 0:
            log.warning("[SKIP] qty=0 for %s", symbol)
            return

        await self._submit_order(symbol, "buy", qty, signal)
        self._last_signal[symbol] = direction

    # ── Risk checks ───────────────────────────────────────────────────────
    def _passes_risk(self, signal: Signal) -> bool:
        portfolio_value  = self.portfolio.total_value()
        if portfolio_value == 0:
            return False

        current_exposure = self.portfolio.total_exposure()
        if current_exposure / portfolio_value > self.max_total_exposure:
            log.warning("[RISK] Total exposure %.1f%% exceeds limit",
                        current_exposure / portfolio_value * 100)
            return False

        symbol_value = self.portfolio.position_value(signal.symbol)
        if symbol_value / portfolio_value > self.max_position_pct:
            log.warning("[RISK] %s position %.1f%% exceeds per-symbol limit",
                        signal.symbol, symbol_value / portfolio_value * 100)
            return False

        return True

    # ── Position sizing (fallback when qty_override not provided) ─────────
    def _compute_qty(self, signal: Signal) -> int:
        portfolio_value = self.portfolio.total_value()
        price           = self.portfolio.current_price(signal.symbol)

        if price == 0:
            try:
                trade = self._api.get_latest_trade(signal.symbol)
                price = trade.price
                log.info("[PRICE] %s fetched from Alpaca: $%.2f", signal.symbol, price)
            except Exception as e:
                log.warning("[PRICE] Could not get price for %s: %s", signal.symbol, e)
                return 0

        target = signal.strength * self.max_position_pct * portfolio_value
        qty    = int(target / price)
        log.info("[SIZING] %s target=$%.0f price=$%.2f qty=%d",
                 signal.symbol, target, price, qty)
        return qty if signal.strength > 0.3 else 0

    # ── Order submission ──────────────────────────────────────────────────
    async def _submit_order(self, symbol: str, side: str, qty: int, signal: Signal) -> None:
        try:
            order = self._api.submit_order(
                symbol         = symbol,
                qty            = qty,
                side           = side,
                type           = "market",
                time_in_force  = "day",
            )
            log.info("[ORDER] %s %d %s @ market | id=%s | %s",
                     side.upper(), qty, symbol, order.id, signal.reason)

            self._order_log.append(OrderRecord(
                timestamp        = datetime.now(),
                symbol           = symbol,
                side             = side,
                qty              = qty,
                order_type       = "market",
                signal_strength  = signal.strength,
                order_id         = order.id,
                status           = "submitted",
                reason           = signal.reason,
            ))

        except APIError as e:
            log.error("[ORDER FAILED] %s %d %s: %s", side, qty, symbol, e)

    @staticmethod
    def _opposite_side(direction: Direction) -> str:
        return "sell" if direction == Direction.LONG else "buy"

    def get_order_log(self) -> list[OrderRecord]:
        return list(self._order_log)
