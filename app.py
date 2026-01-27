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
        current_p = df['c'].iloc[-1]
        old_p = df['c'].iloc[-15]
        return ("LONG", "UP") if current_p > old_p else ("SHORT", "DOWN")
    except: return "SHORT", "Error"

def bot_worker():
    global stats
    send_tg("🎯 *ВЕРСИЯ 5.4:* Агрессивный замок активирован.")
    try: 
        exchange.load_markets()
        exchange.set_leverage(LEVERAGE, SYMBOL)
    except: pass

    while True:
        try:
            # Получаем позиции через fetch_positions (более надежный метод ccxt)
            all_positions = exchange.fetch_positions([SYMBOL])
            active_ps = [p for p in all_positions if float(p['contracts']) > 0]
            
            pos_data = {p['side'].upper(): abs(float(p['contracts'])) for p in active_ps}
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)
            
            ticker = exchange.fetch_ticker(SYMBOL)
            curr_p = float(ticker['last'])

            # 1. ЛОГИКА ВХОДА
            if long_amt == 0 and short_amt == 0:
                if stats["cycles"] > 0:
                    stats["profit"] += PROFIT_GOAL 
                    send_tg(f"💰 *ПРОФИТ!* Всего: `{round(stats['profit'], 2)}` USDC")

                exchange.cancel_all_orders(SYMBOL)
                side, _ = get_market_sentiment()
                raw_qty = (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p
                qty = float(exchange.amount_to_precision(SYMBOL, raw_qty))
                
                if side == "SHORT":
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_p, params={'positionSide': 'SHORT'})
                    send_tg(f"📉 *SHORT* по `{curr_p}`")
                else:
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_p, params={'positionSide': 'LONG'})
                    send_tg(f"📈 *LONG* по `{curr_p}`")
                stats["cycles"] += 1

            # 2. ПРОВЕРКА ЗАМКА (Максимальная надежность)
            # Мы в Шорте, цена растет
            if short_amt > 0 and long_amt == 0:
                p = next(x for x in active_ps if x['side'].upper() == 'SHORT' or x['info'].get('positionSide') == 'SHORT')
                entry_s = float(p.get('entryPrice', p['info'].get('entryPrice', 0)))
                
                # Условие: если цена ушла ВЫШЕ на STEP или БОЛЕЕ
                if entry_s > 0 and curr_p >= (entry_s + STEP - 0.1):
                    exchange.create_order(SYMBOL, 'market', 'buy', short_amt, params={'positionSide': 'LONG'})
                    tp_l = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', short_amt, tp_l, params={'positionSide': 'LONG'})
                    send_tg(f"🔒 *ЗАМОК ОТКРЫТ (LONG)*\nЦена: `{curr_p}` (Вход: `{entry_s}`)")

            # Мы в Лонге, цена падает
            if long_amt > 0 and short_amt == 0:
                p = next(x for x in active_ps if x['side'].upper() == 'LONG' or x['info'].get('positionSide') == 'LONG')
                entry_l = float(p.get('entryPrice', p['info'].get('entryPrice', 0)))
                
                # Условие: если цена ушла НИЖЕ на STEP или БОЛЕЕ
                if entry_l > 0 and curr_p <= (entry_l - STEP + 0.1):
                    exchange.create_order(SYMBOL, 'market', 'sell', long_amt, params={'positionSide': 'SHORT'})
                    tp_s = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', long_amt, tp_s, params={'positionSide': 'SHORT'})
                    send_tg(f"🔒 *ЗАМОК ОТКРЫТ (SHORT)*\nЦена: `{curr_p}` (Вход: `{entry_l}`)")

        except Exception as e:
            # Если ошибка в поиске позиции - бот напишет об этом
            if "StopIteration" not in str(e):
                send_tg(f"⚠️ *Ошибка:* `{str(e)[:80]}`")
            time.sleep(10)
        
        time.sleep(10) # 10 секунд — теперь бот проверяет очень часто

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Ready", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
