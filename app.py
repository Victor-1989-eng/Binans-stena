import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ПОВЫШЕННОЙ БЕЗОПАСНОСТИ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50
QTY_BNB = 0.20       # Оптимальный объем для твоей маржи
WALL_SIZE = 1900     # Ищем только "бетонные" стены
RANGE_MAX = 0.002    # Вход только впритык к стене (0.2%)
CALLBACK_RATE = 1.0  # Трейлинг-стоп 1% (минимизируем шум)
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

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= 0.5])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, entry_price, target_wall_price=None):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # 1. Вход по рынку
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        time.sleep(2) # Даем API Binance время обновить баланс

        # 2. Трейлинг-стоп (Активация при движении на 0.5% или у стены)
        activation_p = target_wall_price if target_wall_price else round(entry_price * 1.005 if side == "LONG" else entry_price * 0.995, 2)
        
        client.futures_create_order(
            symbol=SYMBOL, side=close_side, type='TRAILING_STOP_MARKET',
            quantity=QTY_BNB, callbackRate=CALLBACK_RATE,
            activationPrice=str(activation_p), reduceOnly=True
        )
        
        # 3. Обычный защитный СТОП-ЛОСС (0.6% - чуть дальше от "бритвы")
        stop_p = round(entry_price * 0.994 if side == "LONG" else entry_price * 1.006, 2)
        client.futures_create_order(
            symbol=SYMBOL, side=close_side, type='STOP_MARKET', 
            stopPrice=str(stop_p), closePosition=True
        )
        
        send_tg(f"✅ *ВХОД {side}* (Стена: {WALL_SIZE})\n📈 Трейлинг после: `{activation_p}`\n🛡 Стоп: `{stop_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

@app.route('/')
def run_bot():
    global LAST_CHECK_TIME
    now = time.time()
    
    # Защита от суеты (раз в 50 сек)
    if now - LAST_CHECK_TIME < 50:
        return f"Ожидание... Осталось {int(50 - (now - LAST_CHECK_TIME))} сек."
    
    LAST_CHECK_TIME = now
    client = get_binance_client()
    if not client: return "API Keys Missing", 500

    try:
        # ПРОВЕРКА ПОЗИЦИИ И ОЧИСТКА МУСОРА
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if not active_pos:
            # Если позиции нет, а ордера висят — чистим всё!
            open_orders = client.futures_get_open_orders(symbol=SYMBOL)
            if open_orders:
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                send_tg("🧹 Позиция закрыта. Лишние ордера удалены автоматически.")
            
            # Ищем новую сделку
            depth = client.futures_order_book(symbol=SYMBOL, limit=100)
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            
            bid_p, bid_v = find_whale_walls(depth['bids'])
            ask_p, ask_v = find_whale_walls(depth['asks'])

            if bid_p and (curr_p - bid_p) / bid_p <= RANGE_MAX:
                open_trade(client, "LONG", curr_p, target_wall_price=ask_p)
                return "Открываю LONG"
                
            elif ask_p and (ask_p - curr_p) / ask_p <= RANGE_MAX:
                open_trade(client, "SHORT", curr_p, target_wall_price=bid_p)
                return "Открываю SHORT"

            return f"Цена: {curr_p}. Жду стену {WALL_SIZE}+"

        return "В сделке. Трейлинг работает."
        
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
