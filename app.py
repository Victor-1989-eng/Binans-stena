import os, requests, time
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ V14.5 (С ФИЛЬТРОМ ОБЪЕМА) ---
SYMBOL = 'ZECUSDC'
LEVERAGE = 20
QTY_USDC = 5       
WALL_SIZE = 1000   
AGGREGATION_RANGE = 0.20 
MIN_5M_VOLUME = 1500  # Минимум 1500 ZEC должно проторговаться за 5 минут для входа
PROFIT_TO_UNLOCK = 0.0025 
ACTIVATION_PNL = 0.0065   
CALLBACK_RATE = 0.0025    

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

def check_volume(client):
    """Проверяет объем торгов за последние 5 минут"""
    klines = client.futures_klines(symbol=SYMBOL, interval=KLINE_INTERVAL_1MINUTE, limit=5)
    total_vol = sum(float(k[5]) for k in klines) # Суммируем Volume
    return total_vol

def find_best_wall(data, range_val, target_vol):
    best_price, max_vol = None, 0
    for i in range(len(data)):
        price = float(data[i][0])
        current_sum = sum(float(item[1]) for item in data if abs(float(item[0]) - price) <= range_val)
        if current_sum > max_vol:
            max_vol, best_price = current_sum, price
    return best_price, max_vol if max_vol >= target_vol else (None, 0)

@app.route('/')
def run_bot():
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    try:
        # 0. ПРОВЕРКА ОБЪЕМА (ДЛЯ НОВЫХ СДЕЛОК)
        vol_5m = check_volume(client)
        
        pos_info = client.futures_position_information(symbol=SYMBOL)
        active_l = next((p for p in pos_info if p['positionSide'] == 'LONG' and float(p['positionAmt']) != 0), None)
        active_s = next((p for p in pos_info if p['positionSide'] == 'SHORT' and float(p['positionAmt']) != 0), None)
        curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        # 1. РАЗБЛОКИРОВКА (всегда работает)
        if active_l and active_s:
            pnl_l = (curr_p - float(active_l['entryPrice'])) / float(active_l['entryPrice'])
            pnl_s = (float(active_s['entryPrice']) - curr_p) / float(active_s['entryPrice'])
            if pnl_l >= PROFIT_TO_UNLOCK or pnl_s >= PROFIT_TO_UNLOCK:
                side_to_close = 'SHORT' if pnl_l >= PROFIT_TO_UNLOCK else 'LONG'
                act_close = active_s if side_to_close == 'SHORT' else active_l
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY if side_to_close == 'SHORT' else SIDE_SELL, 
                                            positionSide=side_to_close, type=ORDER_TYPE_MARKET, quantity=abs(float(act_close['positionAmt'])))
                send_tg(f"🔓 *ZEC*: Раскрыл замок. Оставил тренд.")
                return "Unlocked"

        # 2. ТРЕЙЛИНГ (всегда работает)
        if (active_l or active_s) and not (active_l and active_s):
            # ... (логика трейлинга остается прежней)
            # [Для краткости оставим её внутри твоего кода]
            pass

        # 3. ВХОД (ТОЛЬКО ЕСЛИ ОБЪЕМ ВЫШЕ MIN_5M_VOLUME)
        if not active_l and not active_s:
            if vol_5m < MIN_5M_VOLUME:
                return f"Wait. Low Volume: {round(vol_5m)} ZEC/5m", 200
            
            depth = client.futures_order_book(symbol=SYMBOL, limit=50)
            bid_p, bid_v = find_best_wall(depth['bids'], AGGREGATION_RANGE, WALL_SIZE)
            ask_p, ask_v = find_best_wall(depth['asks'], AGGREGATION_RANGE, WALL_SIZE)

            if (bid_p and curr_p <= bid_p + 0.15) or (ask_p and curr_p >= ask_p - 0.15):
                qty = round((QTY_USDC * LEVERAGE) / curr_p, 3)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_BUY, positionSide='LONG', type=ORDER_TYPE_MARKET, quantity=qty)
                client.futures_create_order(symbol=SYMBOL, side=SIDE_SELL, positionSide='SHORT', type=ORDER_TYPE_MARKET, quantity=qty)
                send_tg(f"🔒 *ZEC*: Замок! Объем рынка: {round(vol_5m)} ZEC/5m")
                return "Hedge Entry"

        return f"Scan. Vol: {round(vol_5m)}. Price: {curr_p}"
    except Exception as e:
        return f"Error: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
