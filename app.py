import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- КОНФИГУРАЦИЯ v7.5 ---
SYMBOLS = ['BTCUSDC', 'SOLUSDC']
LEVERAGE = 75
MARGIN_USDC = 1.0 
TF = '1m'            
NET_PROFIT_TARGET = 0.10 
APPROX_ENTRY_FEE = 0.04  
TOTAL_TARGET = NET_PROFIT_TARGET + APPROX_ENTRY_FEE 
GAP_THRESHOLD = 0.0006  # Зазор для 7/25

EMA_FAST = 7    
EMA_MED = 25   

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_precisions(symbol):
    """Узнаем точность монеты автоматически"""
    info = client.futures_exchange_info()
    s_info = next(item for item in info['symbols'] if item['symbol'] == symbol)
    return int(s_info['quantityPrecision']), int(s_info['pricePrecision'])

def get_ema(symbol):
    klines = client.futures_klines(symbol=symbol, interval=TF, limit=50)
    closes = pd.Series([float(k[4]) for k in klines])
    f = closes.ewm(span=EMA_FAST, adjust=False).mean()
    m = closes.ewm(span=EMA_MED, adjust=False).mean()
    return f.iloc[-1], f.iloc[-2], m.iloc[-1], m.iloc[-2]

def run_scanner():
    print("🚀 Завод v7.5 IRON SHELL запущен!")
    while True:
        for symbol in SYMBOLS:
            try:
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if not active:
                    f_now, f_prev, m_now, m_prev = get_ema(symbol)
                    gap = abs(f_now - m_now) / m_now
                    
                    side = None
                    if f_prev <= m_prev and f_now > m_now and gap >= GAP_THRESHOLD: side = "LONG"
                    elif f_prev >= m_prev and f_now < m_now and gap >= GAP_THRESHOLD: side = "SHORT"

                    if side: execute_trade(symbol, side)
                else:
                    # Аварийный выход при развороте
                    f_now, _, m_now, _ = get_ema(symbol)
                    amt = float(active[0]['positionAmt'])
                    if (amt > 0 and f_now < m_now) or (amt < 0 and f_now > m_now):
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if amt > 0 else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        send_tg(f"⚠️ *{symbol}* Авария (разворот)")
            except Exception as e: print(f"Ошибка сканера {symbol}: {e}")
            time.sleep(0.5)

def execute_trade(symbol, side):
    try:
        q_prec, p_prec = get_precisions(symbol)
        price_ticker = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        qty = round((MARGIN_USDC * LEVERAGE) / price_ticker, q_prec)

        # Вход по рынку
        order = client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        entry_price = float(order['avgPrice']) if 'avgPrice' in order and float(order['avgPrice']) > 0 else price_ticker

        # Расчет тейка
        price_offset = TOTAL_TARGET / qty
        tp_price = round(entry_price + price_offset if side == "LONG" else entry_price - price_offset, p_prec)

        # Цикл защиты: пытаемся поставить лимитку 5 раз
        for attempt in range(5):
            try:
                client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
                    type='LIMIT', timeInForce='GTC', quantity=qty, price=tp_price, reduceOnly=True)
                send_tg(f"✅ *{symbol}* {side}\nВход: `{entry_price}`\nТейк: `{tp_price}`")
                return # Выходим из функции, если всё ок
            except Exception as e:
                print(f"Попытка {attempt+1} тейка {symbol} провалена: {e}")
                time.sleep(1)
        
        send_tg(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: Тейк для {symbol} НЕ ПОСТАВЛЕН!")

    except Exception as e: print(f"Ошибка входа {symbol}: {e}")

threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Iron Shell 7.5 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
