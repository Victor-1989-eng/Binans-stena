import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ СИСТЕМЫ "SMART MEMORY" ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 20
QTY_ZEC = 1.0

# Параметры целей (твои 10.5% и ступенчатый стоп)
TP_LEVEL = 0.105
SL_LEVEL = 0.020
TRAIL_STEP = 0.010

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

def get_market_data(client):
    """Блок анализа рынка для авто-настройки"""
    try:
        # 1. Анализируем активность (объем за 5 минут)
        klines = client.futures_klines(symbol=SYMBOL, interval='5m', limit=12) # за последний час
        avg_vol = sum(float(k[5]) for k in klines) / 12
        curr_vol = float(klines[-1][5])
        
        # 2. Анализируем стакан для авто-определения WALL_SIZE
        depth = client.futures_order_book(symbol=SYMBOL, limit=50)
        all_orders = [float(q) for p, q in depth['bids']] + [float(q) for p, q in depth['asks']]
        avg_wall = sum(all_orders) / len(all_orders)
        
        # Динамический порог: в 3.5 раза больше среднего ордера
        dynamic_wall = avg_wall * 3.5
        # Но не меньше 150 и не больше 800
        dynamic_wall = max(150, min(800, dynamic_wall))
        
        return curr_vol, dynamic_wall, avg_vol, depth
    except: return 0, 300, 0, None

def open_trade(client, side, price):
    try:
        # 1. Сбрасываем режим позиции и маржи
        try: client.futures_change_position_mode(dualSidePosition=False)
        except: pass
        
        try: client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        except: pass # Если уже изолированная, просто идем дальше

        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        
        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # 2. Входим (объем 1.0 для теста стабильности)
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=1.0)
        
        price = round(price, 2)
        stop_p = round(price * (1 - SL_LEVEL) if side == "LONG" else price * (1 + SL_LEVEL), 2)
        take_p = round(price * (1 + TP_LEVEL) if side == "LONG" else price * (1 - TP_LEVEL), 2)
        
        # 3. Выставляем защитные ордера
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', timeInForce='GTC', price=str(take_p), quantity=1.0, reduceOnly=True)
        
        send_tg(f"✅ *УСПЕШНЫЙ ВХОД!* {side} по `{price}`\nЦель 10.5%: `{take_p}`\nМаржа под контролем.")
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
            # --- ЛОГИКА ТРЕЙЛИНГА (из прошлой версии) ---
            p = active_pos[0]
            amt, entry_p = float(p['positionAmt']), float(p['entryPrice'])
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            pnl_pct = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            
            steps = int(pnl_pct / TRAIL_STEP)
            if steps >= 1:
                trail_pnl = (steps - 1) * TRAIL_STEP
                new_stop = round(entry_p * (1 + trail_pnl) if amt > 0 else entry_p * (1 - trail_pnl), 2)
                
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                s_ord = next((o for o in orders if o['type'] == 'STOP_MARKET'), None)
                if s_ord and ((new_stop > float(s_ord['stopPrice']) if amt > 0 else new_stop < float(s_ord['stopPrice']))):
                    client.futures_cancel_order(symbol=SYMBOL, orderId=s_ord['orderId'])
                    client.futures_create_order(symbol=SYMBOL, side=('SELL' if amt > 0 else 'BUY'), type='STOP_MARKET', stopPrice=str(new_stop), closePosition=True)
            return f"В сделке. PNL: {pnl_pct*100:.2f}%"

        # --- АВТО-АНАЛИЗ ПЕРЕД ВХОДОМ ---
        curr_vol, wall_limit, avg_h_vol, depth = get_market_data(client)
        
        # Если текущий объем меньше 30% от среднего за час - рынок спит
        if curr_vol < (avg_h_vol * 0.3):
            return f"Сонный рынок. Vol: {curr_vol:.1f} (нужно {avg_h_vol*0.3:.1f})"

        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        
        # Ищем стены по динамическому лимиту dynamic_wall
        def find_walls(data, limit):
            for p, q in data:
                # Агрегация 0.60$
                vol = sum(float(rq) for rp, rq in data if abs(float(rp) - float(p)) <= 0.60)
                if vol >= limit: return float(p), vol
            return None, 0

        bid_p, bid_v = find_walls(depth['bids'], wall_limit)
        ask_p, ask_v = find_walls(depth['asks'], wall_limit)

        if bid_p and curr_p <= bid_p + 0.35:
            open_trade(client, "LONG", curr_p)
            return f"LONG от авто-стены {bid_v:.0f}"
        
        if ask_p and curr_p >= ask_p - 0.35:
            open_trade(client, "SHORT", curr_p)
            return f"SHORT от авто-стены {ask_v:.0f}"

        return f"Анализ: Стены < {wall_limit:.0f} ZEC. Рынок активен на {curr_vol:.1f}"

    except Exception as e:
        return f"Ошибка: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
