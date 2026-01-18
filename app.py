import os
import requests
import time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDT'
LEVERAGE = 75
QTY_BNB = 0.24
WALL_SIZE = 1000     
RANGE_MAX = 0.015
AGGREGATION = 0.5
STATS_FILE = "stats.txt"

# ПЛАН Б: Настройки защиты
BE_LEVEL = 0.005    # Безубыток при +0.5% профита
MAX_TIME = 3600     # Закрыть сделку через 60 минут (3600 сек)
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

def update_stats(profit):
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: f.write("0,0.0")
    with open(STATS_FILE, "r") as f:
        content = f.read().strip()
        data = content.split(",") if content else ["0", "0.0"]
        count, total = int(data[0]) + 1, float(data[1]) + profit
    with open(STATS_FILE, "w") as f:
        f.write(f"{count},{total}")
    if count % 10 == 0:
        res = "🟢 ПРОФИТ" if total > 0 else "🔴 УБЫТОК"
        send_tg(f"📊 *ИТОГ 10 СДЕЛОК*: `{total:.2f} USDT` ({res})")

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

        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='LIMIT',
            timeInForce='GTC', quantity=QTY_BNB, price=str(round(price, 2)))
        
        stop_p = round(price * 0.993 if side == "LONG" else price * 1.007, 2)
        take_p = round(price * 1.011 if side == "LONG" else price * 0.989, 2)
        
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET',
            stopPrice=str(stop_p), closePosition=True)
        
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT',
            timeInForce='GTC', price=str(take_p), quantity=QTY_BNB, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {side}* по `{price}`\n🛡 SL: `{stop_p}` | 🎯 TP: `{take_p}`")
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
            amt = float(p['positionAmt'])
            entry_p = float(p['entryPrice'])
            trade_time = int(p['updateTime']) / 1000
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            
            # 1. ТАЙМ-АУТ: Проверка времени (60 мин)
            if (time.time() - trade_time) > MAX_TIME:
                side = 'SELL' if amt > 0 else 'BUY'
                client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=abs(amt), reduceOnly=True)
                client.futures_cancel_all_open_orders(symbol=SYMBOL)
                send_tg("⏰ *План Б:* Выход по времени (сделка висит > 60 мин)")
                return "Закрыто по времени"

            # 2. БЕЗУБЫТОК: Перенос стопа
            pnl_pct = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            if pnl_pct >= BE_LEVEL:
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                for o in orders:
                    if o['type'] == 'STOP_MARKET' and float(o['stopPrice']) != entry_p:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=o['orderId'])
                        side = 'SELL' if amt > 0 else 'BUY'
                        client.futures_create_order(symbol=SYMBOL, side=side, type='STOP_MARKET',
                            stopPrice=str(entry_p), closePosition=True)
                        send_tg("🛡 *План Б:* Стоп перенесен в БЕЗУБЫТОК (+0.5% достигнуто)")
            
            return f"В сделке. PNL: {pnl_pct*100:.2f}%"

        # Если позиции нет — ищем вход
        open_orders = client.futures_get_open_orders(symbol=SYMBOL)
        if not open_orders:
            trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
            if trades:
                pnl = float(trades[0]['realizedPnl'])
                if pnl != 0: update_stats(pnl)
            
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

        return "Поиск китов..."
    except Exception as e:
        return f"Ошибка: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
