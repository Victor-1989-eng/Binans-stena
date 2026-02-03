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
MIN_GAP = 0.0005 
# -----------------

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def run_scanner():
    print(f"🚀 Снайпер v3.1 (No-Spam) запущен...")
    send_tg(f"⚡️ *Снайпер v3.1 активирован*\nБезубыток убран. Только Smart Exit (EMA 7/25).")
    
    while True:
        for symbol in SYMBOLS:
            try:
                klines = client.futures_klines(symbol=symbol, interval=TIMEFRAME, limit=150)
                closes = [float(k[4]) for k in klines]
                series = pd.Series(closes)
                
                ema7 = series.ewm(span=EMA_PROTECT, adjust=False).mean().iloc[-1]
                f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
                f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
                s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
                s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]
                
                slope = abs(f_now - f_series.iloc[-4]) / f_now
                gap = abs(f_now - s_now) / s_now

                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    p = active[0]
                    amt, entry = float(p['positionAmt']), float(p['entryPrice'])
                    side = "LONG" if amt > 0 else "SHORT"
                    
                    # 🔥 ОСТАВЛЯЕМ ТОЛЬКО SMART EXIT (Выход при развороте EMA 7)
                    should_exit = False
                    if side == "LONG" and ema7 < f_now: should_exit = True
                    elif side == "SHORT" and ema7 > f_now: should_exit = True
                    
                    if should_exit:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        profit = round((closes[-1] - entry) / entry * 100 * (1 if side=="LONG" else -1) * LEVERAGE, 2)
                        send_tg(f"💰 *{symbol}* ЗАКРЫТ\nROI: `{profit}%` (Exit: EMA 7/25)")

                # ЛОГИКА ВХОДА
                signal = None
                if f_prev <= s_prev and f_now > s_now: signal = "LONG"
                elif f_prev >= s_prev and f_now < s_now: signal = "SHORT"

                if signal and not active and gap >= MIN_GAP and slope >= MIN_SLOPE:
                    execute_trade(symbol, signal, closes[-1])

            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
            time.sleep(0.5)

def execute_trade(symbol, side, price):
    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    qty = (MARGIN_USDC * LEVERAGE) / price
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)

    client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
    # Оставляем только аварийный стоп (3%) - на самый крайний случай
    sl_price = round(price * 0.97 if side == "LONG" else price * 1.03, 2)
    client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                               type='STOP_MARKET', stopPrice=str(sl_price), quantity=qty, reduceOnly=True)
    send_tg(f"🔥 *ВХОД {symbol} {side}*\nЦена: `{price}`")

threading.Thread(target=run_scanner, daemon=True).start()
@app.route('/')
def health(): return "Clean Scalper v3.1 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
