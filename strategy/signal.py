import time
import numpy as np
from enum import Enum
import logging


class Direction(Enum):
    LONG = "long"
    FLAT = "flat"


class Signal:
    def __init__(self, symbol: str, direction: Direction, strength: float, reason: str):
        self.symbol = symbol
        self.direction = direction
        self.strength = strength
        self.reason = reason


class SignalGenerator:
    def __init__(
        self,
        lookback: int = 20,
        trend_window: int = 50,
        threshold: float = 0.0015,
        volatility_window: int = 20,
        min_volatility: float = 0.0005,
        cooldown: int = 300,
        monitor_interval: int = 300,  # 5 minutes
    ):
        self.lookback = lookback
        self.trend_window = trend_window
        self.threshold = threshold
        self.volatility_window = volatility_window
        self.min_volatility = min_volatility
        self.cooldown = cooldown
        self.monitor_interval = monitor_interval

        self.last_trade_time = {}
        self.last_monitor_time = {}

    def _can_trade(self, symbol: str) -> bool:
        return time.time() - self.last_trade_time.get(symbol, 0) > self.cooldown

    def _should_monitor(self, symbol: str) -> bool:
        return time.time() - self.last_monitor_time.get(symbol, 0) > self.monitor_interval

    def generate(self, symbol: str, history: list[float], has_position: bool):
        if len(history) < max(self.lookback + 1, self.trend_window, self.volatility_window):
            return []

        # Moving averages (previous vs now)
        fast_prev = np.mean(history[-6:-1])
        slow_prev = np.mean(history[-self.lookback-1:-1])

        fast_now = np.mean(history[-5:])
        slow_now = np.mean(history[-self.lookback:])

        # Trend filter
        trend_ma = np.mean(history[-self.trend_window:])
        price = history[-1]

        trend_up = price > trend_ma
        trend_down = price < trend_ma

        # Momentum
        gap = (fast_now - slow_now) / slow_now
        strong_momentum = abs(gap) > self.threshold

        # Volatility
        returns = np.diff(history[-self.volatility_window:])
        volatility = np.std(returns)
        enough_vol = volatility > self.min_volatility

        # Cross detection
        bull_cross = fast_prev <= slow_prev and fast_now > slow_now
        bear_cross = fast_prev >= slow_prev and fast_now < slow_now

        signals = []

        # ENTRY
        if (
            bull_cross
            and trend_up
            and enough_vol
            and not has_position
        ):
            self.last_trade_time[symbol] = time.time()
            signals.append(
                Signal(symbol, Direction.LONG, 1.0, "Filtered MA bull cross")
            )

        # EXIT
        elif bear_cross and has_position:
            self.last_trade_time[symbol] = time.time()
            signals.append(
                Signal(symbol, Direction.FLAT, 1.0, "Filtered MA bear cross")
            )

        # MONITORING (always report every `monitor_interval`)
        if self._should_monitor(symbol):
            self.last_monitor_time[symbol] = time.time()
            signals.append(
                Signal(
                    symbol,
                    Direction.LONG if has_position else Direction.FLAT,
                    min(abs(gap) / (self.threshold * 3 + 1e-8), 1.0),  # avoid divide by zero
                    f"Monitor: price={price:.2f}, gap={gap:.4f}, trend={'up' if trend_up else 'down'}, vol={volatility:.4f}"
                )
            )
        return signals
