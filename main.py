"""
TFA Trading Bot — main entry point
"""
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from data.market_data import MarketDataFeed
from strategy.signal import SignalGenerator, Signal, Direction
from execution.order_manager import OrderManager
from monitoring.portfolio import Portfolio

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def run():
    symbols = ["AAPL", "MSFT", "SPY"]

    feed = MarketDataFeed(symbols=symbols)
    strategy = SignalGenerator(lookback=20, threshold=0.02)
    portfolio = Portfolio(initial_cash=100_000)  # match your Alpaca balance
    orders = OrderManager(portfolio=portfolio, max_position_pct=0.20)

    log.info("Starting TFA trading bot — paper mode")

    # Pre-fill price history before streaming starts
    feed.load_history(days=30)
    log.info("History loaded, starting live stream...")

    async for bar in feed.stream_bars(interval="1min"):
        # 1. Update portfolio with latest prices
        portfolio.update_prices(bar.prices)

        # 2. Generate signals — passing history explicitly
        signals = []
        for symbol in symbols:
            history = feed.get_price_series(symbol)
            if len(history) < strategy.lookback:
                continue

            fast_ma = float(np.mean(history[-5:]))
            slow_ma = float(np.mean(history[-strategy.lookback:]))
            gap = (fast_ma - slow_ma) / slow_ma

            if gap > strategy.threshold:
                strength = min(abs(gap) / (strategy.threshold * 3), 1.0)
                signals.append(Signal(symbol, Direction.LONG, strength, f"MA cross +{gap:.2%}"))
            elif gap < -strategy.threshold:
                signals.append(Signal(symbol, Direction.FLAT, 1.0, f"MA cross {gap:.2%}"))

        # 3. Execute signals
        for signal in signals:
            log.info("[SIGNAL] %s %s strength=%.2f %s",
                     signal.direction.value, signal.symbol, signal.strength, signal.reason)
            await orders.handle_signal(signal)

        # 4. Log status every 5 minutes
        if datetime.now().minute % 5 == 0:
            portfolio.log_status()


if __name__ == "__main__":
    asyncio.run(run())
