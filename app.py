import os
import requests
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 75
QTY_BNB = 0.24      # Объем (~3$ x 75)
WALL_SIZE = 500     # Порог входа (BNB)
RANGE_MAX = 0.015   # Коридор 1.5%
AGGREGATION = 0.5   # Радиус плотности
STATS_FILE = "stats.txt"

def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    return Client(api_key, api_secret) if api_key and api_secret else None

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def update_stats(profit):
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: f.write("0,0.0")
    with open(STATS_FILE, "r") as f:
        data = f.read().split(",")
        count, total = int(data[0]) + 1, float(data[1]) + profit
    with open(STATS_FILE, "w") as f:
        f.write(f"{count},{total}")
    if count % 10 == 0:
        res = "🟢 ПРОФИТ" if total > 0 else "🔴 УБЫТОК"
        send_tg(f"📊 *ИТОГ 10 СДЕЛОК*: `{total:.2f} USDT` ({res})")

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        client.futures_create_order(symbol=SYMBOL, side=order_side, type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC, quantity=QTY_BNB, price=str(round(price, 2)))
        
        # SL 0.7%, TP 1.1%
        stop_p = round(price * 0.993 if side == "LONG" else price * 1.007, 2)
        take_p = round(price * 1.011 if side == "LONG" else price * 0.989, 2)
        
        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == "LONG" else SIDE_BUY, 
                                     type=ORDER_TYPE_STOP_MARKET, stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == "LONG" else SIDE_BUY, 
                                     type=ORDER_TYPE_LIMIT, timeInForce=TIME_IN_FORCE_GTC, 
                                     price=str(take_p), quantity=QTY_BNB, reduceOnly=True)
        send_tg(f"🚀 *ВХОД {side}* по `{price}`\n🛡 SL: `{stop_p}` | 🎯 TP: `{take_p}`")
    except Exception as e:
        print(f"Trade Error: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No Keys", 500
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        has_pos = any(float(p['positionAmt']) != 0 for p in pos)
        
        if not has_pos:
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades:
                pnl = float(trades[0]['realizedPnl'])
                if pnl != 0: update_stats(pnl)
            
            depth = client.futures_order_book(symbol=SYMBOL, limit=100)
            bid_p, _ = find_whale_walls(depth['bids'])
            ask_p, _ = find_whale_walls(depth['asks'])

            if bid_p and ask_p:
                gap = (ask_p - bid_p) / bid_p
                curr_p = float(depth['bids'][0][0])
                if gap <= RANGE_MAX:
                    if curr_p <= bid_p + (ask_p - bid_p) * 0.2:
                        open_trade(client, "LONG", bid_p + 0.15)
                    elif curr_p >= ask_p - (ask_p - bid_p) * 0.2:
                        open_trade(client, "SHORT", ask_p - 0.15)

        return "OK"
    except Exception as e:
        return str(e), 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
