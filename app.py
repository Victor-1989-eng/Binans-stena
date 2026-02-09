import os, time, threading, requests, json
from flask import Flask
from binance.client import Client
from unicorn_binance_websocket_api.manager import BinanceWebSocketApiManager # pip install unicorn-binance-websocket-api

app = Flask(__name__)

# --- НАСТРОЙКИ (берутся из сред Render) ---
SYMBOL = os.environ.get("SYMBOL", "SOLUSDC")
THRESHOLD = 0.004       
STEP_DIFF = 0.002       
MAX_STEPS = 6           
LEVERAGE = 30            
MARGIN_STEP = 1.0       

# Инициализация клиента для ордеров
client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

# Память бота
current_steps = 0
last_entry_diff = 0
last_update_time = 0

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": f"[{SYMBOL}] {text}", "parse_mode": "Markdown"})
        except: pass

def get_ema(values, span):
    if len(values) < span: return 0
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]: ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def execute_entry(side, price):
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
        send_tg(f"✅ *ВХОД {side}* (Добор). Цена: `{price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка входа: {e}")

def flip_position(new_side, price, reason):
    try:
        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = next((p for p in pos if p['symbol'] == SYMBOL), None)
        old_qty = abs(float(active_pos['positionAmt'])) if active_pos else 0
        
        if old_qty > 0:
            close_side = 'SELL' if new_side == 'SELL' else 'BUY'
            client.futures_create_order(symbol=SYMBOL, side=close_side, type='MARKET', quantity=old_qty, reduceOnly=True)
            send_tg(f"💰 *ЗАКРЫТИЕ ПОЗИЦИИ* ({reason})")
            time.sleep(1)

        new_qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        client.futures_create_order(symbol=SYMBOL, side=new_side, type='MARKET', quantity=new_qty)
        send_tg(f"🚀 *ПЕРЕВОРОТ В {new_side}*. Цена: `{price}`")
    except Exception as e:
        send_tg(f"❌ Ошибка переворота: {e}")

def process_logic(curr_p):
    global current_steps, last_entry_diff, last_update_time
    
    # Чтобы не перегружать проц, считаем логику не чаще чем раз в 5 секунд
    if time.time() - last_update_time < 5:
        return
    last_update_time = time.time()

    try:
        # Получаем историю для EMA (через API, но редко)
        klines = client.futures_klines(symbol=SYMBOL, interval='1m', limit=50)
        closes = [float(k[4]) for k in klines[:-1]]
        
        f_now = get_ema(closes, 7)
        s_now = get_ema(closes, 25)
        diff = (f_now - s_now) / s_now 

        pos = client.futures_position_information(symbol=SYMBOL)
        active_pos = next((p for p in pos if p['symbol'] == SYMBOL and float(p['positionAmt']) != 0), None)
        amt = float(active_pos['positionAmt']) if active_pos else 0

        # --- ТВОЯ ЛОГИКА КАЧЕЛЕЙ ---
        if amt == 0:
            current_steps = 0
            if diff <= -THRESHOLD: 
                execute_entry('BUY', curr_p)
                last_entry_diff, current_steps = diff, 1
            elif diff >= THRESHOLD:
                execute_entry('SELL', curr_p)
                last_entry_diff, current_steps = diff, 1

        elif amt > 0: # LONG
            if diff <= (last_entry_diff - STEP_DIFF) and current_steps < MAX_STEPS:
                execute_entry('BUY', curr_p)
                last_entry_diff, current_steps = diff, current_steps + 1
                send_tg(f"📉 Усреднение ЛОНГА №{current_steps}")
            elif diff >= THRESHOLD:
                flip_position('SELL', curr_p, "Верхний пик")
                last_entry_diff, current_steps = diff, 1

        elif amt < 0: # SHORT
            if diff >= (last_entry_diff + STEP_DIFF) and current_steps < MAX_STEPS:
                execute_entry('SELL', curr_p)
                last_entry_diff, current_steps = diff, current_steps + 1
                send_tg(f"📈 Усреднение ШОРТА №{current_steps}")
            elif diff <= -THRESHOLD:
                flip_position('BUY', curr_p, "Нижний пик")
                last_entry_diff, current_steps = diff, 1

    except Exception as e:
        print(f"Ошибка логики: {e}")

# --- SOCKET МЕНЕДЖЕР ---
def run_websocket():
    # Создаем менеджер сокетов
    ubwa = BinanceWebSocketApiManager(exchange="binance.com-futures")
    ubwa.create_stream(['kline_1m'], [SYMBOL.lower()])
    
    log_msg = f"🔌 Сокет запущен для {SYMBOL}. Слушаю эфир..."
    print(log_msg)
    send_tg(log_msg)

    while True:
        if ubwa.is_update_available():
            oldest_data = ubwa.pop_stream_data_from_stream_buffer()
            if oldest_data:
                data = json.loads(oldest_data)
                if 'data' in data and 'k' in data['data']:
                    curr_p = float(data['data']['k']['c'])
                    process_logic(curr_p)
        else:
            time.sleep(0.1)

# Запуск сокета в отдельном потоке
threading.Thread(target=run_websocket, daemon=True).start()

@app.route('/')
def health(): return "Snake Bot is Online (WebSocket Mode)"

if __name__ == "__main__":
    # На бесплатном Render важен PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
