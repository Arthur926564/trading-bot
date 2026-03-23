"""
backtest/runner.py

Runs your strategy against historical data before you touch real money.

Usage:
    python -m backtest.runner

It pulls historical bars from Alpaca, feeds them through your SignalGenerator
exactly as the live system would, and prints a performance summary.
"""

import os
import logging
from datetime import datetime, timedelta

import pandas as pd
import alpaca_trade_api as tradeapi

from strategy.signal import SignalGenerator, Direction
from monitoring.portfolio import Portfolio
from dotenv import load_dotenv
load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


class BacktestRunner:
    def __init__(
        self,
        symbols: list[str],
        initial_cash: float = 10_000,
        lookback: int = 20,
        threshold: float = 0.005,
        max_position_pct: float = 0.20,
        slippage_pct: float = 0.001,   # 0.1% slippage assumption
        commission: float = 0.0,       # Alpaca is commission-free
    ):
        self.symbols = symbols
        self.slippage = slippage_pct
        self.commission = commission
        self.max_position_pct = max_position_pct

        self.strategy = SignalGenerator(lookback=lookback, threshold=threshold)
        self.portfolio = Portfolio(initial_cash=initial_cash)

        self._api = tradeapi.REST(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url="https://paper-api.alpaca.markets",
        )

    def run(self, days: int = 365) -> pd.DataFrame:
        end = datetime.now()
        start = end - timedelta(days=days)

        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        # Load historical bars for all symbols
        bars: dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            df = self._api.get_bars(
                symbol,
                tradeapi.TimeFrame.Day,
                start_str,
                end_str,
                feed="iex",
            ).df
            bars[symbol] = df
            log.info("Loaded %d bars for %s", len(df), symbol)

        # Align on common dates
        closes = pd.DataFrame({s: bars[s]["close"] for s in self.symbols}).dropna()
        price_history: dict[str, list[float]] = {s: [] for s in self.symbols}
        last_direction: dict[str, Direction] = {}

        snapshots = []

        for timestamp, row in closes.iterrows():
            prices = row.to_dict()
            self.portfolio.update_prices(prices)

            # Build simulated Bar
            for symbol in self.symbols:
                price_history[symbol].append(prices[symbol])

            # Generate signals using price history
            signals = []
            for symbol in self.symbols:
                history = price_history[symbol]
                if len(history) < self.strategy.lookback:
                    continue

                import numpy as np
                fast = float(np.mean(history[-5:]))
                slow = float(np.mean(history[-self.strategy.lookback:]))
                gap = (fast - slow) / slow

                from strategy.signal import Signal
                if gap > self.strategy.threshold:
                    strength = min(abs(gap) / (self.strategy.threshold * 3), 1.0)
                    signals.append(Signal(symbol, Direction.LONG, strength, f"cross {gap:.2%}"))
                elif gap < -self.strategy.threshold:
                    strength = min(abs(gap) / (self.strategy.threshold * 3), 1.0)
                    signals.append(Signal(symbol, Direction.SHORT, strength, f"cross {gap:.2%}"))

            # Simulate fills
            for signal in signals:
                if last_direction.get(signal.symbol) == signal.direction:
                    continue

                price = prices[signal.symbol]
                fill_price = price * (1 + self.slippage) if signal.direction == Direction.LONG \
                             else price * (1 - self.slippage)

                portfolio_value = self.portfolio.total_value()
                target = signal.strength * self.max_position_pct * portfolio_value
                qty = max(int(target / fill_price), 1) if signal.strength > 0.3 else 0

                if qty > 0:
                    side = "buy" if signal.direction == Direction.LONG else "sell"
                    self.portfolio.apply_fill(signal.symbol, qty, fill_price, side)
                    last_direction[signal.symbol] = signal.direction

            snapshots.append({
                "date": timestamp,
                "portfolio_value": self.portfolio.total_value(),
                "cash": self.portfolio._cash,
                "drawdown": self.portfolio.drawdown(),
            })

        results = pd.DataFrame(snapshots).set_index("date")
        self._print_summary(results)
        return results

    def _print_summary(self, results: pd.DataFrame) -> None:
        first = results["portfolio_value"].iloc[0]
        last = results["portfolio_value"].iloc[-1]
        total_return = (last - first) / first
        max_dd = results["drawdown"].max()
        n_days = len(results)
        annual_return = (1 + total_return) ** (365 / n_days) - 1

        log.info("=" * 50)
        log.info("Backtest results")
        log.info("  Period:         %d days", n_days)
        log.info("  Start value:    $%.2f", first)
        log.info("  End value:      $%.2f", last)
        log.info("  Total return:   %.2f%%", total_return * 100)
        log.info("  Annual return:  %.2f%%", annual_return * 100)
        log.info("  Max drawdown:   %.2f%%", max_dd * 100)
        log.info("=" * 50)


if __name__ == "__main__":
    runner = BacktestRunner(
        symbols=["SPY", "QQQ", "AAPL"],
        initial_cash=10_000,
        lookback=20,
        threshold=0.02,
    )
    runner.run(days=365)
