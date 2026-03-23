"""
data/market_data.py

Handles both:
  - Historical data (for backtesting / seeding lookback windows)
  - Live streaming bars via Alpaca WebSocket

Requires: pip install alpaca-trade-api pandas
Set env vars: ALPACA_KEY, ALPACA_SECRET
"""

import os
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
from typing import AsyncIterator

import pandas as pd
import alpaca_trade_api as tradeapi
from alpaca_trade_api.stream import Stream

log = logging.getLogger(__name__)


@dataclass
class Bar:
    """One OHLCV bar for all tracked symbols."""
    timestamp: datetime
    prices: dict[str, float]        # symbol → close price
    ohlcv: dict[str, dict]          # symbol → {open, high, low, close, volume}


class MarketDataFeed:
    def __init__(self, symbols: list[str], lookback: int = 50):
        self.symbols = symbols
        self.lookback = lookback

        self._api = tradeapi.REST(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url="https://paper-api.alpaca.markets",  # paper trading
        )

        # Rolling price history per symbol — used by strategy for indicators
        self.history: dict[str, deque] = {
            s: deque(maxlen=lookback) for s in symbols
        }

        self._pending_bars: dict[str, dict] = {}  # buffer until all symbols arrive

    # ------------------------------------------------------------------
    # Historical data — call once at startup to pre-fill the lookback window
    # ------------------------------------------------------------------
    def load_history(self, days: int = 30) -> dict[str, pd.DataFrame]:
        end = datetime.now()
        start = end - timedelta(days=days)
        
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")
        
        result = {}
        for symbol in self.symbols:
            df = self._api.get_bars(
                symbol,
                tradeapi.TimeFrame.Day,
                start_str,
                end_str,
                adjustment="raw",
                feed="iex",
            ).df
            result[symbol] = df
            for close in df["close"].tolist():
                self.history[symbol].append(close)

        log.info("Loaded %d days of history for %s", days, self.symbols)
        return result


    def get_price_series(self, symbol: str) -> list[float]:
        """Returns the rolling close price list for a symbol (for indicator math)."""
        return list(self.history[symbol])

    # ------------------------------------------------------------------
    # Live streaming — async generator, yields a Bar when all symbols updated
    # ------------------------------------------------------------------

    async def stream_bars(self, interval: str = "1min") -> AsyncIterator[Bar]:
        """
        Async generator that yields a Bar each time all symbols have a fresh update.

        Usage:
            async for bar in feed.stream_bars():
                do_something(bar)
        """
        queue: asyncio.Queue[Bar] = asyncio.Queue()

        stream = Stream(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url="https://stream.data.alpaca.markets",
            data_feed="iex",  # free tier; use "sip" for paid consolidated feed
        )

        async def _on_bar(bar_data):
            symbol = bar_data.symbol
            self._pending_bars[symbol] = {
                "open": bar_data.open,
                "high": bar_data.high,
                "low": bar_data.low,
                "close": bar_data.close,
                "volume": bar_data.volume,
            }
            self.history[symbol].append(bar_data.close)

            # Only emit a Bar once we have a fresh update for every symbol
            if set(self._pending_bars.keys()) == set(self.symbols):
                bar = Bar(
                    timestamp=datetime.now(),
                    prices={s: self._pending_bars[s]["close"] for s in self.symbols},
                    ohlcv=dict(self._pending_bars),
                )
                self._pending_bars.clear()
                await queue.put(bar)

        for symbol in self.symbols:
            stream.subscribe_bars(_on_bar, symbol)

        # Run the WebSocket in the background
        asyncio.create_task(stream._run_forever())

        while True:
            yield await queue.get()
