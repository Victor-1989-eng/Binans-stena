import os, time, requests, threading
import numpy as np
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = 'BNBUSDC'
LEVERAGE = 50
MARGIN_USDC = 1.0
EMA_FAST = 7
EMA_SLOW = 99

class BotState:
    def __init__(self):
        self.active_pos = None
        self.current_tf = '1m'
        self.ema_data = {'1m': {'f': 0, 's': 0}, '3m': {'f': 0, 's': 0}, '5m': {'f': 0, 's': 0}}
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

# --- МАТЕМАТИКА ---
def calculate_ema(prices, span):
    alpha = 2 / (span + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * alpha) + (ema * (1 - alpha))
    return ema

# --- ОСНОВНОЙ ЦИКЛ ---
def bot_worker():
    send_tg("🔌 *BNB Матрешка (API):* Запуск системы...")
    
    while True:
        try:
            # Обновляем данные для всех таймфреймов
            for tf in ['1m', '3m', '5m']:
                klines = client.futures_klines(symbol=SYMBOL, interval=tf, limit=105)
                closes = [float(k[4]) for k in klines[:-1]] # Берем только закрытые свечи
                current_price = float(klines[-1][4]) # Текущая цена (незакрытая свеча)
                
                f_ema = calculate_ema(closes, EMA_FAST)
                s_ema = calculate_ema(closes, EMA_SLOW)
                
                # Сохраняем предыдущие значения для поиска креста на 1м
                if tf == '1m':
                    prev_f, prev_s = state.ema_data['1m']['f'], state.ema_data['1m']['s']
                    
                    # Логика входа (только если нет позиции)
                    if not state.active_pos and prev_f > 0:
                        if prev_f <= prev_s and f_ema > s_ema:
                            open_trade('LONG', current_price)
                        elif prev_f >= prev_s and f_ema < s_ema:
                            open_trade('SHORT', current_price)
                
                state.ema_data[tf]['f'] = f_ema
                state.ema_data[tf]['s'] = s_ema

            # Если позиция открыта — проверяем условия выхода
            if state.active_pos:
                check_exit_logic(float(client.futures_symbol_ticker(symbol=SYMBOL)['price']))

        except Exception as e:
            print(f"Ошибка цикла: {e}")
        
        time.sleep(10) # Опрос каждые 10 секунд

def open_trade(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side='BUY' if side=='LONG' else 'SELL', type='MARKET', quantity=qty)
        
        state.active_pos = side
        state.entry_price = price
        state.current_tf = '1m'
        send_tg(f"🚀 *ВХОД {side}*\nЦена: `{price}`\nКонтроль: `1m`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def check_exit_logic(price):
    # Апгрейд таймфрейма
    for tf in ['5m', '3m']:
        slow = state.ema_data[tf]['s']
        if (state.active_pos == 'LONG' and price > slow) or (state.active_pos == 'SHORT' and price < slow):
            if tf != state.current_tf:
                if (tf == '5m' and state.current_tf in ['1m', '3m']) or (tf == '3m' and state.current_tf == '1m'):
                    state.current_tf = tf
                    send_tg(f"🔼 *Уровень:* `{tf}`")
                break
    
    # Выход
    cur_slow = state.ema_data[state.current_tf]['s']
    if (state.active_pos == 'LONG' and price < cur_slow) or (state.active_pos == 'SHORT' and price > cur_slow):
        close_trade(price)

def close_trade(price):
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        qty = abs(float(pos[0]['positionAmt']))
        if qty > 0:
            side = 'SELL' if state.active_pos == 'LONG' else 'BUY'
            client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty, reduceOnly=True)
            send_tg(f"🏁 *ВЫХОД* по `{price}`")
        state.active_pos = None
    except Exception as e:
        send_tg(f"❌ Ошибка выхода: {e}")

@app.route('/')
def health(): return "API_BOT_ALIVE"

if __name__ == "__main__":
    threading.Thread(target=bot_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
