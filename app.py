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
EMA_PROTECT = 7   # Используется и для БУ, и для Тейка
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
    print(f"🚀 Снайпер v2.0 (Smart Exit) запущен: {SYMBOLS}")
    send_tg(f"🚀 *Снайпер v2.0 запущен!*\nРежим: `Ранний выход по EMA 7`\nПары: `{', '.join(SYMBOLS)}`")
    
    while True:
        for symbol in SYMBOLS:
            try:
                # 1. Получаем данные
                klines = client.futures_klines(symbol=symbol, interval=TIMEFRAME, limit=150)
                closes = [float(k[4]) for k in klines]
                series = pd.Series(closes)
                
                # Расчет всех EMA
                ema7 = series.ewm(span=EMA_PROTECT, adjust=False).mean().iloc[-1]
                
                f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
                f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
                
                s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
                s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]
                
                slope = abs(f_now - f_series.iloc[-4]) / f_now

                # 2. Работа с позициями
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    p = active[0]
                    amt = float(p['positionAmt'])
                    side = "LONG" if amt > 0 else "SHORT"
                    entry = float(p['entryPrice'])
                    current_price = closes[-1]
                    
                    # --- ЛОГИКА 1: ПРЕДОХРАНИТЕЛЬ (Безубыток) ---
                    is_safe = (side == "LONG" and ema7 > entry * 1.005) or \
                              (side == "SHORT" and ema7 < entry * 0.995)
                    
                    if is_safe:
                        open_orders = client.futures_get_open_orders(symbol=symbol)
                        # Округление цены для стопа (SOL-2, ETH-2, BTC-1 знак)
                        digits = 1 if "BTC" in symbol else 2
                        entry_rounded = round(entry, digits)
                        
                        has_be_stop = any(o['type'] == 'STOP_MARKET' and float(o['stopPrice']) == entry_rounded for o in open_orders)

                        if not has_be_stop:
                            client.futures_cancel_all_open_orders(symbol=symbol)
                            client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
                                                      type='STOP_MARKET', stopPrice=str(entry_rounded),
                                                      quantity=abs(amt), reduceOnly=True)
                            send_tg(f"🛡 *{symbol}*: Стоп в безубытке ({entry_rounded})")

                    # --- ЛОГИКА 2: УМНЫЙ ТЕЙК (Выход по EMA 7/25) ---
                    # Если быстрая (7) пересекает среднюю (25) ПРОТИВ нас -> ВЫХОДИМ
                    should_take_profit = False
                    if side == "LONG" and ema7 < f_now: # Разворот вниз
                        should_take_profit = True
                    elif side == "SHORT" and ema7 > f_now: # Разворот вверх
                        should_take_profit = True
                    
                    if should_take_profit:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        
                        # Считаем примерный профит для красивого лога
                        profit_pct = round((current_price - entry) / entry * 100 * (1 if side=="LONG" else -1) * LEVERAGE, 2)
                        send_tg(f"💰 *{symbol}* SMART EXIT!\nEMA 7 пересекла EMA 25.\nЦена: `{current_price}`\nROI: `~{profit_pct}%`")

                # 3. ЛОГИКА ВХОДА (Только если нет позиции)
                signal = None
                if f_prev <= s_prev and f_now > s_now: signal = "LONG"
                elif f_prev >= s_prev and f_now < s_now: signal = "SHORT"

                if signal:
                    # Если позиции нет -> входим. Если есть -> реверс только по 25/99 (но SMART EXIT сработает раньше)
                    if not active and slope >= MIN_SLOPE:
                        execute_trade(symbol, signal, closes[-1])
                    elif active:
                        # Если вдруг SMART EXIT не успел, а уже глобальный разворот
                        current_side = "LONG" if float(active[0]['positionAmt']) > 0 else "SHORT"
                        if signal != current_side:
                            client.futures_cancel_all_open_orders(symbol=symbol)
                            client.futures_create_order(symbol=symbol, side='SELL' if current_side=="LONG" else 'BUY', 
                                                      type='MARKET', quantity=abs(float(active[0]['positionAmt'])), reduceOnly=True)
                            execute_trade(symbol, signal, closes[-1])

            except Exception as e:
                print(f"Ошибка по {symbol}: {e}")
            
            time.sleep(0.5)

def execute_trade(symbol, side, price):
    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    # Расчет объема
    qty_usdt = MARGIN_USDC * LEVERAGE
    qty = qty_usdt / price
    
    # Округление количества
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1) # SOL и др

    client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
    
    # Аварийный стоп 3% (на случай резкого сквиза до включения безубытка)
    sl_price = round(price * 0.97 if side == "LONG" else price * 1.03, 2)
    client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                               type='STOP_MARKET', stopPrice=str(sl_price), quantity=qty, reduceOnly=True)
    
    send_tg(f"🔥 *ВХОД {symbol} {side}*\nЦена: `{price}`\nСтоп (аварийный): `{sl_price}`")

threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Smart Scalper V2 is Running"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
