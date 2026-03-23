import os
from dotenv import load_dotenv
load_dotenv()
import alpaca_trade_api as tradeapi

api = tradeapi.REST(
    key_id=os.environ["ALPACA_KEY"],
    secret_key=os.environ["ALPACA_SECRET"],
    base_url="https://paper-api.alpaca.markets",
)

api.close_all_positions()
print("All positions closed")
