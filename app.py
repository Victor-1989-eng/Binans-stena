import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 20        # Плечо (рекомендую х20 для старта)
QTY_USD = 1          # Сумма маржи (твои $5)
WALL_SIZE = 850      # Размер стенки кита в BNB
AGGREGATION = 0.3    # Группировка ордеров в стакане
STATS_FILE = "stats_v2.txt"

BE_LEVEL = 0.003     # Перенос в безубыток при +0.3%
MAX_TIME = 3600      # Макс. время сделки (1 час)

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

def get_global_trend(client):
    bars = client.futures_klines(symbol=SYMBOL, interval='1w', limit=2)
    return "UP" if float(bars[-1][4]) > float(bars[-1][1]) else "DOWN"

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def execute_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        qty = round((QTY_USD * LEVERAGE) / curr_p, 2)
        
        order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        close_side = SIDE_SELL if side == "LONG" else SIDE_BUY
        
        # 1. Вход лимитным ордером (чуть впереди стенки)
        client.futures_create_order(symbol=SYMBOL, side=order_side, type=ORDER_TYPE_LIMIT, 
                                    timeInForce=TIME_IN_FORCE_GTC, quantity=qty, price=str(round(price, 2)))
        
        # 2. Стоп-лосс (0.4%) и Тейк-профит (0.6%)
        sl = round(price * 0.996 if side == "LONG" else price * 1.004, 2)
        tp = round(price * 1.006 if side == "LONG" else price * 0.994, 2)
        
        client.futures_create_order(symbol=SYMBOL, side=close_side, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(sl), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type=ORDER_TYPE_LIMIT, price=str(tp), quantity=qty, timeInForce=TIME_IN_FORCE_GTC, reduceOnly=True)
        
        send_tg(f"🐳 *СТЕНКА НАЙДЕНА!* Вход {side} от `{price}`\n🎯 TP: `{tp}` | 🛡 SL: `{sl}`")
    except Exception as e: send_tg(f"❌ Ошибка входа: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active:
            p = active[0]
            amt, entry_p = float(p['positionAmt']), float(p['entryPrice'])
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            trade_time = int(p['updateTime']) / 1000
            
            # Выход по времени
            if (time.time() - trade_time) > MAX_TIME:
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if amt > 0 else SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=abs(amt), reduceOnly=True)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                send_tg("⏰ Закрыто по времени (1 час)")
                return "Closed by time"

            # Безубыток
            pnl = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            if pnl >= BE_LEVEL:
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                for o in orders:
                    if o['type'] == ORDER_TYPE_STOP_MARKET and float(o['stopPrice']) != entry_p:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=o['orderId'])
                        client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL if amt > 0 else SIDE_BUY, type=ORDER_TYPE_STOP_MARKET, stopPrice=str(entry_p), closePosition=True)
                        send_tg("🛡 Безубыток активирован")
            return f"В сделке. PNL: {pnl*100:.2f}%"

        # Поиск входа
        trend = get_global_trend(client)
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        bid_p, _ = find_whale_walls(depth['bids'])
        ask_p, _ = find_whale_walls(depth['asks'])
        curr_p = float(depth['bids'][0][0])

        if trend == "UP" and bid_p and curr_p <= bid_p + 0.5:
            execute_trade(client, "LONG", bid_p + 0.1)
        elif trend == "DOWN" and ask_p and curr_p >= ask_p - 0.5:
            execute_trade(client, "SHORT", ask_p - 0.1)

        return f"Мониторинг. Тренд: {trend} | Стенки: B:{bid_p} A:{ask_p}"
    except Exception as e: return f"Error: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
