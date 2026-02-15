import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket 

app = Flask(__name__)

# ================= НАСТРОЙКИ (ИЗОЛИРОВАННАЯ МАРЖА / БЕЗ УСРЕДНЕНИЯ) =================
SYMBOL_UPPER = "SOLUSDT"
SYMBOL_LOWER = "solusdt" 

ENTRY_MIN_GAP = 0.002      # Начинаем слежку для ВХОДА
EXIT_MIN_GAP = 0.0005      # Начинаем слежку для ВЫХОДА (пролет за среднюю)
PULLBACK_RATE = 0.10       # Откат 10% от пика для входа/выхода

LEVERAGE = 30              
MARGIN_STEP = 10.0          # Сумма одной сделки (с плечом x30 это 300$ в рынке)
# ===================================================================================

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
closes = []
last_log_time = 0
peak_gap = 0               # Трекер пика

stats = {"entry_gaps": [], "exit_overshoots": [], "total_trades": 0}

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except: pass

def get_ema(values, span):
    if len(values) < span: return values[-1]
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]: ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def execute_order(side, gap):
    try:
        # УСТАНОВКА ИЗОЛИРОВАННОЙ МАРЖИ
        try: client.futures_change_margin_type(symbol=SYMBOL_UPPER, marginType='ISOLATED')
        except: pass # Если уже изолированная, пропустит
        
        client.futures_change_leverage(symbol=SYMBOL_UPPER, leverage=LEVERAGE)
        
        price = closes[-1]
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        if qty < 0.1: qty = 0.1
        
        client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
        stats["entry_gaps"].append(gap)
        
        icon = "🟢" if side == "BUY" else "🔴"
        send_tg(f"{icon} *ВХОД {side}*\n📐 Gap: `{gap:.5f}`\n💵 Цена: `{price}`")
        return True
    except Exception as e:
        send_tg(f"❌ *ОШИБКА ВХОДА*: `{e}`"); return False

def process_candle(close_price):
    global closes, last_log_time, peak_gap
    
    closes.append(close_price)
    if len(closes) > 150: closes.pop(0) 
    if len(closes) < 99: return 

    # Твои 25 и 99
    f_now = get_ema(closes, 25)
    s_now = get_ema(closes, 99)
    gap = (f_now - s_now) / s_now 

    if time.time() - last_log_time > 60:
        print(f"💓 LIVE: {close_price} | Gap: {gap:.5f} | Peak: {peak_gap:.5f}", flush=True)
        last_log_time = time.time()

    try:
        pos_info = client.futures_position_information(symbol=SYMBOL_UPPER)
        my_pos = next((p for p in pos_info if p['symbol'] == SYMBOL_UPPER), None)
        amt = float(my_pos['positionAmt']) if my_pos else 0
        
        # --- ЛОГИКА ВХОДА (СБЛИЖЕНИЕ) ---
        if amt == 0:
            if gap >= ENTRY_MIN_GAP: # Ищем пик для SELL
                if gap > peak_gap: peak_gap = gap
                elif gap < peak_gap * (1 - PULLBACK_RATE):
                    if execute_order('SELL', gap): peak_gap = 0
            
            elif gap <= -ENTRY_MIN_GAP: # Ищем пик для BUY
                if gap < peak_gap: peak_gap = gap
                elif gap > peak_gap * (1 - PULLBACK_RATE):
                    if execute_order('BUY', gap): peak_gap = 0
            else:
                peak_gap = 0

        # --- ЛОГИКА ВЫХОДА (СБЛИЖЕНИЕ) ---
        elif amt > 0: # В ЛОНГЕ
            if gap >= EXIT_MIN_GAP:
                if gap > peak_gap: peak_gap = gap
                elif gap < peak_gap * (1 - PULLBACK_RATE):
                    client.futures_create_order(symbol=SYMBOL_UPPER, side='SELL', type='MARKET', quantity=amt, reduceOnly=True)
                    stats["total_trades"] += 1
                    send_tg(f"💰 *ФИКС ЛОНГ*\n🏁 Gap: `{gap:.5f}`")
                    peak_gap = 0 # Сразу готов к новому входу

        elif amt < 0: # В ШОРТЕ
            if gap <= -EXIT_MIN_GAP:
                if gap < peak_gap: peak_gap = gap
                elif gap > peak_gap * (1 - PULLBACK_RATE):
                    client.futures_create_order(symbol=SYMBOL_UPPER, side='BUY', type='MARKET', quantity=abs(amt), reduceOnly=True)
                    stats["total_trades"] += 1
                    send_tg(f"💰 *ФИКС ШОРТ*\n🏁 Gap: `{gap:.5f}`")
                    peak_gap = 0 # Сразу готов к новому входу

    except Exception as e:
        print(f"⚠️ Ошибка: {e}", flush=True)

def start_socket():
    url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    def on_msg(ws, msg):
        js = json.loads(msg)
        if js['k']['x']: process_candle(float(js['k']['c']))
    ws = websocket.WebSocketApp(url, on_message=on_msg, on_close=lambda w,a,b: [time.sleep(5), start_socket()])
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def idx(): return f"Snake Bot Isolated. Trades: {stats['total_trades']}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
