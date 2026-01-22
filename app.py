import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ TRAP & FLIP V7.2 (ИСПРАВЛЕННЫЙ) ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50
QTY_BNB = 0.50
WALL_SIZE = 1200      # Увеличили, чтобы не ловить мелкий шум
OFFSET_PCT = 0.0015   # Вход при приближении на 0.15%
TP_PCT = 0.012        # Тейк 1.2% (целимся в нормальное движение)
SL_PCT = 0.009        # Стоп 0.9% (даем цене пространство, чтобы не было "пилы")
FLIP_MULT = 1.8       # Коэффициент реверса (0.5 -> 0.9 BNB)

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
        
        # Исправление ошибки Precision (округляем до 2 знаков)
        entry_p = round(entry_p, 2)
        
        if side == "LONG":
            order_side, flip_side = 'BUY', 'SELL'
            tp_p = round(entry_p * (1 + TP_PCT), 2)
            sl_p = round(entry_p * (1 - SL_PCT), 2)
        else:
            order_side, flip_side = 'SELL', 'BUY'
            tp_p = round(entry_p * (1 - TP_PCT), 2)
            sl_p = round(entry_p * (1 + SL_PCT), 2)

        # 1. Основной вход
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        
        # 2. Тейк-профит (LIMIT)
        client.futures_create_order(symbol=SYMBOL, side=flip_side, type='LIMIT', 
                                    price=str(tp_p), quantity=QTY_BNB, timeInForce='GTC', reduceOnly=True)
        
        # 3. Стоп-Перевертыш (STOP_MARKET)
        flip_qty = round(QTY_BNB * FLIP_MULT, 2)
        client.futures_create_order(
            symbol=SYMBOL, side=flip_side, type='STOP_MARKET',
            stopPrice=str(sl_p), quantity=flip_qty
        )

        send_tg(f"🎯 *ВХОД {side}*\n💰 Тейк: `{tp_p}`\n🔄 Реверс: `{sl_p}` (Объем: {flip_qty})")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "No API Keys"
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            amt = float(active_pos[0]['positionAmt'])
            entry_price = float(active_pos[0]['entryPrice'])
            pnl = active_pos[0]['unRealizedProfit']
            
            # --- ПРОВЕРКА НАЛИЧИЯ ТЕЙКА (ДЛЯ РЕВЕРСА) ---
            open_orders = client.futures_get_open_orders(symbol=SYMBOL)
            has_tp = any(o['type'] == 'LIMIT' for o in open_orders)
            
            if not has_tp:
                tp_side = 'SELL' if amt > 0 else 'BUY'
                # Ставим Тейк относительно цены входа
                tp_price = round(entry_price * (1 + TP_PCT), 2) if amt > 0 else round(entry_price * (1 - TP_PCT), 2)
                
                client.futures_create_order(
                    symbol=SYMBOL, side=tp_side, type='LIMIT', 
                    price=str(tp_price), quantity=abs(round(amt, 2)), timeInForce='GTC', reduceOnly=True
                )
                send_tg(f"🩹 *Тейк восстановлен!* Для позиции {amt} BNB на `{tp_price}`")

            return f"В игре! Позиция: {amt} BNB. PNL: {pnl}$"

        # Если позиции нет — чистим старье и ищем новые стены
        client.futures_cancel_all_open_orders(symbol=SYMBOL)
        
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_wall = find_walls(depth['bids'])
        ask_wall = find_walls(depth['asks'])

        if bid_wall and curr_p <= bid_wall * (1 + OFFSET_PCT):
            open_flip_trade(client, "LONG", curr_p)
            return f"Зашел в LONG от {bid_wall}"

        if ask_wall and curr_p >= ask_wall * (1 - OFFSET_PCT):
            open_flip_trade(client, "SHORT", curr_p)
            return f"Зашел в SHORT от {ask_wall}"

        return f"Мониторю... Цена: {curr_p}. Стен нет (WALL > {WALL_SIZE})"
    except Exception as e:
        return f"Ошибка цикла: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
