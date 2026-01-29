import os
import time
import threading
import pandas as pd
import pandas_ta as ta
import ccxt
import requests
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNB/USDC' 
TRADE_AMOUNT_CURRENCY = 3.5 
LEVERAGE = 20
PROFIT_GOAL = 3.5 # Чуть уменьшил тейк для более быстрых выходов на 1м

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

def get_market_sentiment():
    try:
        # Запрашиваем минутные свечи
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=50)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        
        # Считаем быстрый RSI
        df['rsi'] = ta.rsi(df['c'], length=14)
        current_rsi = df['rsi'].iloc[-1]
        
        # Границы для 1-минутного таймфрейма (более жесткие)
        if current_rsi >= 75:
            return "SHORT", f"RSI: {round(current_rsi, 1)} (ПЕРЕКУПЛЕННОСТЬ)"
        elif current_rsi <= 25:
            return "LONG", f"RSI: {round(current_rsi, 1)} (ПЕРЕПРОДАННОСТЬ)"
        
        return None, f"RSI: {round(current_rsi, 1)} (ОЖИДАНИЕ)"
    except: return None, "Ошибка данных"

def bot_worker():
    global stats
    send_tg("🎯 *ВЕРСИЯ 6.3 (Снайпер 1m):* Вход только на разворотах RSI.")
    
    while True:
        try:
            all_positions = exchange.fetch_positions([SYMBOL])
            active_ps = [p for p in all_positions if float(p.get('contracts', 0)) > 0]
            
            if not active_ps:
                side, reason = get_market_sentiment()
                
                if side:
                    ticker = exchange.fetch_ticker(SYMBOL)
                    curr_p = float(ticker['last'])
                    
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
                    
                    send_tg(f"🚀 *ВХОД {side}*\nПричина: {reason}\nЦена: `{curr_p}`\nТейк: `{tp_p}`")
                    stats["cycles"] += 1
                
            # Если позиция есть - бот просто спит и ждет тейка
        except Exception as e:
            if "429" in str(e): time.sleep(60)
            else: time.sleep(10)
        
        time.sleep(30) # Проверка каждые 30 секунд

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
