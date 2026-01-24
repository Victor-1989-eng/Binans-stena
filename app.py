import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ V15.2 "SMART DOLLAR" ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 20
DOLLAR_AMOUNT = 1.0    # Тратим ровно 6 USDC на сделку (при балансе 7.5 это ок)
STATS_FILE = "stats_zec.txt"

# ПАРАМЕТРЫ ЦЕЛЕЙ
TP_LEVEL = 0.105      # Тейк-профит 10.5%
SL_LEVEL = 0.020      # Стоп-лосс 2.0%
TRAIL_STEP = 0.010    # Подтягиваем стоп каждый 1% роста

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
        if count % 3 == 0: # Отчет каждые 3 сделки
            send_tg(f"📊 *ОТЧЕТ*: Сделок: `{count}`, Профит: `{total_profit:.2f} USDC`")

def get_market_data(client):
    """Анализ волатильности и расчет динамического порога стен"""
    try:
        # Анализ активности за час
        klines = client.futures_klines(symbol=SYMBOL, interval='5m', limit=12)
        avg_vol = sum(float(k[5]) for k in klines) / 12
        curr_vol = float(klines[-1][5])
        
        # Анализ плотности стакана
        depth = client.futures_order_book(symbol=SYMBOL, limit=50)
        all_q = [float(q) for p, q in depth['bids']] + [float(q) for p, q in depth['asks']]
        dynamic_wall = (sum(all_q) / len(all_q)) * 3.5
        dynamic_wall = max(120, min(700, dynamic_wall)) # Адаптивные границы
        
        return curr_vol, dynamic_wall, avg_vol, depth
    except: return 0, 200, 0, None

def open_trade(client, side, price):
    try:
        # 1. Расчет объема в монетах исходя из суммы в долларах
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        qty = round(DOLLAR_AMOUNT / curr_p, 1)
        if qty < 0.1: qty = 0.1

        # 2. Настройка режимов (One-Way, Isolated, Leverage)
        try: client.futures_change_position_mode(dualSidePosition=False)
        except: pass
        try: client.futures_change_margin_type(symbol=SYMBOL, marginType='ISOLATED')
        except: pass
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

        order_side, close_side = ('BUY', 'SELL') if side == "LONG" else ('SELL', 'BUY')
        
        # 3. Вход по рынку
        client.futures_create_order(symbol=SYMBOL, side=order_side, type='MARKET', quantity=qty)
        
        # 4. Стоп и Тейк
        price_f = round(curr_p, 2)
        stop_p = round(price_f * (1 - SL_LEVEL) if side == "LONG" else price_f * (1 + SL_LEVEL), 2)
        take_p = round(price_f * (1 + TP_LEVEL) if side == "LONG" else price_f * (1 - TP_LEVEL), 2)
        
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=str(stop_p), closePosition=True)
        client.futures_create_order(symbol=SYMBOL, side=close_side, type='LIMIT', timeInForce='GTC', price=str(take_p), quantity=qty, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {side}* на `${DOLLAR_AMOUNT}`\nОбъем: `{qty} ZEC` по `{price_f}`\n🎯 Цель: `{take_p}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа на ${DOLLAR_AMOUNT}: {e}")

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        # Проверка текущих позиций
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = [p for p in pos if float(p['positionAmt']) != 0]
        
        if active_pos:
            p = active_pos[0]
            amt, entry_p = float(p['positionAmt']), float(p['entryPrice'])
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            pnl_pct = (curr_p - entry_p) / entry_p if amt > 0 else (entry_p - curr_p) / entry_p
            
            # --- СТУПЕНЧАТЫЙ ТРЕЙЛИНГ (Защита прибыли) ---
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

        # ЕСЛИ ПОЗИЦИИ НЕТ - ПРОВЕРЯЕМ СТАТИСТИКУ И ИЩЕМ ВХОД
        trades = client.futures_account_trades(symbol=SYMBOL, limit=1)
        if trades:
            last_t = trades[0]
            if float(last_t['realizedPnl']) != 0:
                update_stats(float(last_t['realizedPnl']), last_t['id'])

        # Анализ для входа
        curr_vol, wall_limit, avg_h_vol, depth = get_market_data(client)
        
        # Фильтр активности (25% от среднего за час)
        if curr_vol < (avg_h_vol * 0.25):
            return f"Рынок спит. Vol: {curr_vol:.1f} (нужно {avg_h_vol*0.25:.1f})"

        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        def find_walls(data, limit):
            for p, q in data:
                # Агрегация 0.60$ для ZEC (склеиваем близкие ордера)
                vol = sum(float(rq) for rp, rq in data if abs(float(rp) - float(p)) <= 0.60)
                if vol >= limit: return float(p), vol
            return None, 0

        bid_p, bid_v = find_walls(depth['bids'], wall_limit)
        ask_p, ask_v = find_walls(depth['asks'], wall_limit)

        # Логика входа от стен
        if bid_p and curr_p <= bid_p + 0.40:
            open_trade(client, "LONG", curr_p)
            return f"Входим в LONG. Стены: {bid_v:.0f}"
        
        if ask_p and curr_p >= ask_p - 0.40:
            open_trade(client, "SHORT", curr_p)
            return f"Входим в SHORT. Стены: {ask_v:.0f}"

        return f"Поиск. Планка: {wall_limit:.0f} ZEC. Vol: {curr_vol:.1f}"

    except Exception as e:
        return f"Ошибка системы: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
