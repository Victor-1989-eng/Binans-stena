import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ АНТИ-СНАЙПЕРА (РЕВЕРС) ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50        # Снизили с 75 до 50 для выживания
QTY_BNB = 0.10       # Объем
WALL_SIZE = 600     # Ищем средние стены, которые легко "прогрызть"
PROBOY_DIST = 0.001  # Заходим, когда до стены осталось 0.1% цены
TP_PCT = 0.004       # Забираем быстрый импульс 0.4%
SL_PCT = 0.006       # Стоп 0.6% (с другой стороны стены)
LAST_CHECK_TIME = 0

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

def find_walls(data):
    for p, q in data:
        if float(q) >= WALL_SIZE: return float(p)
    return None

def open_reverse_trade(client, side, curr_p):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        
        # РЕВЕРС ЛОГИКА: 
        # Видим стену снизу (LONG сигнал) -> Открываем SHORT (на пробой)
        # Видим стену сверху (SHORT сигнал) -> Открываем LONG (на пробой)
        if side == "SHORT_PROBOY": # Снесли стену ASK
            order_side, close_side = 'BUY', 'SELL'
            tp_p = round(curr_p * (1 + TP_PCT), 2)
            sl_p = round(curr_p * (1 - SL_PCT), 2)
        else: # Снесли стену BID
            order_side, close_side = 'SELL', 'BUY'
            tp_p = round(curr_p * (1 - TP_PCT), 2)
            sl_p = round(curr_p * (1 + SL_PCT), 2)

        # 1. Вход по рынку на импульсе
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        
        # 2. Тейк-профит лимиткой
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', 
                                    price=str(tp_p), quantity=QTY_BNB, timeInForce='GTC', reduceOnly=True)
        
        # 3. Стоп-лосс
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', 
                                    stopPrice=str(sl_p), closePosition=True)

        send_tg(f"🔄 *АНТИ-СНАЙПЕР: РЕВЕРС {order_side}*\n🚀 Вход на пробой стены!\n🎯 Тейк: `{tp_p}`\n🛡 Стоп: `{sl_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка реверса: {e}")

@app.route('/')
def run_bot():
    global LAST_CHECK_TIME
    now = time.time()
    if now - LAST_CHECK_TIME < 10: return "Жду импульс..."
    LAST_CHECK_TIME = now

    client = get_binance_client()
    if not client: return "No API Keys"

    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        if any(float(p['positionAmt']) != 0 for p in pos):
            return "В сделке..."

        # Чистим старые ордера, если сделка закрылась
        client.futures_cancel_all_open_orders(symbol=SYMBOL)

        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_wall = find_walls(depth['bids'])
        ask_wall = find_walls(depth['asks'])

        # Если цена подошла к стене BUY (снизу) — открываем SHORT на пробой
        if bid_wall and (curr_p - bid_wall) / bid_wall <= PROBOY_DIST:
            open_reverse_trade(client, "LONG_PROBOY", curr_p)
            return "Ломаю стену BUY (Вход в SHORT)"

        # Если цена подошла к стене SELL (сверху) — открываем LONG на пробой
        if ask_wall and (ask_wall - curr_p) / ask_wall <= PROBOY_DIST:
            open_reverse_trade(client, "SHORT_PROBOY", curr_p)
            return "Ломаю стену SELL (Вход в LONG)"

        return f"Слежу за BNB: {curr_p}"
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
