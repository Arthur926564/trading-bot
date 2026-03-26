"""
TFA Trading Bot — main entry point
"""

import asyncio
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

from data.market_data import MarketDataFeed
from strategy.signal import SignalGenerator, Direction
from execution.order_manager import OrderManager
from monitoring.portfolio import Portfolio, Position

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def run():
    symbols = ["AAPL", "MSFT", "SPY"]

    # Alpaca connection
    api = tradeapi.REST(
        key_id=os.environ["ALPACA_KEY"],
        secret_key=os.environ["ALPACA_SECRET"],
        base_url="https://paper-api.alpaca.markets",
    )

    account = api.get_account()
    real_cash = float(account.cash)
    log.info("Real Alpaca balance: $%.2f", real_cash)

    # Components
    feed = MarketDataFeed(symbols=symbols)
    strategy = SignalGenerator(
            threshold=0.0,
            min_volatility=0.0,
            cooldown=60
        )
    portfolio = Portfolio(initial_cash=real_cash)
    alpaca_positions = api.list_positions()
    for p in alpaca_positions:
        portfolio._positions[p.symbol] = Position(
            symbol=p.symbol,
            qty=int(p.qty),
            avg_entry_price=float(p.avg_entry_price),
            current_price=float(p.current_price)
        )
        log.info("Loaded position: %s qty=%d entry=%.2f",
                 p.symbol, int(p.qty), float(p.avg_entry_price))


    portfolio.load_from_alpaca(alpaca_positions)
    orders = OrderManager(portfolio=portfolio, max_position_pct=0.20)

    log.info("Starting TFA trading bot — paper mode")

    # Load history
    feed.load_history(days=30)
    log.info("History loaded, starting live stream...")

    last_bar_time = time.time()
    last_logged_minute = None

    # Watchdog
    async def watchdog():
        while True:
            await asyncio.sleep(120)
            if time.time() - last_bar_time > 120:
                log.warning("No bar received in 2 minutes — restarting bot")
                raise Exception("Watchdog timeout")

    asyncio.create_task(watchdog())

    # Main loop
    async for bar in feed.stream_bars(interval="1min"):
        last_bar_time = time.time()

        symbol = bar.symbol
        print(bar.prices)
        portfolio.update_prices(bar.prices)

        history = feed.get_price_series(symbol)
        has_position = portfolio.has_position(symbol)

        signals = strategy.generate(symbol, history, has_position)

        print(signals)
        for signal in signals:
            log.info(
                "[SIGNAL] %s %s strength=%.2f %s",
                signal.direction.value,
                signal.symbol,
                signal.strength,
                signal.reason,
            )
            await orders.handle_signal(signal)

        # Log every 5 minutes (no spam)
        now = datetime.now()
        if now.minute % 5 == 0 and now.minute != last_logged_minute:
            portfolio = Portfolio(initial_cash=real_cash)

            portfolio.log_status()
            last_logged_minute = now.minute


if __name__ == "__main__":
    asyncio.run(run())
