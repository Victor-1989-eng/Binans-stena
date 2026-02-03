import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC']
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0
EMA_FAST = 25
EMA_SLOW = 99
EMA_PROTECT = 7
MIN_SLOPE = 0.0001
# -----------------

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except:
            pass

def run_scanner():
    print(f"🚀 Мульти-Снайпер запущен на парах: {SYMBOLS}")
    send_tg(f"🚀 *Мульти-Снайпер запущен!*\nПары: `{', '.join(SYMBOLS)}`")
    
    while True:
        for symbol in SYMBOLS:
            try:
                # 1. Получаем данные
                klines = client.futures_klines(symbol=symbol, interval=TIMEFRAME, limit=150)
                closes = [float(k[4]) for k in klines]
                series = pd.Series(closes)
                
                # Расчет EMA
                ema7 = series.ewm(span=EMA_PROTECT, adjust=False).mean().iloc[-1]
                f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
                f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
                s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
                s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]
                
                slope = abs(f_now - f_series.iloc[-4]) / f_now

                # 2. Проверка позиций по конкретному символу
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    p = active[0]
                    amt = float(p['positionAmt'])
                    side = "LONG" if amt > 0 else "SHORT"
                    entry = float(p['entryPrice'])
                    
                    # Защита (Безубыток)
                    is_safe = (side == "LONG" and ema7 > entry * 1.005) or \
                              (side == "SHORT" and ema7 < entry * 0.995)
                    
                    if is_safe:
                        open_orders = client.futures_get_open_orders(symbol=symbol)
                        entry_rounded = round(entry, 2) if "SOL" in symbol else round(entry, 1) if "ETH" in symbol else round(entry, 0)
                        
                        has_be_stop = any(o['type'] == 'STOP_MARKET' and float(o['stopPrice']) == entry_rounded for o in open_orders)

                        if not has_be_stop:
                            client.futures_cancel_all_open_orders(symbol=symbol)
                            client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
                                                      type='STOP_MARKET', stopPrice=str(entry_rounded),
                                                      quantity=abs(amt), reduceOnly=True)
                            send_tg(f"🛡 *{symbol}*: Стоп в безубытке ({entry_rounded})")

                # 3. Сигнал входа/реверса
                signal = None
                if f_prev <= s_prev and f_now > s_now: signal = "LONG"
                elif f_prev >= s_prev and f_now < s_now: signal = "SHORT"

                if signal:
                    if active:
                        current_side = "LONG" if float(active[0]['positionAmt']) > 0 else "SHORT"
                        if signal != current_side:
                            client.futures_cancel_all_open_orders(symbol=symbol)
                            client.futures_create_order(symbol=symbol, side='SELL' if current_side=="LONG" else 'BUY', 
                                                      type='MARKET', quantity=abs(float(active[0]['positionAmt'])), reduceOnly=True)
                            execute_trade(symbol, signal, closes[-1])
                    else:
                        if slope >= MIN_SLOPE:
                            execute_trade(symbol, signal, closes[-1])

            except Exception as e:
                print(f"Ошибка по {symbol}: {e}")
            
            time.sleep(0.5) # Пауза между парами

def execute_trade(symbol, side, price):
    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    # Динамический расчет QTY для разных цен (BTC стоит 100к, SOL 100)
    qty = (MARGIN_USDC * LEVERAGE) / price
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)

    client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
    
    sl_price = round(price * 0.97 if side == "LONG" else price * 1.03, 2)
    client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                               type='STOP_MARKET', stopPrice=str(sl_price), quantity=qty, reduceOnly=True)
    
    send_tg(f"🔥 *ВХОД {symbol} {side}*\nЦена: `{price}`\nСтоп: `{sl_price}`")

threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Scanner Multi-Pair Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
