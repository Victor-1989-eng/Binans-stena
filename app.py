import os
import requests
import time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- ИТОГОВЫЕ НАСТРОЙКИ ---
SYMBOL = 'BNBUSDC'  
LEVERAGE = 75
WALL_SIZE = 900     
RANGE_MAX = 0.015
AGGREGATION = 0.5
STATS_FILE = "stats_v2.txt"

BE_LEVEL = 0.003    # Безубыток при +0.3%
# ------------------

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

def update_stats(profit, trade_id):
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: f.write(f"0,0.0,{trade_id}")
    with open(STATS_FILE, "r") as f:
        content = f.read().strip().split(",")
        count, total_profit = int(content[0]), float(content[1])
        last_id = content[2] if len(content) > 2 else ""

    if str(trade_id) != last_id:
        count += 1
        total_profit += profit
        with open(STATS_FILE, "w") as f:
            f.write(f"{count},{total_profit},{trade_id}")
        if count % 10 == 0:
            res = "🟢 ПРОФИТ" if total_profit > 0 else "🔴 УБЫТОК"
            send_tg(f"📊 *ИТОГ 10 СДЕЛОК*: `{total_profit:.2f} USDC` ({res})")

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

def open_trade(client, side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        try: client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        except: pass

        # Ставка 1 USDC (Плечо 75х)
        qty = round(75 / price, 3) 
        price = round(price, 2)
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # Вход лимитом
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='LIMIT',
            timeInForce='GTC', quantity=qty, price=str(price))
        
        # SL 0.3%, TP 0.9%
        stop_p = round(price * 0.997 if side == "LONG" else price * 1.003, 2)
        take_p = round(price * 1.009 if side == "LONG" else price * 0.991, 2)
        
        # Тейк лимитом (Maker)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT',
            timeInForce='GTC', price=str(take_p), quantity=qty, reduceOnly=True)
        
        # Стоп лимитом (Maker)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP',
            stopPrice=str(stop_p), price=str(stop_p), quantity=qty, reduceOnly=True)
        
        send_tg(f"⚡️ *ВХОД {side}* (1 USDC)\n🛡 SL: `{stop_p}` | 🎯 TP: `{take_p}` (Maker)")
    except Exception as e:
        send_tg(f"❌ Ошибка открытия: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            p = active_pos[0]
            amt, entry_p = float(p['positionAmt']), float(p['entryPrice'])
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            side_long = amt > 0
            
            # --- ЗАЩИТА ОТ ПРОСКАЛЬЗЫВАНИЯ (Panic Sell) ---
            # Если цена ушла за стоп более чем на 0.1%, а позиция еще жива - закрываем по рынку
            stop_level = entry_p * 0.997 if side_long else entry_p * 1.003
            if (side_long and curr_p < stop_level * 0.999) or (not side_long and curr_p > stop_level * 1.001):
                client.futures_create_order(symbol=SYMBOL, side='SELL' if side_long else 'BUY', 
                                          type='MARKET', quantity=abs(amt), reduceOnly=True)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                send_tg("⚠️ *Аварийное закрытие!* Лимитный стоп не сработал.")
                return "Emergency Closed"

            # --- ЛОГИКА БЕЗУБЫТКА ---
            pnl_pct = (curr_p - entry_p) / entry_p if side_long else (entry_p - curr_p) / entry_p
            if pnl_pct >= BE_LEVEL:
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                for o in orders:
                    if o['type'] == 'STOP' and abs(float(o['stopPrice']) - entry_p) > 0.05:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=o['orderId'])
                        client.futures_create_order(symbol=SYMBOL, side='SELL' if side_long else 'BUY', 
                            type='STOP', stopPrice=str(round(entry_p, 2)), price=str(round(entry_p, 2)), 
                            quantity=abs(amt), reduceOnly=True)
                        send_tg(f"🛡 *БЕЗУБЫТОК*: Лимит переставлен на `{round(entry_p, 2)}`")
            
            return f"В сделке. PNL: {pnl_pct*100:.2f}%"

        # --- ПОИСК СДЕЛКИ ---
        open_orders = client.futures_get_open_orders(symbol=SYMBOL)
        if not open_orders:
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades:
                last_t = trades[0]
                if float(last_t['realizedPnl']) != 0:
                    update_stats(float(last_t['realizedPnl']), last_t['id'])
            
            depth = client.futures_order_book(symbol=SYMBOL, limit=100)
            bid_p, _ = find_whale_walls(depth['bids'])
            ask_p, _ = find_whale_walls(depth['asks'])

            if bid_p and ask_p:
                gap, curr_p = (ask_p - bid_p) / bid_p, float(depth['bids'][0][0])
                if gap <= RANGE_MAX:
                    if curr_p <= bid_p + (ask_p - bid_p) * 0.2:
                        open_trade(client, "LONG", bid_p + 0.15)
                    elif curr_p >= ask_p - (ask_p - bid_p) * 0.2:
                        open_trade(client, "SHORT", ask_p - 0.15)

        return "Сканирую BNB/USDC (Maker Mode + Safety)..."
    except Exception as e:
        return f"Ошибка: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
