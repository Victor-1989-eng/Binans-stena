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
    send_tg("🛠 *ОБНОВЛЕНИЕ v5.6:* Исправлена логика тейков при замке.")
    try: 
        exchange.load_markets()
        exchange.set_leverage(LEVERAGE, SYMBOL)
    except: pass

    while True:
        try:
            # 1. ПОЛУЧАЕМ ДАННЫЕ
            all_positions = exchange.fetch_positions([SYMBOL])
            active_ps = [p for p in all_positions if float(p.get('contracts', 0)) > 0]
            
            pos_data = {p['side'].upper(): abs(float(p['contracts'])) for p in active_ps}
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)
            
            ticker = exchange.fetch_ticker(SYMBOL)
            curr_p = float(ticker['last'])

            # 2. НОВЫЙ ВХОД (Если всё чисто)
            if long_amt == 0 and short_amt == 0:
                if stats["cycles"] > 0:
                    stats["profit"] += PROFIT_GOAL 
                    send_tg(f"💰 *ЦИКЛ ЗАВЕРШЕН!* \nВсего профита: `{round(stats['profit'], 2)}` USDC")

                exchange.cancel_all_orders(SYMBOL) # Чистим только когда позиций НЕТ
                side, _ = get_market_sentiment()
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

            # 3. ЛОГИКА ЗАМКА (БЕЗ УДАЛЕНИЯ СТАРЫХ ТЕЙКОВ)
            # Шорт в минусе -> Открываем Лонг
            if short_amt > 0 and long_amt == 0:
                p = next(x for x in active_ps if x['info'].get('positionSide') == 'SHORT')
                entry_s = float(p.get('entryPrice', p['info'].get('entryPrice', 0)))
                
                if entry_s > 0 and curr_p >= (entry_s + STEP - 0.1):
                    # Открываем только Лонг и его тейк. Тейк Шорта не трогаем!
                    exchange.create_order(SYMBOL, 'market', 'buy', short_amt, params={'positionSide': 'LONG'})
                    tp_l = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', short_amt, tp_l, params={'positionSide': 'LONG'})
                    send_tg(f"🔒 *ЗАМОК ОТКРЫТ (LONG)*\nТейк лонга: `{tp_l}`. Тейк шорта сохранен.")
                    time.sleep(5)

            # Лонг в минусе -> Открываем Шорт
            if long_amt > 0 and short_amt == 0:
                p = next(x for x in active_ps if x['info'].get('positionSide') == 'LONG')
                entry_l = float(p.get('entryPrice', p['info'].get('entryPrice', 0)))
                
                if entry_l > 0 and curr_p <= (entry_l - STEP + 0.1):
                    exchange.create_order(SYMBOL, 'market', 'sell', long_amt, params={'positionSide': 'SHORT'})
                    tp_s = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', long_amt, tp_s, params={'positionSide': 'SHORT'})
                    send_tg(f"🔒 *ЗАМОК ОТКРЫТ (SHORT)*\nТейк шорта: `{tp_s}`. Тейк лонга сохранен.")
                    time.sleep(5)

        except Exception as e:
            if "StopIteration" not in str(e):
                send_tg(f"⚠️ *Ошибка:* `{str(e)[:80]}`")
            time.sleep(35)
        
        time.sleep(35)

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Ready", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
