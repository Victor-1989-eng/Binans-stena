import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ V14.8 GLOBAL HUNTER (ZECUSDC) ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 20
QTY_ZEC = 1.2         # Объем в монетах
WALL_SIZE = 600       # Минимум ZEC в стакане для входа
AGGREGATION = 0.35    # Радиус суммирования стен ($)
MIN_5M_VOLUME = 200   # Активность рынка (ZEC за 5 мин)

# ПАРАМЕТРЫ ПРОФИТА И ТРЕЙЛИНГА
TP_LEVEL = 0.105      # Тейк-профит 10.5% (Главная цель)
SL_LEVEL = 0.020      # Начальный стоп 2.0% (чуть расширили для большой цели)
TRAIL_STEP = 0.010    # Шаг подтягивания стопа (каждый 1% профита)

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

def get_5m_volume(client):
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval='5m', limit=1)
        return float(klines[0][5])
    except: return 0

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # Вход по рынку
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_ZEC)
        
        # Расчет Стопа и Тейка
        price = round(price, 2)
        stop_p = round(price * (1 - SL_LEVEL) if side == "LONG" else price * (1 + SL_LEVEL), 2)
        take_p = round(price * (1 + TP_LEVEL) if side == "LONG" else price * (1 - TP_LEVEL), 2)
        
        # Выставляем ордера
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', timeInForce='GTC', price=str(take_p), quantity=QTY_ZEC, reduceOnly=True)
        
        send_tg(f"🚀 *ZEC: ВХОД В ОХОТУ {side}*\n💰 Вход: `{price}`\n🎯 Цель 10.5%: `{take_p}`\n🛡 Стоп 2%: `{stop_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка открытия ZEC: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No API Keys"
    
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        # --- ЛОГИКА СОПРОВОЖДЕНИЯ ПОЗИЦИИ ---
        if active_pos:
            p = active_pos[0]
            amt, entry_p = float(p['positionAmt']), float(p['entryPrice'])
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            
            pnl_pct = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            
            # Ступенчатый трейлинг
            steps_passed = int(pnl_pct / TRAIL_STEP) 
            if steps_passed >= 1:
                trail_pnl = (steps_passed - 1) * TRAIL_STEP
                new_stop_p = round(entry_p * (1 + trail_pnl) if amt > 0 else entry_p * (1 - trail_pnl), 2)

                orders = client.futures_get_open_orders(symbol=SYMBOL)
                stop_order = next((o for o in orders if o['type'] == 'STOP_MARKET'), None)
                
                if stop_order:
                    old_stop_p = float(stop_order['stopPrice'])
                    is_better = (new_stop_p > old_stop_p) if amt > 0 else (new_stop_p < old_stop_p)
                    
                    if is_better:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=stop_order['orderId'])
                        side = 'SELL' if amt > 0 else 'BUY'
                        client.futures_create_order(symbol=SYMBOL, side=side, type='STOP_MARKET', stopPrice=str(new_stop_p), closePosition=True)
                        send_tg(f"📈 *ZEC ТРЕЙЛИНГ:* Профит `{pnl_pct*100:.1f}%`. Стоп поднят до `+{trail_pnl*100:.0f}%` (`{new_stop_p}`)")

            return f"В сделке. PNL: {pnl_pct*100:.2f}%. Цель: 10.5%"

        # --- ЛОГИКА ПОИСКА ВХОДА ---
        vol_5m = get_5m_volume(client)
        if vol_5m < MIN_5M_VOLUME:
            return f"Рынок спит (Vol: {vol_5m:.1f})"

        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_p, bid_v = find_whale_walls(depth['bids'])
        ask_p, ask_v = find_whale_walls(depth['asks'])

        if bid_p and curr_p <= bid_p + 0.15:
            open_trade(client, "LONG", curr_p)
            return "Открываю LONG"

        if ask_p and curr_p >= ask_p - 0.15:
            open_trade(client, "SHORT", curr_p)
            return "Открываю SHORT"

        return f"Поиск. Цена: {curr_p}. Стен нет."
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
