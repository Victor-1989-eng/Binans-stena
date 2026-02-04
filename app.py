import os, time, threading, requests
import pandas as pd
import numpy as np
from flask import Flask
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- ГЕОМЕТРИЯ ГЕНИЯ (v5.0) ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC']
TIMEFRAME = '1m'
LEVERAGE = 75
MARGIN_USDC = 1.0  # Твоя ставка

EMA_FAST = 7    # Пульс (для входа и выхода)
EMA_MED = 25    # Фильтр выхода (Smart Exit)
EMA_SLOW = 99   # Фильтр входа (Бетонная стена)

MIN_GAP = 0.0003 # 0.06% зазора между 7 и 99 для входа
# ------------------------------

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def setup_account(symbol):
    """Настройка изолированной маржи и плеча"""
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
    print(f"🚀 Снайпер v5.0 GENIUS запущен!")
    send_tg(f"🧠 *Снайпер v5.0 GENIUS АКТИВИРОВАН*\nВход: `7 / 99` (+ зазор {MIN_GAP*100}%)\nВыход: `7 / 25` (Мгновенно)")
    
    for s in SYMBOLS: setup_account(s)

    while True:
        for symbol in SYMBOLS:
            try:
                # Получаем свечи
                klines = client.futures_klines(symbol=symbol, interval=TIMEFRAME, limit=150)
                closes = [float(k[4]) for k in klines]
                series = pd.Series(closes)
                
                # Расчет трех линий EMA
                f_series = series.ewm(span=EMA_FAST, adjust=False).mean()
                f_now, f_prev = f_series.iloc[-1], f_series.iloc[-2]
                
                m_now = series.ewm(span=EMA_MED, adjust=False).mean().iloc[-1]
                
                s_series = series.ewm(span=EMA_SLOW, adjust=False).mean()
                s_now, s_prev = s_series.iloc[-1], s_series.iloc[-2]

                # Зазор между быстрой и тяжелой для входа
                gap = abs(f_now - s_now) / s_now

                # Проверка позиций
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    p = active[0]
                    amt, entry = float(p['positionAmt']), float(p['entryPrice'])
                    side = "LONG" if amt > 0 else "SHORT"
                    
                    # 🏁 ВЫХОД ПО 7 / 25 (Быстрая фиксация)
                    should_exit = False
                    if side == "LONG" and f_now < m_now: should_exit = True
                    elif side == "SHORT" and f_now > m_now: should_exit = True
                    
                    if should_exit:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        
                        # Считаем профит
                        current_price = closes[-1]
                        profit = round((current_price - entry) / entry * 100 * (1 if side=="LONG" else -1) * LEVERAGE, 2)
                        send_tg(f"💰 *{symbol}* ЗАКРЫТ\nROI: `{profit}%` (Выход 7/25)")
                else:
                    # 🔥 ВХОД ПО 7 / 99 (Глобальный пробой)
                    if f_prev <= s_prev and f_now > s_now and gap >= MIN_GAP:
                        execute_trade(symbol, "LONG", closes[-1])
                    elif f_prev >= s_prev and f_now < s_now and gap >= MIN_GAP:
                        execute_trade(symbol, "SHORT", closes[-1])

            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
            
            time.sleep(0.5)

def execute_trade(symbol, side, price):
    """Выполнение входа в позицию"""
    qty = (MARGIN_USDC * LEVERAGE) / price
    # Округление для разных пар
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1) # SOL и прочие

    try:
        client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        send_tg(f"🚀 *{symbol}* ВХОД {side}\nПробой 99-й EMA!\nЦена: `{price}`")
    except Exception as e:
        print(f"Trade Error {symbol}: {e}")

# Запуск сканера в отдельном потоке
threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health():
    return "Genius Scalper v5.0 is Running"

if __name__ == "__main__":
    # Render использует переменную окружения PORT
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
