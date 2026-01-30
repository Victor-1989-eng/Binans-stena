import os, time, threading, requests
import ccxt
import pandas as pd
from flask import Flask

app = Flask(__name__)

# --- НАСТРОЙКИ РЕАЛЬНОГО ТОРГОВЦА ---
SYMBOL = 'BNB/USDC' # Или 'BNB/USDT'
RISK_REAL_USD = 1.0   # СТОП $1
REWARD_REAL_USD = 3.0 # ТЕЙК $3
LEVERAGE = 10         # Плечо

exchange = ccxt.binance({
    'apiKey': os.environ.get('BINANCE_API_KEY'),
    'secret': os.environ.get('BINANCE_API_SECRET'),
    'options': {'defaultType': 'future'},
    'enableRateLimit': True
})

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except: pass

def get_limits():
    bars = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=60)
    df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    return df['h'].max(), df['l'].min()

def trade_logic():
    send_tg("🚀 *РЕАЛЬНЫЙ КОНВЕЙЕР ЗАПУЩЕН* (Риск $1)\nРаботаем по BNB/USDC")
    
    while True:
        try:
            # Проверяем позицию
            pos = exchange.fetch_position(SYMBOL)
            if float(pos.get('contracts', 0)) == 0:
                h, l = get_limits()
                ticker = exchange.fetch_ticker(SYMBOL)
                price = ticker['last']
                
                side = None
                if price >= h: side = 'sell'
                elif price <= l: side = 'buy'
                
                if side:
                    # Расчет объема для стопа в $1 (0.5% движения цены)
                    stop_dist = price * 0.005 
                    qty = exchange.amount_to_precision(SYMBOL, RISK_REAL_USD / stop_dist)
                    
                    # Ставим плечо и режим
                    exchange.set_leverage(LEVERAGE, SYMBOL)
                    
                    # ВХОД
                    order = exchange.create_market_order(SYMBOL, side, qty)
                    
                    # Тейк и Стоп
                    tp_price = price + (stop_dist * 3) if side == 'buy' else price - (stop_dist * 3)
                    sl_price = price - stop_dist if side == 'buy' else price + stop_dist
                    
                    close_side = 'sell' if side == 'buy' else 'buy'
                    exchange.create_order(SYMBOL, 'limit', close_side, qty, tp_price, {'reduceOnly': True})
                    exchange.create_order(SYMBOL, 'stop_market', close_side, qty, {'stopPrice': sl_price, 'reduceOnly': True})
                    
                    send_tg(f"🔥 *РЕАЛЬНАЯ СДЕЛКА: {side.upper()}*\nВход: `{price}`\nTP: `{round(tp_price, 2)}` | SL: `{round(sl_price, 2)}`")
            
            time.sleep(30)
        except Exception as e:
            send_tg(f"⚠️ Ошибка: `{str(e)[:100]}`")
            time.sleep(60)

threading.Thread(target=trade_logic, daemon=True).start()

@app.route('/')
def health(): return "Real Bot Active", 200
