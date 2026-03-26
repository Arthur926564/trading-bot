"""
data/market_data.py
"""

import os
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import deque
from typing import AsyncIterator

import pandas as pd
import alpaca_trade_api as tradeapi
from alpaca_trade_api.stream import Stream

log = logging.getLogger(__name__)


@dataclass
class Bar:
    """One OHLCV bar for a single symbol."""
    timestamp: datetime
    symbol: str
    prices: dict[str, float]
    ohlcv: dict[str, dict]


class MarketDataFeed:
    def __init__(self, symbols: list[str], lookback: int = 50):
        self.symbols = symbols
        self.lookback = lookback

        self._api = tradeapi.REST(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url="https://paper-api.alpaca.markets",
        )

        self.history: dict[str, deque] = {
            s: deque(maxlen=lookback) for s in symbols
        }

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
        return list(self.history[symbol])

    async def stream_bars(self, interval: str = "1min") -> AsyncIterator[Bar]:
        queue: asyncio.Queue[Bar] = asyncio.Queue()

        stream = Stream(
            key_id=os.environ["ALPACA_KEY"],
            secret_key=os.environ["ALPACA_SECRET"],
            base_url="https://stream.data.alpaca.markets",
            data_feed="iex",
        )

        async def _on_bar(bar_data):
            symbol = bar_data.symbol
            self.history[symbol].append(bar_data.close)

            # Emit immediately for each symbol — no waiting for others
            bar = Bar(
                timestamp=datetime.now(),
                symbol=symbol,
                prices={symbol: bar_data.close},
                ohlcv={symbol: {
                    "open": bar_data.open,
                    "high": bar_data.high,
                    "low": bar_data.low,
                    "close": bar_data.close,
                    "volume": bar_data.volume,
                }},
            )
            await queue.put(bar)

        for symbol in self.symbols:
            stream.subscribe_bars(_on_bar, symbol)

        asyncio.create_task(stream._run_forever())

        while True:
            yield await queue.get()
