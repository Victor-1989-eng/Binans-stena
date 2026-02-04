import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- ГЕОМЕТРИЯ ГЕНИЯ v5.2 (15m Edition) ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC', 'BNBUSDC']
TIMEFRAME = '15m'  # ТЕПЕРЬ 15 МИНУТ
LEVERAGE = 75
MARGIN_USDC = 1.0 

EMA_FAST = 7    
EMA_MED = 25    
EMA_SLOW = 99   

# На 15м зазор можно сделать чуть больше (0.1%), чтобы отсечь ложные тени
MIN_GAP = 0.0005 
# --------------------------------------------

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
        if "No need to change margin type" not in str(e): print(f"Margin error {symbol}: {e}")
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    except Exception as e: print(f"Leverage error {symbol}: {e}")

def run_scanner():
    print(f"🌊 Genius 15m Edition запущен!")
    send_tg(f"🌊 *Genius v5.2 (15-минутка)*\nЧистый тренд активирован. Плечо: {LEVERAGE}x")
    
    for s in SYMBOLS: setup_account(s)

    while True:
        for symbol in SYMBOLS:
            try:
                # Берем больше свечей для точности EMA на старшем таймфрейме
                klines = client.futures_klines(symbol=symbol, interval=TIMEFRAME, limit=200)
                closes = [float(k[4]) for k in klines]
                series = pd.Series(closes)
                
                f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
                f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
                m_now = series.ewm(span=EMA_MED, adjust=False).mean().iloc[-1]
                s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
                s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]

                gap = abs(f_now - s_now) / s_now
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    p = active[0]
                    amt, entry = float(p['positionAmt']), float(p['entryPrice'])
                    side = "LONG" if amt > 0 else "SHORT"
                    
                    should_exit = False
                    exit_reason = ""

                    if side == "LONG":
                        if f_now < m_now: 
                            should_exit = True
                            exit_reason = "7/25 (Trend Bend)"
                        elif f_now <= s_now:
                            should_exit = True
                            exit_reason = "7/99 (Armor Break)"
                    else: # SHORT
                        if f_now > m_now:
                            should_exit = True
                            exit_reason = "7/25 (Trend Bend)"
                        elif f_now >= s_now:
                            should_exit = True
                            exit_reason = "7/99 (Armor Break)"
                    
                    if should_exit:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        profit = round((closes[-1] - entry) / entry * 100 * (1 if side=="LONG" else -1) * LEVERAGE, 2)
                        send_tg(f"🏁 *{symbol}* ВЫШЕЛ\nROI: `{profit}%`\nПричина: {exit_reason}")
                
                else:
                    # ВХОД ПО 7 / 99 (На 15м это очень сильный сигнал)
                    if f_prev <= s_prev and f_now > s_now and gap >= MIN_GAP:
                        execute_trade(symbol, "LONG", closes[-1])
                    elif f_prev >= s_prev and f_now < s_now and gap >= MIN_GAP:
                        execute_trade(symbol, "SHORT", closes[-1])

            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
            
            # На 15м можно опрашивать чуть реже, но оставим 0.5с для скорости исполнения
            time.sleep(0.5)

def execute_trade(symbol, side, price):
    qty = (MARGIN_USDC * LEVERAGE) / price
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)

    try:
        client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        send_tg(f"🚀 *{symbol}* ВХОД {side} (15m)\nЦена: `{price}`")
    except Exception as e:
        print(f"Trade Error {symbol}: {e}")

threading.Thread(target=run_scanner, daemon=True).start()
@app.route('/')
def health(): return "Genius 15m v5.2 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
