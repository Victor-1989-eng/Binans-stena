import os, time, requests, sys, threading
import numpy as np
from flask import Flask
from binance.client import Client
from binance.streams import ThreadedWebsocketManager

app = Flask(__name__)

# --- НАСТРОЙКИ BNB ---
SYMBOL = 'BNBUSDC'
LEVERAGE = 50
MARGIN_USDC = 2.0
EMA_FAST = 7
EMA_SLOW = 99

class BotState:
    def __init__(self):
        self.active_pos = None  # 'LONG', 'SHORT'
        self.current_tf = '1m'  # Рабочий таймфрейм для выхода
        self.ema_data = {'1m': {}, '3m': {}, '5m': {}}
        self.entry_price = 0

state = BotState()
client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
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
    send_tg("🔌 Инициализация BNB Матрешки...")
    for tf in ['1m', '3m', '5m']:
        klines = client.futures_klines(symbol=SYMBOL, interval=tf, limit=150)
        closes = [float(k[4]) for k in klines]
        state.ema_data[tf] = {
            'fast': get_ema(closes, EMA_FAST),
            'slow': get_ema(closes, EMA_SLOW)
        }
    send_tg(f"✅ Готов! Вход на 1м, трейлинг до 5м.")

def handle_socket_message(msg):
    if 'e' not in msg or msg['e'] != 'kline': return
    k = msg['k']
    if k['s'] != SYMBOL: return
    
    close_price = float(k['c'])
    is_closed = k['x']
    interval = k['i']

    # Обновляем EMA только по закрытию свечи
    if is_closed and interval in state.ema_data:
        d = state.ema_data[interval]
        d['prev_fast'], d['prev_slow'] = d['fast'], d['slow']
        d['fast'] = update_ema(d['fast'], close_price, EMA_FAST)
        d['slow'] = update_ema(d['slow'], close_price, EMA_SLOW)
        
        # ЛОГИКА ВХОДА (1м)
        if interval == '1m' and not state.active_pos:
            if d['prev_fast'] <= d['prev_slow'] and d['fast'] > d['slow']:
                execute_trade('LONG', close_price)
            elif d['prev_fast'] >= d['prev_slow'] and d['fast'] < d['slow']:
                execute_trade('SHORT', close_price)

        # ЛОГИКА ВЫХОДА И ПЕРЕКЛЮЧЕНИЯ (Проверка каждую закрытую минуту)
        if interval == '1m' and state.active_pos:
            check_exit_and_upgrade(close_price)

def execute_trade(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 2) # Точность BNB
        
        client.futures_create_order(symbol=SYMBOL, side='BUY' if side=='LONG' else 'SELL', type='MARKET', quantity=qty)
        
        state.active_pos = side
        state.current_tf = '1m'
        state.entry_price = price
        send_tg(f"🚀 *ВХОД {side}* (BNB)\nЦена: `{price}`\nКонтроль: `1m`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def check_exit_and_upgrade(price):
    # 1. Проверяем возможность апгрейда таймфрейма
    for tf in ['5m', '3m']:
        ema_slow = state.ema_data[tf]['slow']
        if (state.active_pos == 'LONG' and price > ema_slow) or \
           (state.active_pos == 'SHORT' and price < ema_slow):
            if tf != state.current_tf:
                # Если мы "доросли" до старшего таймфрейма (3м или 5м)
                if (tf == '5m' and state.current_tf in ['1m', '3m']) or (tf == '3m' and state.current_tf == '1m'):
                    state.current_tf = tf
                    send_tg(f"🔼 Уровень повышен! Контроль по `{tf}`")
                break

    # 2. Проверка выхода по текущему TF
    current_slow = state.ema_data[state.current_tf]['slow']
    should_exit = False
    if state.active_pos == 'LONG' and price < current_slow: should_exit = True
    elif state.active_pos == 'SHORT' and price > current_slow: should_exit = True
    
    if should_exit:
        close_pos(price)

def close_pos(price):
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        qty = abs(float(pos[0]['positionAmt']))
        if qty > 0:
            side = 'SELL' if state.active_pos == 'LONG' else 'BUY'
            client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, reduceOnly=True)
            res = "PROFIT" if (state.active_pos == 'LONG' and price > state.entry_price) or \
                             (state.active_pos == 'SHORT' and price < state.entry_price) else "LOSS"
            send_tg(f"🏁 *ВЫХОД BNB* ({res})\nТаймфрейм: `{state.current_tf}`\nЦена: `{price}`")
        state.active_pos = None
        state.current_tf = '1m'
    except Exception as e:
        send_tg(f"❌ Ошибка закрытия: {e}")

def start_bot():
    init_all_tf()
    twm = ThreadedWebsocketManager(api_key=os.environ.get("BINANCE_API_KEY"), api_secret=os.environ.get("BINANCE_API_SECRET"))
    twm.start()
    for tf in ['1m', '3m', '5m']:
        twm.start_kline_socket(callback=handle_socket_message, symbol=SYMBOL, interval=tf)
    twm.join()

@app.route('/')
def health(): return "BNB Speedster Live"

if __name__ == "__main__":
    threading.Thread(target=lambda: (time.sleep(5), start_bot()), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
