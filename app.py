import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- КОНФИГУРАЦИЯ v7.4 (PRECISION) ---
SYMBOLS = [
    'BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'BNBUSDC', 
    'XRPUSDC', 'ENAUSDC', 'AVAXUSDC', 'ZECUSDC', 
    'LINKUSDC', 'NEOUSDC'
]
LEVERAGE = 75
MARGIN_USDC = 1.0 
TF = '1m'            
NET_PROFIT_TARGET = 0.20 
APPROX_ENTRY_FEE = 0.04  
TOTAL_TARGET = NET_PROFIT_TARGET + APPROX_ENTRY_FEE 

# --- ФИЛЬТР ЗАЗОРА ---
GAP_THRESHOLD = 0.0005  # 0.1% зазор между EMA (чтобы избежать ложных входов)

EMA_FAST = 7    
EMA_SLOW = 99   

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_ema(symbol):
    klines = client.futures_klines(symbol=symbol, interval=TF, limit=110)
    closes = pd.Series([float(k[4]) for k in klines])
    f = closes.ewm(span=EMA_FAST, adjust=False).mean()
    s = closes.ewm(span=EMA_SLOW, adjust=False).mean()
    return f.iloc[-1], f.iloc[-2], s.iloc[-1], s.iloc[-2]

def run_scanner():
    print("🎯 Precision Scalper запущен: Фильтр зазора 0.1%")
    while True:
        for symbol in SYMBOLS:
            try:
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if not active:
                    f_now, f_prev, s_now, s_prev = get_ema(symbol)
                    
                    # Считаем текущий зазор в процентах
                    current_gap = abs(f_now - s_now) / s_now
                    
                    side = None
                    # Вход только если зазор больше порога (уверенный пробой)
                    if f_prev <= s_prev and f_now > s_now and current_gap >= GAP_THRESHOLD:
                        side = "LONG"
                    elif f_prev >= s_prev and f_now < s_now and current_gap >= GAP_THRESHOLD:
                        side = "SHORT"

                    if side:
                        execute_trade(symbol, side)
                else:
                    # Авария (выходим при обратном пересечении БЕЗ учета зазора, чтобы спасти депо)
                    f_now, _, s_now, _ = get_ema(symbol)
                    amt = float(active[0]['positionAmt'])
                    if (amt > 0 and f_now < s_now) or (amt < 0 and f_now > s_now):
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if amt > 0 else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        send_tg(f"⚠️ *{symbol}* Авария")
            except Exception as e: print(f"Error {symbol}: {e}")
            time.sleep(0.5)

def execute_trade(symbol, side):
    try:
        price_ticker = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        qty = (MARGIN_USDC * LEVERAGE) / price_ticker
        
        if "BTC" in symbol: qty = round(qty, 3)
        elif "ETH" in symbol: qty = round(qty, 2)
        else: qty = round(qty, 1)

        order = client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        entry_price = float(order['avgPrice']) if 'avgPrice' in order and float(order['avgPrice']) > 0 else price_ticker

        price_offset = TOTAL_TARGET / qty
        tp_price = entry_price + price_offset if side == "LONG" else entry_price - price_offset
        
        prec = 2 if "BTC" in symbol or "ETH" in symbol else 3
        tp_price = round(tp_price, prec)

        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
            type='LIMIT', timeInForce='GTC', quantity=qty, price=tp_price, reduceOnly=True)
        
        send_tg(f"🚀 *{symbol}* {side} (Зазор ок!)\nВход: `{entry_price}`\nТейк: `{tp_price}`")
    except Exception as e: print(f"Trade Error {symbol}: {e}")

threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Precision 7.4 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
