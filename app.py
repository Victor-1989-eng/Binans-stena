import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ TRAP & FLIP V7 ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50        # Оптимально для перевороту
QTY_BNB = 0.10       # Початковий об'єм
WALL_SIZE = 800      # Твоя налаштування агресивного пошуку
OFFSET_PCT = 0.001   # Вхід трохи відступивши від стіни
TP_PCT = 0.008       # Тейк 0.8%
SL_PCT = 0.006       # Стоп 0.6% (тут спрацює ПЕРЕВЕРТЕНЬ)
FLIP_MULT = 2        # Множник об'єму при перевороті (0.5 -> 1.0)

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

def open_flip_trade(client, side, entry_p):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        
        # Визначаємо напрямки
        if side == "LONG":
            order_side, flip_side = 'BUY', 'SELL'
            tp_p = round(entry_p * (1 + TP_PCT), 2)
            sl_p = round(entry_p * (1 - SL_PCT), 2) # Точка перевороту
        else:
            order_side, flip_side = 'SELL', 'BUY'
            tp_p = round(entry_p * (1 - TP_PCT), 2)
            sl_p = round(entry_p * (1 + SL_PCT), 2) # Точка перевороту

        # 1. Основний вхід (Снайпер)
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        
        # 2. Основний Тейк-профіт
        client.futures_create_order(symbol=SYMBOL, side=flip_side, type='LIMIT', 
                                    price=str(tp_p), quantity=QTY_BNB, timeInForce='GTC', reduceOnly=True)
        
        # 3. ПЕРЕВЕРТЕНЬ (Ордер, який закриє мінус і відкриє плюс у зворотний бік)
        # Ставимо STOP_MARKET з подвійним об'ємом (не reduceOnly!)
        client.futures_create_order(
            symbol=SYMBOL, side=flip_side, type='STOP_MARKET',
            stopPrice=str(sl_p), quantity=QTY_BNB * FLIP_MULT
        )

        send_tg(f"🎯 *ВХІД {side} від стіни*\n💰 Тейк: `{tp_p}`\n🛡 Перевертень на: `{sl_p}` (Об'єм x{FLIP_MULT})")
    except Exception as e:
        send_tg(f"❌ Помилка входу: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No API Keys"

    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            amt = float(active_pos[0]['positionAmt'])
            pnl = float(active_pos[0]['unRealizedProfit'])
            return f"В грі! Позиція: {amt} BNB. PNL: {pnl}$"

        # Очищення перед пошуком
        client.futures_cancel_all_open_orders(symbol=SYMBOL)

        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_wall = find_walls(depth['bids'])
        ask_wall = find_walls(depth['asks'])

        # Логіка входу від стіни (Снайпер)
        if bid_wall and curr_p <= bid_wall * (1 + OFFSET_PCT):
            open_flip_trade(client, "LONG", curr_p)
            return f"Зайшов у LONG від стіни {bid_wall}"

        if ask_wall and curr_p >= ask_wall * (1 - OFFSET_PCT):
            open_flip_trade(client, "SHORT", curr_p)
            return f"Зайшов у SHORT від стіни {ask_wall}"

        return f"Ціна: {curr_p}. Стіни поруч не бачу (WALL > {WALL_SIZE})"
    except Exception as e:
        return f"Помилка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
