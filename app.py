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
TRADE_AMOUNT_CURRENCY = 3.0 
LEVERAGE = 2
STEP = 2.0
PROFIT_GOAL = 4.0

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
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=50)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        rsi = ta.rsi(df['c'], length=14).iloc[-1]
        current_p = df['c'].iloc[-1]
        old_p = df['c'].iloc[-15]
        
        if rsi < 35: return "LONG", f"RSI Перепродан ({round(rsi,1)})"
        if rsi > 65: return "SHORT", f"RSI Перекуплен ({round(rsi,1)})"
        return ("LONG", "Тренд ВВЕРХ") if current_p > old_p else ("SHORT", "Тренд ВНИЗ")
    except:
        return "SHORT", "Ошибка анализа"

def bot_worker():
    send_tg("🚀 *БОТ ОБНОВЛЕН!* Исправлена ошибка 'entryPrice'.")
    try: exchange.set_leverage(LEVERAGE, SYMBOL)
    except: pass

    while True:
        try:
            exchange.load_markets()
            balance = exchange.fetch_balance()
            positions = balance['info']['positions']
            clean_symbol = SYMBOL.replace('/', '')
            
            pos_data = {p['positionSide']: abs(float(p['positionAmt'])) for p in positions if p['symbol'] == clean_symbol}
            curr_p = exchange.fetch_ticker(SYMBOL)['last']
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)

            # 1. СТАРТ ЦИКЛА
            if long_amt == 0 and short_amt == 0:
                side, reason = get_market_sentiment()
                qty = float(exchange.amount_to_precision(SYMBOL, (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p))
                
                if side == "SHORT":
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_p, params={'positionSide': 'SHORT'})
                    send_tg(f"📉 *SHORT* по `{curr_p}`. Тейк: `{tp_p}`\nПричина: {reason}")
                else:
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_p, params={'positionSide': 'LONG'})
                    send_tg(f"📈 *LONG* по `{curr_p}`. Тейк: `{tp_p}`\nПричина: {reason}")

            # 2. ЛОГИКА ЗАМКА (С БЕЗОПАСНЫМ ПОЛУЧЕНИЕМ ЦЕНЫ)
            # Для Шорта
            if short_amt > 0 and long_amt == 0:
                pos_list = [p for p in positions if p['symbol'] == clean_symbol and p['positionSide'] == 'SHORT']
                if pos_list:
                    entry_s = float(pos_list[0].get('entryPrice', pos_list[0].get('entry_price', 0)))
                    if entry_s > 0 and curr_p >= (entry_s + STEP):
                        qty = float(exchange.amount_to_precision(SYMBOL, short_amt))
                        exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                        tp_l = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                        exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_l, params={'positionSide': 'LONG'})
                        send_tg(f"🔒 *ЗАМОК (Лонг)* по `{curr_p}`. Тейк: `{tp_l}`")

            # Для Лонга
            if long_amt > 0 and short_amt == 0:
                pos_list = [p for p in positions if p['symbol'] == clean_symbol and p['positionSide'] == 'LONG']
                if pos_list:
                    entry_l = float(pos_list[0].get('entryPrice', pos_list[0].get('entry_price', 0)))
                    if entry_l > 0 and curr_p <= (entry_l - STEP):
                        qty = float(exchange.amount_to_precision(SYMBOL, long_amt))
                        exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                        tp_s = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                        exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_s, params={'positionSide': 'SHORT'})
                        send_tg(f"🔒 *ЗАМОК (Шорт)* по `{curr_p}`. Тейк: `{tp_s}`")

        except Exception as e:
            send_tg(f"⚠️ *Ошибка:* `{str(e)}`")
            time.sleep(30)
        
        time.sleep(15)

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "AI Bot Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
