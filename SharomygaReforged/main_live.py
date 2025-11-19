import os
from dotenv import load_dotenv
from binance.client import Client

# === Sharo imports ===
from core.data import load_data
from core.models import load_models
from core.predict import get_predictions

# === env ===
load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")
if not api_key or not api_secret:
    raise RuntimeError("API keys missing")

client = Client(api_key, api_secret)

def get_usdt_balance():
    balances = client.futures_account_balance()
    usdt = next((x for x in balances if x["asset"] == "USDT"), None)
    return usdt["balance"] if usdt else None

def main():
    bal = get_usdt_balance()
    print("USDT futures balance:", bal)

    df_up, df_down = load_data()
    nf_up, nf_down = load_models()
    preds = get_predictions(nf_up, nf_down, df_up, df_down)

    print("\n=== Model Predictions (UP / DOWN) ===")
    for _, row in preds.tail(20).iterrows():
        up = row.get("up_pred", None)
        down = row.get("down_pred", None)
        print(f"{row['symbol']:7} {row['ds']}  up={up:.6f}  down={down:.6f}")

if __name__ == "__main__":
    main()
