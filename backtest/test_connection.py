import os
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

load_dotenv()

api = tradeapi.REST(
    key_id=os.environ["ALPACA_KEY"],
    secret_key=os.environ["ALPACA_SECRET"],
    base_url="https://paper-api.alpaca.markets",
)

account = api.get_account()
print(f"Status:       {account.status}")
print(f"Cash:         ${float(account.cash):,.2f}")
print(f"Buying power: ${float(account.buying_power):,.2f}")
print(f"Portfolio:    ${float(account.portfolio_value):,.2f}")
