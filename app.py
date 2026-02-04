import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- КОНФИГУРАЦИЯ MICRO-SCALPER v7.1 ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC', 'BNBUSDC']
LEVERAGE = 75
MARGIN_USDC = 1.0 
TF = '1m'            
PROFIT_TARGET = 0.20 # Ставим 22 цента (20 чистыми + запас на комиссию входа)
EMA_FAST = 7    
EMA_SLOW = 99   
# ---------------------------------------

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
    print("🚀 Micro-Scalper v7.1 запущен! Цель: +20 центов.")
    send_tg("🚀 *Micro-Scalper v7.1 ЗАПУЩЕН*\nЦель: +0.20$ (Лимитный выход)\nТаймфрейм: 1м")

    while True:
        for symbol in SYMBOLS:
            try:
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if not active:
                    f_now, f_prev, s_now, s_prev = get_ema(symbol)
                    
                    side = None
                    if f_prev <= s_prev and f_now > s_now: side = "LONG"
                    elif f_prev >= s_prev and f_now < s_now: side = "SHORT"

                    if side:
                        execute_scalp_trade(symbol, side)
                
                else:
                    # Аварийный контроль: если 7-я EMA пробивает 99-ю в обратную сторону
                    f_now, _, s_now, _ = get_ema(symbol)
                    amt = float(active[0]['positionAmt'])
                    if (amt > 0 and f_now < s_now) or (amt < 0 and f_now > s_now):
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        client.futures_create_order(symbol=symbol, side='SELL' if amt > 0 else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        send_tg(f"⚠️ *{symbol}* Аварийный выход (разворот тренда).")

            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
            time.sleep(1)

def execute_scalp_trade(symbol, side):
    # 1. Рыночный вход
    price_info = client.futures_symbol_ticker(symbol=symbol)
    price = float(price_info['price'])
    qty = (MARGIN_USDC * LEVERAGE) / price
    
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)

    try:
        order = client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', 
                                          type='MARKET', quantity=qty)
        # Получаем реальную цену исполнения из ордера
        entry_price = float(order['avgPrice']) if 'avgPrice' in order and float(order['avgPrice']) > 0 else price
        
        # 2. Выставляем лимитку на продажу (Maker 0%)
        # Считаем смещение цены для профита $0.20
        price_offset = PROFIT_TARGET / qty
        tp_price = entry_price + price_offset if side == "LONG" else entry_price - price_offset
        
        # Округление цены под тики биржи (автоматизация округления)
        if "BTC" in symbol: tp_price = round(tp_price, 2)
        elif "ETH" in symbol: tp_price = round(tp_price, 2)
        else: tp_price = round(tp_price, 3)

        client.futures_create_order(
            symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
            type='LIMIT', timeInForce='GTC', quantity=qty,
            price=tp_price, reduceOnly=True
        )
        
        send_tg(f"📥 *{symbol}* {side} по `{entry_price}`\n🎯 Тейк-лимитка: `{tp_price}`")
    
    except Exception as e:
        print(f"Ошибка ордера: {e}")

threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Scalper 7.1 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
