import os, time, requests, threading
import numpy as np
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ SOL ---
SYMBOL = 'SOLUSDC'
LEVERAGE = 100
MARGIN_USDC = 1.0
EMA_FAST = 7
EMA_SLOW = 25
TAKE_PROFIT_USD = 0.10  # Тейк-профит 10 центов от цены входа

class BotState:
    def __init__(self):
        self.active_pos = None
        self.ema_f = 0
        self.ema_s = 0
        self.prev_f = 0
        self.prev_s = 0

state = BotState()
client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def calculate_ema(prices, span):
    alpha = 2 / (span + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * alpha) + (ema * (1 - alpha))
    return ema

def bot_worker():
    send_tg(f"⚡ *SOL Sniper 100x* Запущен!\nПараметры: EMA {EMA_FAST}/{EMA_SLOW}, TP: ${TAKE_PROFIT_USD}")
    
    while True:
        try:
            # Получаем свечи (100 штук достаточно для EMA 25)
            klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=100)
            closes = [float(k[4]) for k in klines[:-1]] # Закрытые
            current_price = float(klines[-1][4]) # Текущая цена
            
            state.prev_f, state.prev_s = state.ema_f, state.ema_s
            state.ema_f = calculate_ema(closes, EMA_FAST)
            state.ema_s = calculate_ema(closes, EMA_SLOW)
            
            # Если позиции нет и данные инициализированы
            if not state.active_pos and state.prev_f > 0:
                side = None
                if state.prev_f <= state.prev_s and state.ema_f > state.ema_s:
                    side = 'LONG'
                elif state.prev_f >= state.prev_s and state.ema_f < state.ema_s:
                    side = 'SHORT'
                
                if side:
                    execute_trade(side, current_price)
            
            # Проверка закрытия позиции (если она есть)
            if state.active_pos:
                check_position_status()

        except Exception as e:
            print(f"Ошибка: {e}")
        
        time.sleep(10) # Опрос каждые 10 секунд

def execute_trade(side, price):
    try:
        # 1. Плечо
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        
        # 2. Количество (для SOL точность обычно 2 знака, например 0.15 SOL)
        qty = round((MARGIN_USDC * LEVERAGE) / price, 2)
        
        # 3. Вход по маркету
        order = client.futures_create_order(symbol=SYMBOL, side='BUY' if side=='LONG' else 'SELL', type='MARKET', quantity=qty)
        entry_price = float(order.get('avgPrice', price))
        
        # 4. Тейк-профит (ровно +10 центов)
        tp_price = round(entry_price + TAKE_PROFIT_USD if side == 'LONG' else entry_price - TAKE_PROFIT_USD, 3)
        
        client.futures_create_order(
            symbol=SYMBOL, 
            side='SELL' if side=='LONG' else 'BUY', 
            type='LIMIT', 
            timeInForce='GTC', 
            quantity=qty, 
            price=tp_price, 
            reduceOnly=True
        )
        
        state.active_pos = side
        send_tg(f"🚀 *ВХОД {side} SOL*\nЦена: `{entry_price}`\nТейк: `{tp_price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def check_position_status():
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        for p in pos:
            if p['symbol'] == SYMBOL:
                if float(p['positionAmt']) == 0:
                    send_tg(f"💰 *SOL Сделка закрыта!* Жду новый сигнал...")
                    state.active_pos = None
                break
    except: pass

@app.route('/')
def health(): return "SOL_SNIPER_OK"

if __name__ == "__main__":
    threading.Thread(target=bot_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
