import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ TRAP & FLIP V7.1 (С АВТО-ТЕЙКОМ) ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 50
QTY_BNB = 0.05
WALL_SIZE = 800
OFFSET_PCT = 0.001
TP_PCT = 0.008       # Тейк 0.8%
SL_PCT = 0.006       # Стоп 0.6% (точка переворота)
FLIP_MULT = 2        # Множник (0.5 -> 1.0)

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
        if side == "LONG":
            order_side, flip_side = 'BUY', 'SELL'
            tp_p = round(entry_p * (1 + TP_PCT), 2)
            sl_p = round(entry_p * (1 - SL_PCT), 2)
        else:
            order_side, flip_side = 'SELL', 'BUY'
            tp_p = round(entry_p * (1 - TP_PCT), 2)
            sl_p = round(entry_p * (1 + SL_PCT), 2)

        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_BNB)
        client.futures_create_order(symbol=SYMBOL, side=flip_side, type='LIMIT', 
                                    price=str(tp_p), quantity=QTY_BNB, timeInForce='GTC', reduceOnly=True)
        client.futures_create_order(symbol=SYMBOL, side=flip_side, type='STOP_MARKET',
                                    stopPrice=str(sl_p), quantity=QTY_BNB * FLIP_MULT)

        send_tg(f"🎯 *ВХОД {side}*\n💰 Тейк: `{tp_p}`\n🔄 Переворот на: `{sl_p}`")
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
            
            # --- БЛОК АВТО-ТЕЙКА ДЛЯ РЕВЕРСА ---
            open_orders = client.futures_get_open_orders(symbol=SYMBOL)
            # Проверяем, есть ли лимитка на закрытие (Тейк)
            has_tp = any(o['type'] == 'LIMIT' for o in open_orders)
            
            if not has_tp:
                # Если тейка нет (значит мы перевернулись), ставим его!
                tp_side = 'SELL' if amt > 0 else 'BUY'
                tp_price = round(entry_price * (1 + TP_PCT), 2) if amt > 0 else round(entry_price * (1 - TP_PCT), 2)
                
                client.futures_create_order(
                    symbol=SYMBOL, side=tp_side, type='LIMIT', 
                    price=str(tp_price), quantity=abs(amt), timeInForce='GTC', reduceOnly=True
                )
                send_tg(f"🩹 *Тейк восстановлен!* Для позиции {amt} BNB на цену `{tp_price}`")

            return f"В игре! Позиция: {amt} BNB. PNL: {active_pos[0]['unRealizedProfit']}$"

        # Очистка если нет позиции
        client.futures_cancel_all_open_orders(symbol=SYMBOL)
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        bid_wall = find_walls(depth['bids'])
        ask_wall = find_walls(depth['asks'])

        if bid_wall and curr_p <= bid_wall * (1 + OFFSET_PCT):
            open_flip_trade(client, "LONG", curr_p)
            return f"LONG от {bid_wall}"
        if ask_wall and curr_p >= ask_wall * (1 - OFFSET_PCT):
            open_flip_trade(client, "SHORT", curr_p)
            return f"SHORT от {ask_wall}"

        return f"Цена: {curr_p}. Стен нет."
    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
