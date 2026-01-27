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
STEP = 2.0
PROFIT_GOAL = 4.0

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
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=50)
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        rsi = ta.rsi(df['c'], length=14).iloc[-1]
        current_p = df['c'].iloc[-1]
        old_p = df['c'].iloc[-15]
        return ("LONG", "UP") if current_p > old_p else ("SHORT", "DOWN")
    except: return "SHORT", "Error"

def bot_worker():
    global stats
    send_tg("🔍 *ОТЛАДКА 5.3:* Мониторинг замка включен.")
    try: 
        exchange.load_markets()
        exchange.set_leverage(LEVERAGE, SYMBOL)
    except: pass

    while True:
        try:
            balance = exchange.fetch_balance()
            positions = balance['info']['positions']
            clean_symbol = SYMBOL.replace('/', '')
            
            # Фильтруем только активные позиции по нашему символу
            active_ps = [p for p in positions if p['symbol'] == clean_symbol and float(p['positionAmt']) != 0]
            
            pos_data = {p['positionSide']: abs(float(p['positionAmt'])) for p in active_ps}
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)
            curr_p = exchange.fetch_ticker(SYMBOL)['last']

            # 1. ВХОД В СДЕЛКУ
            if long_amt == 0 and short_amt == 0:
                if stats["cycles"] > 0:
                    stats["profit"] += PROFIT_GOAL 
                    send_tg(f"💰 *ПРОФИТ!* Всего: `{round(stats['profit'], 2)}` USDC")

                exchange.cancel_all_orders(SYMBOL)
                side, reason = get_market_sentiment()
                raw_qty = (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p
                qty = float(exchange.amount_to_precision(SYMBOL, raw_qty))
                
                if side == "SHORT":
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_p, params={'positionSide': 'SHORT'})
                    send_tg(f"📉 *Вход SHORT* по `{curr_p}`")
                else:
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_p, params={'positionSide': 'LONG'})
                    send_tg(f"📈 *Вход LONG* по `{curr_p}`")
                stats["cycles"] += 1

            # 2. ПРОВЕРКА ЗАМКА (УЛУЧШЕНО)
            # Если только Шорт
            if short_amt > 0 and long_amt == 0:
                p_info = next((p for p in active_ps if p['positionSide'] == 'SHORT'), None)
                if p_info:
                    entry_s = float(p_info.get('entryPrice', 0))
                    # Если цена выросла выше входа на STEP
                    if entry_s > 0 and curr_p >= (entry_s + STEP - 0.1):
                        exchange.create_order(SYMBOL, 'market', 'buy', short_amt, params={'positionSide': 'LONG'})
                        tp_l = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                        exchange.create_order(SYMBOL, 'limit', 'sell', short_amt, tp_l, params={'positionSide': 'LONG'})
                        send_tg(f"🔒 *ЗАМОК ОТКРЫТ (LONG)*\nЦена: `{curr_p}` (Вход: `{entry_s}`)")

            # Если только Лонг
            if long_amt > 0 and short_amt == 0:
                p_info = next((p for p in active_ps if p['positionSide'] == 'LONG'), None)
                if p_info:
                    entry_l = float(p_info.get('entryPrice', 0))
                    # Если цена упала ниже входа на STEP
                    if entry_l > 0 and curr_p <= (entry_l - STEP + 0.1):
                        exchange.create_order(SYMBOL, 'market', 'sell', long_amt, params={'positionSide': 'SHORT'})
                        tp_s = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                        exchange.create_order(SYMBOL, 'limit', 'buy', long_amt, tp_s, params={'positionSide': 'SHORT'})
                        send_tg(f"🔒 *ЗАМОК ОТКРЫТ (SHORT)*\nЦена: `{curr_p}` (Вход: `{entry_l}`)")

        except Exception as e:
            send_tg(f"⚠️ *Ошибка системы:* `{str(e)[:100]}`")
            time.sleep(15)
        
        time.sleep(15) # Ускорили опрос до 15 секунд

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Ready", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)     
