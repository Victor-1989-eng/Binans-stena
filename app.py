import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ HUNTER 3.0 ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50
QTY_BNB = 0.10          # Увеличенный объем для профита
WALL_SIZE = 800        # Ищем только серьезных китов
REJECTION_PCT = 0.0015  # Вход только ПОСЛЕ отскока от пика на 0.15%
TP_LIMIT_PCT = 0.007    # Лимитка на +0.7% (быстрый выход Maker)
STOP_LOSS_PCT = 0.009   # Стоп 0.9% (даем цене подышать после прокола)
CALLBACK_RATE = 1.0     # Трейлинг оставляем как страховку
LAST_CHECK_TIME = 0

# Состояния для логики "Hunter"
PENDING_WALL = None     # Цена стены, которую "прокололи"
PEAK_PRICE = 0          # Максимальный прокол для расчета отскока

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

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= 0.4])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, entry_p):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # 1. Вход по рынку (на подтвержденном отскоке)
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        
        # 2. УМНЫЙ ВЫХОД: Лимитка на +0.7% (Берет профит без комиссии Тейкера)
        tp_p = round(entry_p * 1.007 if side == "LONG" else entry_p * 0.993, 2)
        client.futures_create_order(
            symbol=SYMBOL, side=close_side, type='LIMIT', 
            quantity=QTY_BNB, price=str(tp_p), timeInForce='GTC', reduceOnly=True
        )

        # 3. ЗАЩИТНЫЙ СТОП: 0.9% от точки входа
        sl_p = round(entry_p * 0.991 if side == "LONG" else entry_p * 1.009, 2)
        client.futures_create_order(
            symbol=SYMBOL, side=close_side, type='STOP_MARKET', 
            stopPrice=str(sl_p), closePosition=True
        )

        send_tg(f"🎯 *HUNTER ВХОД {side}* по `{entry_p}`\n💰 Лимитка: `{tp_p}`\n🛡 Стоп: `{sl_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка Hunter-входа: {e}")

@app.route('/')
def run_bot():
    global LAST_CHECK_TIME, PENDING_WALL, PEAK_PRICE
    now = time.time()
    if now - LAST_CHECK_TIME < 5: # Охотник должен проверять чаще (раз в 5 сек)
        return "Сканирую импульс..."
    
    LAST_CHECK_TIME = now
    client = get_binance_client()
    if not client: return "API Keys Missing"

    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]

        if not active_pos:
            # Очистка, если вышли из сделки
            if PENDING_WALL:
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                PENDING_WALL = None
                PEAK_PRICE = 0

            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            depth = client.futures_order_book(symbol=SYMBOL, limit=100)
            
            bid_p, bid_v = find_whale_walls(depth['bids']) # Стены LONG
            ask_p, ask_v = find_whale_walls(depth['asks']) # Стены SHORT

            # ЛОГИКА ОХОТЫ ЗА LONG (отскок от стены снизу)
            if bid_p and curr_p <= bid_p: # Цена коснулась или пробила стену вниз
                PENDING_WALL = bid_p
                if PEAK_PRICE == 0 or curr_p < PEAK_PRICE: PEAK_PRICE = curr_p
            
            if PENDING_WALL and curr_p >= PEAK_PRICE * (1 + REJECTION_PCT):
                open_trade(client, "LONG", curr_p)
                return "Hunter зашел в LONG"

            # ЛОГИКА ОХОТЫ ЗА SHORT (отскок от стены сверху)
            if ask_p and curr_p >= ask_p: # Цена коснулась или пробила стену вверх
                PENDING_WALL = ask_p
                if PEAK_PRICE == 0 or curr_p > PEAK_PRICE: PEAK_PRICE = curr_p
            
            if PENDING_WALL and curr_p <= PEAK_PRICE * (1 - REJECTION_PCT):
                open_trade(client, "SHORT", curr_p)
                return "Hunter зашел в SHORT"

            return f"Цена: {curr_p}. Стены: L:{bid_p} / S:{ask_p}"

        return "Слежу за позицией..."
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
