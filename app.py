import os
import requests
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ (Берутся из Environment Variables на Render) ---
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

SYMBOL = 'BNBUSDT'
LEVERAGE = 75
QTY_BNB = 0.24      # Объем позиции (~3$ с плечом 75)
WALL_SIZE = 700     # Минимальный объем стены кита
RANGE_MAX = 0.012   # Максимальный канал (1.2%)
STATS_FILE = "stats.txt"

client = Client(API_KEY, API_SECRET)

def send_tg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: pass

def update_stats(profit):
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: f.write("0,0.0")
    
    with open(STATS_FILE, "r") as f:
        data = f.read().split(",")
        count = int(data[0]) + 1
        total_profit = float(data[1]) + profit
    
    with open(STATS_FILE, "w") as f:
        f.write(f"{count},{total_profit}")
    
    if count % 10 == 0:
        status = "🟢 ПРОФИТ" if total_profit > 0 else "🔴 УБЫТОК"
        send_tg(f"📊 *ИТОГ СЕРИИ: 10 СДЕЛОК*\n\nРезультат: `{total_profit:.2f} USDT`\nСтатус: {status}")

def check_position_status():
    pos = client.futures_position_information(symbol=SYMBOL)
    for p in pos:
        if float(p['positionAmt']) == 0:
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades:
                pnl = float(trades[0]['realizedPnl'])
                if pnl != 0:
                    send_tg(f"🏁 *СДЕЛКА ЗАКРЫТА*\nРезультат: `{pnl:.2f} USDT`")
                    update_stats(pnl)

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= 0.3])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        
        order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        client.futures_create_order(
            symbol=SYMBOL, side=order_side, type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC, quantity=QTY_BNB, price=str(round(price, 2))
        )
        
        # Стоп 0.7%, Тейк 1.1%
        stop_p = round(price * 0.993 if side == "LONG" else price * 1.007, 2)
        take_p = round(price * 1.011 if side == "LONG" else price * 0.989, 2)
        
        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == "LONG" else SIDE_BUY, 
                                     type=ORDER_TYPE_STOP_MARKET, stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if side == "LONG" else SIDE_BUY, 
                                     type=ORDER_TYPE_LIMIT, timeInForce=TIME_IN_FORCE_GTC, 
                                     price=str(take_p), quantity=QTY_BNB, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {side}*\n💰 Цена: `{price}`\n🛡 Stop: `{stop_p}`\n🎯 Take: `{take_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка: {e}")

@app.route('/')
def run_bot():
    check_position_status()
    pos = client.futures_position_information(symbol=SYMBOL)
    if any(float(p['positionAmt']) != 0 for p in pos):
        return "Position is active..."

    depth = client.futures_order_book(symbol=SYMBOL, limit=100)
    bid_p, bid_v = find_whale_walls(depth['bids'])
    ask_p, ask_v = find_whale_walls(depth['asks'])

    if bid_p and ask_p:
        gap = (ask_p - bid_p) / bid_p
        curr_p = float(depth['bids'][0][0])

        if gap <= RANGE_MAX:
            if curr_p <= bid_p + (ask_p - bid_p) * 0.2:
                open_trade("LONG", bid_p + 0.15)
                return "Opening Long"
            elif curr_p >= ask_p - (ask_p - bid_p) * 0.2:
                open_trade("SHORT", ask_p - 0.15)
                return "Opening Short"

    return "Searching for whale walls..."

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
