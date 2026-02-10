import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket 

app = Flask(__name__)

# ================= НАСТРОЙКИ (МЕНЯЙ ТУТ) =================
SYMBOL_UPPER = "SOLUSDC"
SYMBOL_LOWER = "solusdc" 

# Логика Входа и Усреднения
ENTRY_THRESHOLD = 0.003    # Вход при 0.3%
STEP_DIFF = 0.002          # Шаг усреднения (каждые +0.2% растяжения)
MAX_STEPS = 5              # Максимум колен усреднения

# Логика Выхода
EXIT_THRESHOLD = 0.001     # Выход при 0.1% после пересечения нуля

# Деньги
LEVERAGE = 30              
MARGIN_STEP = 1.0          # Сумма одного входа/усреднения в $
# =========================================================

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

# Состояние бота (память)
closes = []
last_log_time = 0
current_steps = 0      # Текущее кол-во усреднений
last_entry_gap = 0     # На каком Gap был последний вход

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                          json={"chat_id": chat_id, "text": f"🐍 *{SYMBOL_UPPER}*\n{text}", "parse_mode": "Markdown"})
        except Exception as e:
            print(f"Ошибка ТГ: {e}")

def calculate_ema(prices, days):
    if len(prices) < days: return prices[-1]
    ema = prices[0]
    k = 2 / (days + 1)
    for price in prices[1:]: ema = (price - ema) * k + ema
    return ema

def execute_order(side, step_num):
    try:
        price = closes[-1]
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        if qty < 0.1: qty = 0.1
        
        client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
        
        type_str = "🚀 ВХОД" if step_num == 1 else "➕ УСРЕДНЕНИЕ"
        icon = "🟢" if side == "BUY" else "🔴"
        
        send_tg(f"{icon} *{type_str} (Шаг {step_num})*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📍 Направление: `{side}`\n"
                f"💵 Цена: `{price}`\n"
                f"📊 Объем: `{qty} SOL`\n"
                f"📐 Плечо: `x{LEVERAGE}`")
        return True
    except Exception as e:
        send_tg(f"❌ *ОШИБКА ОРДЕРА*\n`{e}`")
        return False

def process_candle(close_price):
    global closes, last_log_time, current_steps, last_entry_gap
    
    closes.append(close_price)
    if len(closes) > 50: closes.pop(0)
    if len(closes) < 26: return

    f_now = calculate_ema(closes, 7)
    s_now = calculate_ema(closes, 25)
    gap = (f_now - s_now) / s_now 

    if time.time() - last_log_time > 60:
        print(f"💓 LIVE: {close_price} | Gap: {gap:.5f} | Step: {current_steps}", flush=True)
        last_log_time = time.time()

    try:
        # Проверяем позицию
        pos_info = client.futures_position_information(symbol=SYMBOL_UPPER)
        my_pos = next((p for p in pos_info if p['symbol'] == SYMBOL_UPPER), None)
        amt = float(my_pos['positionAmt'])
        
        # --- СИТУАЦИЯ: НЕТ ПОЗИЦИИ ---
        if amt == 0:
            current_steps = 0 # Сбрасываем счетчик
            if gap <= -ENTRY_THRESHOLD:
                if execute_order('BUY', 1):
                    current_steps = 1
                    last_entry_gap = gap
            elif gap >= ENTRY_THRESHOLD:
                if execute_order('SELL', 1):
                    current_steps = 1
                    last_entry_gap = gap

        # --- СИТУАЦИЯ: МЫ В ЛОНГЕ ---
        elif amt > 0:
            # 1. Проверка Усреднения (цена тянет Gap еще ниже)
            if gap <= (last_entry_gap - STEP_DIFF) and current_steps < MAX_STEPS:
                if execute_order('BUY', current_steps + 1):
                    current_steps += 1
                    last_entry_gap = gap
            
            # 2. Проверка Выхода (Пружина пересекла 0 и ушла в +0.001)
            elif gap >= EXIT_THRESHOLD:
                client.futures_create_order(symbol=SYMBOL_UPPER, side='SELL', type='MARKET', quantity=amt, reduceOnly=True)
                send_tg(f"💰 *ЗАКРЫТИЕ ЛОНГА*\n━━━━━━━━━━━━━━━\n✅ Профит взят!\n📈 Итого шагов: `{current_steps}`\n🏁 Gap закрытия: `{gap:.4f}`")
                current_steps = 0

        # --- СИТУАЦИЯ: МЫ В ШОРТЕ ---
        elif amt < 0:
            # 1. Проверка Усреднения (цена тянет Gap еще выше)
            if gap >= (last_entry_gap + STEP_DIFF) and current_steps < MAX_STEPS:
                if execute_order('SELL', current_steps + 1):
                    current_steps += 1
                    last_entry_gap = gap

            # 2. Проверка Выхода (Пружина пересекла 0 и ушла в -0.001)
            elif gap <= -EXIT_THRESHOLD:
                client.futures_create_order(symbol=SYMBOL_UPPER, side='BUY', type='MARKET', quantity=abs(amt), reduceOnly=True)
                send_tg(f"💰 *ЗАКРЫТИЕ ШОРТА*\n━━━━━━━━━━━━━━━\n✅ Профит взят!\n📉 Итого шагов: `{current_steps}`\n🏁 Gap закрытия: `{gap:.4f}`")
                current_steps = 0

    except Exception as e:
        print(f"⚠️ Error: {e}", flush=True)

# === SOCKET С РЕКОННЕКТОМ ===
def start_socket():
    url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    
    def on_msg(ws, msg):
        js = json.loads(msg)
        if js['k']['x']: process_candle(float(js['k']['c']))
        elif int(time.time()) % 20 == 0: print(f"👀 {js['k']['c']}", flush=True)
    
    def on_err(ws, err): print(f"Socket Error: {err}", flush=True)
    def on_cls(ws, *args): 
        print("🔌 Соединение потеряно. Переподключение...", flush=True)
        time.sleep(5)
        start_socket()

    ws = websocket.WebSocketApp(url, on_message=on_msg, on_error=on_err, on_close=on_cls)
    print(f"✅ Сокет запущен: {SYMBOL_LOWER}", flush=True)
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def idx(): return "Snake Bot 5.0 Ultra is Running"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
