import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- ГЕОМЕТРИЯ ГИБРИДА v5.6 (Full Armor) ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC', 'BNBUSDC']
LEVERAGE = 55
MARGIN_USDC = 1.0 

TF_ENTRY = '15m' 
TF_EXIT = '1m'   

EMA_FAST = 7    
EMA_MED = 25    
EMA_SLOW = 99   

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

def get_ema_data(symbol, tf):
    """Получает все нужные EMA для таймфрейма"""
    klines = client.futures_klines(symbol=symbol, interval=tf, limit=150)
    closes = pd.Series([float(k[4]) for k in klines])
    f = closes.ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
    m = closes.ewm(span=EMA_MED, adjust=False).mean().iloc[-1]
    s = closes.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-1]
    # Для входа нам нужны еще и предыдущие значения
    f_prev = closes.ewm(span=EMA_FAST, adjust=False).mean().iloc[-2]
    s_prev = closes.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-2]
    return f, m, s, f_prev, s_prev

def run_scanner():
    print(f"🛡 Снайпер v5.6 (Hybrid Armor) запущен!")
    send_tg(f"🛡 *Sniper v5.6 Hybrid Armor*\nВход: 15м (7/99)\nВыход/Авария: 1м (7/25 и 7/99)")

    while True:
        for symbol in SYMBOLS:
            try:
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    # --- ВЫХОД И АВАРИЯ НА 1М ---
                    f1, m1, s1, _, _ = get_ema_data(symbol, TF_EXIT)
                    
                    p = active[0]
                    amt = float(p['positionAmt'])
                    entry = float(p['entryPrice'])
                    side = "LONG" if amt > 0 else "SHORT"
                    
                    should_exit = False
                    reason = ""

                    if side == "LONG":
                        if f1 < m1: 
                            should_exit = True
                            reason = "7/25 Profit Lock (1m)"
                        elif f1 <= s1: 
                            should_exit = True
                            reason = "7/99 Emergency (1m)"
                    else: # SHORT
                        if f1 > m1:
                            should_exit = True
                            reason = "7/25 Profit Lock (1m)"
                        elif f1 >= s1:
                            should_exit = True
                            reason = "7/99 Emergency (1m)"
                    
                    if should_exit:
                        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        profit = round((f1 - entry) / entry * 100 * (1 if side=="LONG" else -1) * LEVERAGE, 2)
                        send_tg(f"🏁 *{symbol}* ЗАКРЫТ\nПричина: `{reason}`\nROI: `{profit}%`")
                
                else:
                    # --- ВХОД НА 15М ---
                    f15, m15, s15, f15_prev, s15_prev = get_ema_data(symbol, TF_ENTRY)
                    gap = abs(f15 - s15) / s15
                    
                    if f15_prev <= s15_prev and f15 > s15 and gap >= MIN_GAP:
                        execute_trade(symbol, "LONG")
                    elif f15_prev >= s15_prev and f15 < s15 and gap >= MIN_GAP:
                        execute_trade(symbol, "SHORT")

            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
            time.sleep(1)

def execute_trade(symbol, side):
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    qty = (MARGIN_USDC * LEVERAGE) / price
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)

    try:
        client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        send_tg(f"🚀 *{symbol}* ВХОД {side} (15м пробой)")
    except Exception as e:
        print(f"Ошибка входа: {e}")

threading.Thread(target=run_scanner, daemon=True).start()
@app.route('/')
def health(): return "Hybrid Armor v5.6 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
