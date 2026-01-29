import os
import time
import threading
import pandas as pd
import ccxt
import requests
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNB/USDC' 
TRADE_AMOUNT_CURRENCY = 3.5 
LEVERAGE = 20
PROFIT_GOAL = 3.0 # Короткий тейк для работы в канале
LOOKBACK_MINUTES = 60 # За какой период искать Хай и Лоу

stats = {"cycles": 0, "profit": 0.0}

exchange = ccxt.binance({
    'apiKey': os.environ.get('BINANCE_API_KEY'),
    'secret': os.environ.get('BINANCE_API_SECRET'),
    'options': {'defaultType': 'future'},
    'enableRateLimit': True
})

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
        except: pass

def get_channel_extrema():
    try:
        # Берем данные за последние 60 минут
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=LOOKBACK_MINUTES)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        
        local_max = df['h'].max()
        local_min = df['l'].min()
        return local_max, local_min
    except:
        return None, None

def bot_worker():
    global stats
    send_tg(f"🏛️ *ВЕРСИЯ 7.0:* Работа от границ канала ({LOOKBACK_MINUTES} мин).")
    
    while True:
        try:
            all_positions = exchange.fetch_positions([SYMBOL])
            active_ps = [p for p in all_positions if float(p.get('contracts', 0)) > 0]
            
            if not active_ps:
                l_max, l_min = get_channel_extrema()
                ticker = exchange.fetch_ticker(SYMBOL)
                curr_p = float(ticker['last'])
                
                if l_max and l_min:
                    side = None
                    # Если цена у верхней границы или выше
                    if curr_p >= l_max:
                        side = "SHORT"
                        reason = f"Пик канала: {l_max}"
                    # Если цена у нижней границы или ниже
                    elif curr_p <= l_min:
                        side = "LONG"
                        reason = f"Дно канала: {l_min}"

                    if side:
                        raw_qty = (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p
                        qty = float(exchange.amount_to_precision(SYMBOL, raw_qty))
                        
                        exchange.cancel_all_orders(SYMBOL)
                        
                        if side == "SHORT":
                            exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                            tp_p = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                            exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_p, params={'positionSide': 'SHORT'})
                        else:
                            exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                            tp_p = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                            exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_p, params={'positionSide': 'LONG'})
                        
                        send_tg(f"🚀 *ВХОД {side}*\n{reason}\nЦена: `{curr_p}`\nТейк: `{tp_p}`")
                        stats["cycles"] += 1
            
        except Exception as e:
            if "429" in str(e): time.sleep(60)
            else: time.sleep(10)
        
        time.sleep(30)

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
