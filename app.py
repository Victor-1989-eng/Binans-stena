import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket 

app = Flask(__name__)

# ================= НАСТРОЙКИ (ТВОИ ЛЮБИМЫЕ) =================
SYMBOL_UPPER = "SOLUSDT"
SYMBOL_LOWER = "solusdt" 

ENTRY_THRESHOLD = 0.009    # 0.3% разрыва для входа
STEP_DIFF = 0.003          # 0.2% разрыва для усреднения
MAX_STEPS = 9              # Лимит усреднений
EXIT_THRESHOLD = 0.002     # 0.1% после пересечения для выхода

LEVERAGE = 30              
MARGIN_STEP = 4.0          
# ============================================================

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
closes = []
last_log_time = 0
current_steps = 0      
last_entry_gap = 0     

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
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
        f"📈 *Шагов сетки:* `{steps}`\n"
        f"🏁 *Gap на выходе:* `{gap:.5f}`\n"
        f"✨ *Профит в копилке!*"
    )
    send_tg(msg)

# Проверенная формула EMA из твоего идеального кода
def get_ema(values, span):
    if len(values) < span: return values[-1]
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]: ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def execute_order(side, step_num, gap):
    try:
        # Настройка аккаунта перед сделкой (как в старом коде)
        try: client.futures_change_margin_type(symbol=SYMBOL_UPPER, marginType='CROSSED')
        except: pass
        client.futures_change_leverage(symbol=SYMBOL_UPPER, leverage=LEVERAGE)

        price = closes[-1]
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        if qty < 0.1: qty = 0.1
        
        client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
        tg_report_entry(side, step_num, price, gap)
        return True
    except Exception as e:
        send_tg(f"❌ *ОШИБКА ОРДЕРА*: `{e}`")
        return False

def process_candle(close_price):
    global closes, last_log_time, current_steps, last_entry_gap
    
    closes.append(close_price)
    if len(closes) > 60: closes.pop(0)
    if len(closes) < 26: return

    # Расчет Gap на основе EMA
    f_now = get_ema(closes, 7)
    s_now = get_ema(closes, 25)
    gap = (f_now - s_now) / s_now 

    if time.time() - last_log_time > 60:
        print(f"💓 LIVE: {close_price} | Gap: {gap:.5f} | Step: {current_steps}", flush=True)
        last_log_time = time.time()

    try:
        # Получаем позицию с защитой от ошибок
        pos_info = client.futures_position_information(symbol=SYMBOL_UPPER)
        if not isinstance(pos_info, list): return
        my_pos = next((p for p in pos_info if p['symbol'] == SYMBOL_UPPER), None)
        amt = float(my_pos['positionAmt']) if my_pos else 0
        
        # --- ЛОГИКА ТОРГОВЛИ ---
        if amt == 0:
            current_steps = 0
            if gap <= -ENTRY_THRESHOLD:
                if execute_order('BUY', 1, gap):
                    current_steps, last_entry_gap = 1, gap
            elif gap >= ENTRY_THRESHOLD:
                if execute_order('SELL', 1, gap):
                    current_steps, last_entry_gap = 1, gap

        elif amt > 0: # LONG сопровождение
            if gap <= (last_entry_gap - STEP_DIFF) and current_steps < MAX_STEPS:
                if execute_order('BUY', current_steps + 1, gap):
                    current_steps += 1
                    last_entry_gap = gap
            elif gap >= EXIT_THRESHOLD:
                client.futures_create_order(symbol=SYMBOL_UPPER, side='SELL', type='MARKET', quantity=amt, reduceOnly=True)
                tg_report_close("LONG", current_steps, gap)
                current_steps = 0

        elif amt < 0: # SHORT сопровождение
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
        if js['k']['x']: # Срабатывает только на закрытии минутки
            process_candle(float(js['k']['c']))
        elif int(time.time()) % 20 == 0: 
            print(f"👀 {js['k']['c']}", flush=True)
    
    ws = websocket.WebSocketApp(url, on_message=on_msg, on_error=lambda w,e: print(f"Socket Err: {e}"), 
                                on_close=lambda w,a,b: [time.sleep(5), start_socket()])
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def idx(): 
    status = "OK" if len(closes) >= 26 else "WAITING_HISTORY"
    return f"Snake Bot 5.3 Stable. Status: {status} ({len(closes)}/60)"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
