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
        old_p = df['c'].iloc[-15] # Сравнение с ценой 15 минут назад
        return ("LONG", "UP") if current_p > old_p else ("SHORT", "DOWN")
    except: return "SHORT", "Error"

def bot_worker():
    global stats
    send_tg("🎯 *ВЕРСИЯ 6.2:* Чистый скальпинг запущен (БЕЗ ЗАМКОВ).")
    
    try: 
        exchange.load_markets()
        exchange.set_leverage(LEVERAGE, SYMBOL)
    except: pass

    while True:
        try:
            # 1. Сбор данных о позициях
            all_positions = exchange.fetch_positions([SYMBOL])
            active_ps = [p for p in all_positions if float(p.get('contracts', 0)) > 0]
            
            # Если позиций нет
            if not active_ps:
                # Начисляем профит за прошлый цикл
                if stats["cycles"] > 0:
                    stats["profit"] += PROFIT_GOAL 
                    send_tg(f"✅ *ЦИКЛ ЗАВЕРШЕН!* \nПрофит: `{round(stats['profit'], 2)}` USDC")

                exchange.cancel_all_orders(SYMBOL) # Чистим старые лимитки
                
                # Определяем тренд и входим
                ticker = exchange.fetch_ticker(SYMBOL)
                curr_p = float(ticker['last'])
                side, _ = get_market_sentiment()
                
                raw_qty = (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p
                qty = float(exchange.amount_to_precision(SYMBOL, raw_qty))
                
                if side == "SHORT":
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_p, params={'positionSide': 'SHORT'})
                    send_tg(f"📉 *Вход SHORT* по `{curr_p}`. Тейк: `{tp_p}`")
                else:
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_p, params={'positionSide': 'LONG'})
                    send_tg(f"📈 *Вход LONG* по `{curr_p}`. Тейк: `{tp_p}`")
                
                stats["cycles"] += 1
            
            else:
                # Если позиция есть, просто проверяем наличие тейк-профита
                open_orders = exchange.fetch_open_orders(SYMBOL)
                if not open_orders:
                    # Если тейка почему-то нет — выставляем его заново
                    p = active_ps[0]
                    side = p['side'].upper()
                    amt = abs(float(p['contracts']))
                    entry_p = float(p['entryPrice'])
                    
                    if side == 'LONG':
                        tp_p = float(exchange.price_to_precision(SYMBOL, entry_p + PROFIT_GOAL))
                        exchange.create_order(SYMBOL, 'limit', 'sell', amt, tp_p, params={'positionSide': 'LONG'})
                    else:
                        tp_p = float(exchange.price_to_precision(SYMBOL, entry_p - PROFIT_GOAL))
                        exchange.create_order(SYMBOL, 'limit', 'buy', amt, tp_p, params={'positionSide': 'SHORT'})
                    send_tg(f"🔧 *Тейк восстановлен* на уровне `{tp_p}`")

        except Exception as e:
            if "StopIteration" not in str(e):
                send_tg(f"⚠️ *Ошибка:* `{str(e)[:80]}`")
            time.sleep(35)
        
        time.sleep(35) # Спокойный ритм опроса

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Ready", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
