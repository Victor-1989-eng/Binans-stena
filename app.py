import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- ГЕОМЕТРИЯ АГРЕССОРА ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC']
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0
EMA_FAST = 7
EMA_SLOW = 25
MIN_GAP = 0.0006  # Защита от пилы (0.06%)
# ---------------------------

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def setup_account(symbol):
    try:
        client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
    except BinanceAPIException as e:
        if "No need to change margin type" not in str(e):
            print(f"Margin error {symbol}: {e}")
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    except Exception as e:
        print(f"Leverage error {symbol}: {e}")

def run_scanner():
    print(f"💀 Режим 'Изолированный Агрессор' v4.2 запущен!")
    send_tg(f"🛡 *Снайпер v4.2 (Anti-Chop)* АКТИВИРОВАН\nПары: `{SYMBOLS}`\nФильтр пилы: `{MIN_GAP*100}%` зазора")
    
    for s in SYMBOLS: setup_account(s)

    while True:
        for symbol in SYMBOLS:
            try:
                klines = client.futures_klines(symbol=symbol, interval=TIMEFRAME, limit=100)
                closes = [float(k[4]) for k in klines]
                series = pd.Series(closes)
                
                f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
                f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
                
                s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
                s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]

                gap = abs(f_now - s_now) / s_now

                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                # СИГНАЛ ПЕРЕСЕЧЕНИЯ
                signal = None
                if f_prev <= s_prev and f_now > s_now: signal = "LONG"
                elif f_prev >= s_prev and f_now < s_now: signal = "SHORT"

                if active:
                    amt = float(active[0]['positionAmt'])
                    current_side = "LONG" if amt > 0 else "SHORT"
                    
                    # ПЕРЕВЕРТЫШ: Только если есть сигнал И линии разошлись
                    if signal and signal != current_side and gap >= MIN_GAP:
                        client.futures_create_order(symbol=symbol, side='SELL' if current_side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        time.sleep(0.1)
                        execute_trade(symbol, signal, closes[-1])
                        send_tg(f"🔄 *{symbol}*: Реверс в {signal} (Пробой пилы)")
                else:
                    # ВХОД: Только если линии разошлись
                    if signal and gap >= MIN_GAP:
                        execute_trade(symbol, signal, closes[-1])

            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
            time.sleep(0.5)

def execute_trade(symbol, side, price):
    qty = (MARGIN_USDC * LEVERAGE) / price
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)

    try:
        client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        send_tg(f"🚀 *{symbol}*: Вход в {side}\nЦена: `{price}`")
    except Exception as e:
        print(f"Trade Error {symbol}: {e}")

threading.Thread(target=run_scanner, daemon=True).start()
@app.route('/')
def health(): return "Aggressive Scalper v4.2 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
