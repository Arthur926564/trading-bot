"""
strategy/signal.py

This is YOUR layer — swap out the example logic with your actual strategy.
The interface contract is simple:
  - Input:  a Bar (latest prices + OHLCV for all symbols)
  - Output: list of Signal objects (can be empty if no action needed)

The example below implements a simple moving-average crossover.
Replace `compute_signal()` with your own model.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from datetime import time as dtime

import numpy as np

if TYPE_CHECKING:
    from data.market_data import Bar, MarketDataFeed

log = logging.getLogger(__name__)


class Direction(Enum):
    LONG  = "long"
    SHORT = "short"
    FLAT  = "flat"   # close / no position


@dataclass
class Signal:
    symbol: str
    direction: Direction
    strength: float       # 0.0 → 1.0, used for position sizing
    reason: str = ""      # human-readable, good for logs and audit trail


class SignalGenerator:
    """
    Example: dual moving-average crossover.

    Replace compute_signal() with your model's logic.
    The rest of the plumbing (history management, per-symbol loop) stays the same.
    """

    def __init__(self, lookback: int = 20, threshold: float = 0.01):
        self.lookback = lookback
        self.threshold = threshold          # min % gap between MAs to trigger signal


    def on_bar(self, bar: "Bar") -> list[Signal]:
        """Called once per bar. Returns signals for all symbols worth acting on."""
        signals = []
        for symbol, price in bar.prices.items():
            sig = self._compute_signal(symbol, price, bar)
            if sig is not None:
                signals.append(sig)
        return signals

    # ------------------------------------------------------------------
    # YOUR STRATEGY LIVES HERE
    # Replace everything below with your own logic.
    # You receive: symbol name, latest close price, full Bar object
    # You return: a Signal or None
    # ------------------------------------------------------------------

    def _compute_signal(self, symbol: str, price: float, bar: "Bar") -> Signal | None:
        """
        Example: MA crossover. Fast MA crosses above slow MA → LONG, below → SHORT.

        To plug in your own model:
          1. Access bar.ohlcv[symbol] for full OHLCV data
          2. Use your model's feature computation here
          3. Return Signal(symbol, Direction.LONG/SHORT/FLAT, strength=0.0-1.0)
        """
        # Needs to be injected at construction or passed via bar.history
        # For the example we use a synthetic price path
        prices = self._get_history(symbol, bar)
        if len(prices) < self.lookback:
            return None  # not enough history yet

        fast_ma = np.mean(prices[-5:])
        slow_ma = np.mean(prices[-self.lookback:])
        gap = (fast_ma - slow_ma) / slow_ma

        if gap > self.threshold:
            strength = min(abs(gap) / (self.threshold * 3), 1.0)
            return Signal(symbol, Direction.LONG,  strength, f"MA cross +{gap:.2%}")

        if gap < -self.threshold:
            strength = min(abs(gap) / (self.threshold * 3), 1.0)
            return Signal(symbol, Direction.SHORT, strength, f"MA cross {gap:.2%}")

        return None  # in the dead zone, do nothing

    def _get_history(self, symbol: str, bar: "Bar") -> list[float]:
        """
        In production, this would read from feed.history[symbol].
        For simplicity here, bar.ohlcv history is accessed via a shared feed reference.
        Wire this up in main.py by passing feed.get_price_series(symbol).
        """
        # Placeholder — replace with feed.get_price_series(symbol) in main.py
        return []
