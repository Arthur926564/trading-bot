import time
import numpy as np
from enum import Enum
import logging


class Direction(Enum):
    LONG = "long"
    FLAT = "flat"


class Signal:
    def __init__(self, symbol: str, direction: Direction, strength: float, reason: str, volatility=0.0):
        self.symbol = symbol
        self.direction = direction
        self.strength = strength
        self.reason = reason
        self.volatility = volatility


class SignalGenerator:
    def __init__(
        self,
        trend_window: int = 50,
        long_trend_window: int = 200,
        volatility_window: int = 20,
        rsi_period: int = 14,
        dip_lookback: int = 5,       # bars to measure the dip over
        min_dip_pct: float = 0.02,   # minimum 2% drop to qualify as a dip
        max_dip_pct: float = 0.08,   # ignore crashes >8% (panic, not dip)
        rsi_oversold: float = 45.0,  # RSI must be below this (price is weak)
        min_volatility: float = 0.005,
        monitor_interval: int = 300,
    ):
        self.trend_window      = trend_window
        self.long_trend_window = long_trend_window
        self.volatility_window = volatility_window
        self.rsi_period        = rsi_period
        self.dip_lookback      = dip_lookback
        self.min_dip_pct       = min_dip_pct
        self.max_dip_pct       = max_dip_pct
        self.rsi_oversold      = rsi_oversold
        self.min_volatility    = min_volatility
        self.monitor_interval  = monitor_interval

        self.last_monitor_time = {}

    def _should_monitor(self, symbol: str) -> bool:
        return time.time() - self.last_monitor_time.get(symbol, 0) > self.monitor_interval

    def _rsi(self, prices: np.ndarray) -> float:
        deltas = np.diff(prices[-(self.rsi_period + 1):])
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    def generate(self, symbol: str, history: list[float], has_position: bool):
        min_len = max(self.long_trend_window, self.trend_window,
                      self.volatility_window, self.rsi_period + 2,
                      self.dip_lookback + 1)
        if len(history) < min_len:
            return []

        prices = np.array(history, dtype=float)
        price  = prices[-1]

        # ── Trend filters ──────────────────────────────────────────────
        trend_ma      = np.mean(prices[-self.trend_window:])
        long_trend_ma = np.mean(prices[-self.long_trend_window:])

        bull_market   = price > long_trend_ma        # above 200-day MA
        uptrend       = trend_ma > long_trend_ma     # 50-day above 200-day (golden zone)

        # ── Dip measurement ───────────────────────────────────────────
        recent_high   = np.max(prices[-(self.dip_lookback + 1):-1])  # high BEFORE today
        dip_pct       = (recent_high - price) / recent_high           # how far we've fallen
        is_real_dip   = self.min_dip_pct <= dip_pct <= self.max_dip_pct

        # ── Momentum / RSI ────────────────────────────────────────────
        rsi           = self._rsi(prices)
        is_oversold   = rsi < self.rsi_oversold      # price weakened enough to be a dip

        # ── Volatility ────────────────────────────────────────────────
        log_returns   = np.diff(np.log(prices[-self.volatility_window:]))
        volatility    = np.std(log_returns)
        enough_vol    = volatility > self.min_volatility

        # ── Exit: price recovered to trend MA ─────────────────────────
        recovered     = price > trend_ma

        signals = []


        medium_momentum  = (prices[-1] - prices[-11]) / prices[-11]
        trend_still_up   = medium_momentum > -0.02


        # ── ENTRY: buy the dip inside an uptrend ──────────────────────
        if (
            bull_market
            and uptrend
            and is_real_dip
            and is_oversold
            and enough_vol
            and not has_position
        ):
            strength = min(dip_pct / self.max_dip_pct, 1.0)
            logging.info(
                f"{symbol} LONG | dip={dip_pct:.3f} rsi={rsi:.1f} "
                f"trend_ma={trend_ma:.2f} long_ma={long_trend_ma:.2f}"
            )
            signals.append(Signal(symbol, Direction.LONG, strength, f"Dip {dip_pct*100:.1f}% in uptrend", volatility))

        # ── EXIT: price recovered above trend MA ──────────────────────
        elif has_position and recovered:
            logging.info(f"{symbol} EXIT | recovered above trend_ma={trend_ma:.2f}")
            signals.append(Signal(symbol, Direction.FLAT, 1.0, "Recovered to trend MA"))

        # ── MONITOR ───────────────────────────────────────────────────
        if self._should_monitor(symbol):
            self.last_monitor_time[symbol] = time.time()
            signals.append(Signal(
                symbol,
                Direction.LONG if has_position else Direction.FLAT,
                0.0,
                f"Monitor | price={price:.2f} dip={dip_pct:.3f} "
                f"rsi={rsi:.1f} vol={volatility:.4f} bull={bull_market} trend={uptrend}"
            ))

        return signals

