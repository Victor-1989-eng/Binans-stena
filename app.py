import os, time, requests, threading
import numpy as np
from flask import Flask
from binance.client import Client
from binance.streams import ThreadedWebsocketManager

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDC'
LEVERAGE = 50
MARGIN_USDC = 2.0
EMA_FAST = 7
EMA_SLOW = 99

class BotState:
    def __init__(self):
        self.active_pos = None
        self.current_tf = '1m'
        self.ema_data = {'1m': {}, '3m': {}, '5m': {}}
        self.entry_price = 0

state = BotState()
client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text})
        except: pass

def get_ema(closes, span):
    alpha = 2 / (span + 1)
    ema = closes[0]
    for val in closes[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def update_ema(prev_ema, price, span):
    alpha = 2 / (span + 1)
    return (price * alpha) + (prev_ema * (1 - alpha))

def init_all_tf():
    try:
        for tf in ['1m', '3m', '5m']:
            klines = client.futures_klines(symbol=SYMBOL, interval=tf, limit=150)
            closes = [float(k[4]) for k in klines]
            state.ema_data[tf] = {
                'fast': get_ema(closes, EMA_FAST),
                'slow': get_ema(closes, EMA_SLOW)
            }
        send_tg("🚀 BNB Матрешка готова к работе!")
    except Exception as e:
        print(f"Init error: {e}")

def handle_socket_message(msg):
    if 'e' not in msg or msg['e'] != 'kline': return
    k = msg['k']
    if k['s'] != SYMBOL or not k['x']: return
    
    close_price = float(k['c'])
    interval = k['i']

    if interval in state.ema_data:
        d = state.ema_data[interval]
        prev_fast, prev_slow = d['fast'], d['slow']
        d['fast'] = update_ema(d['fast'], close_price, EMA_FAST)
        d['slow'] = update_ema(d['slow'], close_price, EMA_SLOW)
        
        if interval == '1m' and not state.active_pos:
            if prev_fast <= prev_slow and d['fast'] > d['slow']: open_trade('LONG', close_price)
            elif prev_fast >= prev_slow and d['fast'] < d['slow']: open_trade('SHORT', close_price)

        if interval == '1m' and state.active_pos:
            check_logic(close_price)

def open_trade(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side='BUY' if side=='LONG' else 'SELL', type='MARKET', quantity=qty)
        state.active_pos, state.entry_price, state.current_tf = side, price, '1m'
        send_tg(f"🚀 ВХОД {side} по {price}")
    except Exception as e: send_tg(f"❌ Ошибка входа: {e}")

def check_logic(price):
    for tf in ['5m', '3m']:
        slow = state.ema_data[tf]['slow']
        if (state.active_pos == 'LONG' and price > slow) or (state.active_pos == 'SHORT' and price < slow):
            if tf != state.current_tf:
                state.current_tf = tf
                send_tg(f"🔼 Уровень: {tf}")
                break
    
    cur_slow = state.ema_data[state.current_tf]['slow']
    if (state.active_pos == 'LONG' and price < cur_slow) or (state.active_pos == 'SHORT' and price > cur_slow):
        close_trade(price)

def close_trade(price):
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        qty = abs(float(pos[0]['positionAmt']))
        if qty > 0:
            side = 'SELL' if state.active_pos == 'LONG' else 'BUY'
            client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, reduceOnly=True)
            send_tg(f"🏁 ВЫХОД по {price}")
        state.active_pos = None
    except Exception as e: send_tg(f"❌ Ошибка выхода: {e}")

def start_bot():
    init_all_tf()
    twm = ThreadedWebsocketManager(api_key=os.environ.get("BINANCE_API_KEY"), api_secret=os.environ.get("BINANCE_API_SECRET"))
    twm.start()
    for tf in ['1m', '3m', '5m']:
        twm.start_kline_socket(callback=handle_socket_message, symbol=SYMBOL, interval=tf)

@app.route('/')
def health(): return "OK"

if __name__ == "__main__":
    threading.Thread(target=lambda: (time.sleep(5), start_bot()), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
