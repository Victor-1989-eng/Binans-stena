import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ V15.1 (ZECUSDC) ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 20
QTY_ZEC = 1.0         # Базовый объем (1 монета)
STATS_FILE = "stats_zec.txt"

# ПАРАМЕТРЫ ЦЕЛЕЙ
TP_LEVEL = 0.105      # Тейк 10.5%
SL_LEVEL = 0.020      # Стоп 2.0%
TRAIL_STEP = 0.010    # Подтяжка стопа каждый 1%

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
        count, total_profit, last_id = int(content[0]), float(content[1]), content[2]
    
    if str(trade_id) != last_id:
        count += 1
        total_profit += profit
        with open(STATS_FILE, "w") as f: f.write(f"{count},{total_profit},{trade_id}")
        if count % 5 == 0:
            send_tg(f"📊 *ИТОГ 5 СДЕЛОК*: `{total_profit:.2f} USDC`")

def get_market_data(client):
    """Авто-анализ волатильности и стен"""
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval='5m', limit=12)
        avg_vol = sum(float(k[5]) for k in klines) / 12
        curr_vol = float(klines[-1][5])
        
        depth = client.futures_order_book(symbol=SYMBOL, limit=50)
        all_q = [float(q) for p, q in depth['bids']] + [float(q) for p, q in depth['asks']]
        dynamic_wall = (sum(all_q) / len(all_q)) * 3.5
        dynamic_wall = max(150, min(800, dynamic_wall))
        
        return curr_vol, dynamic_wall, avg_vol, depth
    except: return 0, 300, 0, None

def open_trade(client, side, price):
    try:
        # Настройка режима
        try: client.futures_change_position_mode(dualSidePosition=False)
        except: pass
        try: client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        except: pass
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # Вход по рынку для мгновенного исполнения
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=QTY_ZEC)
        
        price = round(price, 2)
        stop_p = round(price * (1 - SL_LEVEL) if side == "LONG" else price * (1 + SL_LEVEL), 2)
        take_p = round(price * (1 + TP_LEVEL) if side == "LONG" else price * (1 - TP_LEVEL), 2)
        
        # Стоп и Тейк
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', timeInForce='GTC', price=str(take_p), quantity=QTY_ZEC, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {side}* по `{price}`\n🎯 Цель: `{take_p}` (10.5%)")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

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
            pnl_pct = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            
            # --- СТУПЕНЧАТЫЙ ТРЕЙЛИНГ ---
            steps = int(pnl_pct / TRAIL_STEP)
            if steps >= 1:
                trail_pnl = (steps - 1) * TRAIL_STEP
                new_stop = round(entry_p * (1 + trail_pnl) if amt > 0 else entry_p * (1 - trail_pnl), 2)
                
                orders = client.futures_get_open_orders(symbol=SYMBOL)
                s_ord = next((o for o in orders if o['type'] == 'STOP_MARKET'), None)
                
                if s_ord:
                    old_stop = float(s_ord['stopPrice'])
                    is_better = (new_stop > old_stop) if amt > 0 else (new_stop < old_stop)
                    if is_better:
                        client.futures_cancel_order(symbol=SYMBOL, orderId=s_ord['orderId'])
                        client.futures_create_order(symbol=SYMBOL, side=('SELL' if amt > 0 else 'BUY'), 
                                                    type='STOP_MARKET', stopPrice=str(new_stop), closePosition=True)
                        send_tg(f"🛡 Стоп подтянут: `+{trail_pnl*100:.1f}%` (`{new_stop}`)")

            return f"В сделке ZEC. PNL: {pnl_pct*100:.2f}%"

        # ЕСЛИ ПОЗИЦИИ НЕТ - ИЩЕМ ВХОД
        # 1. Сначала проверяем последнюю сделку для статистики (как в твоем коде)
        trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
        if trades:
            last_t = trades[0]
            if float(last_t['realizedPnl']) != 0:
                update_stats(float(last_t['realizedPnl']), last_t['id'])

        # 2. Анализ рынка
        curr_vol, wall_limit, avg_h_vol, depth = get_market_data(client)
        
        if curr_vol < (avg_h_vol * 0.25): # Порог 25% от среднего
            return f"Рынок ZEC спит. Vol: {curr_vol:.1f}"

        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        def find_walls(data, limit):
            for p, q in data:
                # Агрегация 0.60$ для ZEC
                vol = sum(float(rq) for rp, rq in data if abs(float(rp) - float(p)) <= 0.60)
                if vol >= limit: return float(p), vol
            return None, 0

        bid_p, bid_v = find_walls(depth['bids'], wall_limit)
        ask_p, ask_v = find_walls(depth['asks'], wall_limit)

        if bid_p and curr_p <= bid_p + 0.35:
            open_trade(client, "LONG", curr_p)
            return f"Открываю LONG. Стена {bid_v:.0f}"
        
        if ask_p and curr_p >= ask_p - 0.35:
            open_trade(client, "SHORT", curr_p)
            return f"Открываю SHORT. Стена {ask_v:.0f}"

        return f"Сканирую. Планка: {wall_limit:.0f} ZEC. Активность: {curr_vol:.1f}"

    except Exception as e:
        return f"Ошибка: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
