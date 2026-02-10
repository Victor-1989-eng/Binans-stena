import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket 

app = Flask(__name__)

# ================= НАСТРОЙКИ =================
SYMBOL_UPPER = "SOLUSDC"
SYMBOL_LOWER = "solusdc" 

ENTRY_THRESHOLD = 0.003    
STEP_DIFF = 0.002          
MAX_STEPS = 5              
EXIT_THRESHOLD = 0.001     

LEVERAGE = 30              
MARGIN_STEP = 1.0          
# =============================================

def get_client():
    try: return Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    except: return None

client = get_client()
closes = []
last_log_time = 0
current_steps = 0      
last_entry_gap = 0     

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            # Отправляем с MarkdownV2 или простым Markdown
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                          json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

# --- ФУНКЦИИ КРАСИВЫХ СООБЩЕНИЙ ---
def tg_report_entry(side, step, price, gap):
    icon = "🟢" if side == "BUY" else "🔴"
    title = "ВХОД В ПОЗИЦИЮ" if step == 1 else "УСРЕДНЕНИЕ (ДОБОР)"
    msg = (
        f"{icon} *{title}* {icon}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔹 *Инструмент:* `{SYMBOL_UPPER}`\n"
        f"🔹 *Тип:* `{side}` (Шаг {step})\n"
        f"💵 *Цена:* `{price}`\n"
        f"📐 *Gap (Пружина):* `{gap:.5f}`\n"
        f"🚀 *Плечо:* `x{LEVERAGE}`\n"
        f"━━━━━━━━━━━━━━━"
    )
    send_tg(msg)

def tg_report_close(side, steps, gap):
    msg = (
        f"💰 *ФИКСАЦИЯ ПРИБЫЛИ* 💰\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ *Позиция {side} закрыта*\n"
        f"📈 *Всего усреднений:* `{steps}`\n"
        f"🏁 *Gap на выходе:* `{gap:.5f}`\n"
        f"✨ *Пружина сработала!*"
    )
    send_tg(msg)

def calculate_ema(prices, days):
    if len(prices) < days: return prices[-1]
    ema = prices[0]
    k = 2 / (days + 1)
    for price in prices[1:]: ema = (price - ema) * k + ema
    return ema

def execute_order(side, step_num, current_gap):
    try:
        price = closes[-1]
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        if qty < 0.1: qty = 0.1
        
        client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
        
        # Вызываем красивый отчет
        tg_report_entry(side, step_num, price, current_gap)
        return True
    except Exception as e:
        send_tg(f"❌ *ОШИБКА ОРДЕРА*\n`{e}`")
        return False

def process_candle(close_price):
    global closes, last_log_time, current_steps, last_entry_gap, client
    if not client: client = get_client(); return

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
        pos_info = client.futures_position_information(symbol=SYMBOL_UPPER)
        if pos_info is None or not isinstance(pos_info, list): return
        my_pos = next((p for p in pos_info if p['symbol'] == SYMBOL_UPPER), None)
        if not my_pos: return
        
        amt = float(my_pos['positionAmt'])
        
        # --- ЛОГИКА ---
        if amt == 0:
            current_steps = 0
            if gap <= -ENTRY_THRESHOLD:
                if execute_order('BUY', 1, gap):
                    current_steps, last_entry_gap = 1, gap
            elif gap >= ENTRY_THRESHOLD:
                if execute_order('SELL', 1, gap):
                    current_steps, last_entry_gap = 1, gap

        elif amt > 0: # LONG
            if gap <= (last_entry_gap - STEP_DIFF) and current_steps < MAX_STEPS:
                if execute_order('BUY', current_steps + 1, gap):
                    current_steps += 1
                    last_entry_gap = gap
            elif gap >= EXIT_THRESHOLD:
                client.futures_create_order(symbol=SYMBOL_UPPER, side='SELL', type='MARKET', quantity=amt, reduceOnly=True)
                tg_report_close("LONG", current_steps, gap)
                current_steps = 0

        elif amt < 0: # SHORT
            if gap >= (last_entry_gap + STEP_DIFF) and current_steps < MAX_STEPS:
                if execute_order('SELL', current_steps + 1, gap):
                    current_steps += 1
                    last_entry_gap = gap
            elif gap <= -EXIT_THRESHOLD:
                client.futures_create_order(symbol=SYMBOL_UPPER, side='BUY', type='MARKET', quantity=abs(amt), reduceOnly=True)
                tg_report_close("SHORT", current_steps, gap)
                current_steps = 0

    except Exception as e:
        print(f"⚠️ Ошибка: {e}", flush=True)

def start_socket():
    url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    def on_msg(ws, msg):
        js = json.loads(msg)
        if js['k']['x']: process_candle(float(js['k']['c']))
        elif int(time.time()) % 20 == 0: print(f"👀 {js['k']['c']}", flush=True)
    
    ws = websocket.WebSocketApp(url, on_message=on_msg, on_error=lambda w,e: print(f"Socket Error: {e}"), 
                                on_close=lambda w,a,b: [time.sleep(5), start_socket()])
    print(f"✅ Сокет запущен: {SYMBOL_LOWER}", flush=True)
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def idx(): return "Snake Bot 5.2 Luxury TG Running"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
