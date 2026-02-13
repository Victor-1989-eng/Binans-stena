import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket 

app = Flask(__name__)

# ================= НАСТРОЙКИ (ТВОИ НОВЫЕ БЕЗОПАСНЫЕ) =================
SYMBOL_UPPER = "SOLUSDT"
SYMBOL_LOWER = "solusdt" 

ENTRY_THRESHOLD = 0.003    # Твой вход на 0.002
STEP_DIFF = 0.005          # Усреднение через каждые 0.001
MAX_STEPS = 2              
EXIT_THRESHOLD = 0.0005     # Выход: пролет на 0.001 за среднюю

LEVERAGE = 30              # Безопасное плечо x10
MARGIN_STEP = 3.0          # Маржа 1$ (итого 10$ в рынке на шаг)
# ============================================================

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
closes = []
last_log_time = 0
current_steps = 0      
last_entry_gap = 0     

# --- ПЕРЕМЕННЫЕ ДЛЯ СТАТИСТИКИ ---
stats = {
    "entry_gaps": [],
    "exit_overshoots": [],
    "total_trades": 0
}

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

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
    # Считаем средние показатели
    avg_entry = sum(stats["entry_gaps"]) / len(stats["entry_gaps"]) if stats["entry_gaps"] else 0
    avg_exit = sum(stats["exit_overshoots"]) / len(stats["exit_overshoots"]) if stats["exit_overshoots"] else 0
    
    msg = (
        f"💰 *ФИКСАЦИЯ ПРИБЫЛИ* 💰\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ *Позиция {side} закрыта*\n"
        f"📈 *Шагов сетки:* `{steps}`\n"
        f"🏁 *Gap на выходе:* `{gap:.5f}`\n"
        f"📊 *Средний вход (сутки):* `-{abs(avg_entry):.4f}`\n"
        f"🎯 *Средний пролет (сутки):* `+{abs(avg_exit):.4f}`\n"
        f"🔢 *Всего сделок:* `{stats['total_trades']}`\n"
        f"✨ *Профит в копилке!*"
    )
    send_tg(msg)

def get_ema(values, span):
    if len(values) < span: return values[-1]
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]: ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def execute_order(side, step_num, gap):
    try:
        try: client.futures_change_margin_type(symbol=SYMBOL_UPPER, marginType='CROSSED')
        except: pass
        client.futures_change_leverage(symbol=SYMBOL_UPPER, leverage=LEVERAGE)

        price = closes[-1]
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        if qty < 0.1: qty = 0.1
        
        client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
        
        # Записываем стат только для первого входа
        if step_num == 1:
            stats["entry_gaps"].append(gap)
            
        tg_report_entry(side, step_num, price, gap)
        return True
    except Exception as e:
        send_tg(f"❌ *ОШИБКА ОРДЕРА*: `{e}`")
        return False

def process_candle(close_price):
    global closes, last_log_time, current_steps, last_entry_gap
    
    closes.append(close_price)
    if len(closes) > 100: closes.pop(0) # Увеличил до 100, чтобы EMA 99 работала, если захочешь
    if len(closes) < 26: return

    # Твои любимые 7 и 25
    f_now = get_ema(closes, 7)
    s_now = get_ema(closes, 25)
    gap = (f_now - s_now) / s_now 

    if time.time() - last_log_time > 60:
        print(f"💓 LIVE: {close_price} | Gap: {gap:.5f} | Step: {current_steps}", flush=True)
        last_log_time = time.time()

    try:
        pos_info = client.futures_position_information(symbol=SYMBOL_UPPER)
        my_pos = next((p for p in pos_info if p['symbol'] == SYMBOL_UPPER), None)
        amt = float(my_pos['positionAmt']) if my_pos else 0
        
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
                stats["exit_overshoots"].append(gap)
                stats["total_trades"] += 1
                tg_report_close("LONG", current_steps, gap)
                current_steps = 0

        elif amt < 0: # SHORT
            if gap >= (last_entry_gap + STEP_DIFF) and current_steps < MAX_STEPS:
                if execute_order('SELL', current_steps + 1, gap):
                    current_steps += 1
                    last_entry_gap = gap
            elif gap <= -EXIT_THRESHOLD:
                client.futures_create_order(symbol=SYMBOL_UPPER, side='BUY', type='MARKET', quantity=abs(amt), reduceOnly=True)
                stats["exit_overshoots"].append(gap)
                stats["total_trades"] += 1
                tg_report_close("SHORT", current_steps, gap)
                current_steps = 0

    except Exception as e:
        print(f"⚠️ Ошибка: {e}", flush=True)

def start_socket():
    url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    def on_msg(ws, msg):
        js = json.loads(msg)
        if js['k']['x']: 
            process_candle(float(js['k']['c']))
    
    ws = websocket.WebSocketApp(url, on_message=on_msg, on_error=lambda w,e: print(f"Socket Err: {e}"), 
                                on_close=lambda w,a,b: [time.sleep(5), start_socket()])
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def idx(): 
    return f"Snake Bot 5.4 Stats Edition. Total Trades: {stats['total_trades']}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
