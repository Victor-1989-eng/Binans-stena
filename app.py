import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket 

app = Flask(__name__)

# ================= НАСТРОЙКИ (ОХОТНИК V6.2.1) =================
SYMBOL_UPPER = "SOLUSDT"
SYMBOL_LOWER = "solusdt" 

ENTRY_MIN_GAP = 0.003      # 0.3%
EXIT_MIN_GAP = 0.0005      # 0.05%
PULLBACK_RATE = 0.07       # 12%
REVERSE_LEVEL_COEFF = 2.0  
LEVERAGE = 30              
MARGIN_STEP = 10.0         
# =====================================================================

# Инициализация клиента
api_key = os.environ.get("BINANCE_API_KEY")
api_secret = os.environ.get("BINANCE_API_SECRET")
client = Client(api_key, api_secret)

closes = []
last_log_time = 0
peak_gap = 0               
stats = {"total_trades": 0}
current_amt = 0
last_pos_check = 0

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except Exception as e:
            print(f"TG Error: {e}")

def get_ema(values, span):
    if len(values) < span: return values[-1]
    alpha = 2 / (span + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * alpha) + (ema * (1 - alpha))
    return ema

def execute_order(side, gap):
    global current_amt, last_pos_check
    try:
        # Пытаемся установить тип маржи и плечо
        try:
            client.futures_change_margin_type(symbol=SYMBOL_UPPER, marginType='ISOLATED')
        except:
            pass 
        
        client.futures_change_leverage(symbol=SYMBOL_UPPER, leverage=LEVERAGE)
        
        price = closes[-1]
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2)
        if qty < 0.1: qty = 0.1
        
        client.futures_create_order(symbol=SYMBOL_UPPER, side=side, type='MARKET', quantity=qty)
        
        last_pos_check = 0 # Сброс для проверки позиции
        
        icon = "🟢" if side == "BUY" else "🔴"
        send_tg(f"{icon} *ВХОД {side}*\n📐 Gap: `{gap:.5f}`\n💵 Цена: `{price}`")
        return True
    except Exception as e:
        send_tg(f"❌ *ОШИБКА ВХОДА*: `{e}`")
        return False

def process_candle(close_price):
    global closes, last_log_time, peak_gap, current_amt, last_pos_check
    
    closes.append(close_price)
    if len(closes) > 100: closes.pop(0) 
    if len(closes) < 26: return 

    f_now = get_ema(closes, 7)
    s_now = get_ema(closes, 25)
    gap = (f_now - s_now) / s_now 

    if time.time() - last_log_time > 60:
        print(f"💓 LIVE: {close_price} | Gap: {gap:.5f} | Peak: {peak_gap:.5f}", flush=True)
        last_log_time = time.time()

    try:
        # Проверка позиции раз в 10 минут
        if time.time() - last_pos_check > 600:
            pos_info = client.futures_position_information(symbol=SYMBOL_UPPER)
            my_pos = next((p for p in pos_info if p['symbol'] == SYMBOL_UPPER), None)
            current_amt = float(my_pos['positionAmt']) if my_pos else 0
            last_pos_check = time.time()
            print(f"🔄 Синхронизация: {current_amt}")

        amt = current_amt
        
        if amt == 0:
            if gap >= ENTRY_MIN_GAP:
                if gap > peak_gap: peak_gap = gap
                elif gap < peak_gap * (1 - PULLBACK_RATE):
                    if execute_order('SELL', gap): peak_gap = 0
            elif gap <= -ENTRY_MIN_GAP:
                if gap < peak_gap: peak_gap = gap
                elif gap > peak_gap * (1 - PULLBACK_RATE):
                    if execute_order('BUY', gap): peak_gap = 0
            else:
                peak_gap = 0

        elif amt > 0: # LONG
            reverse_level = -ENTRY_MIN_GAP * REVERSE_LEVEL_COEFF
            if peak_gap >= EXIT_MIN_GAP and gap <= reverse_level:
                 client.futures_create_order(symbol=SYMBOL_UPPER, side='SELL', type='MARKET', quantity=amt, reduceOnly=True)
                 last_pos_check = 0 
                 send_tg(f"⚠️ *РЕВЕРС LONG*")
                 peak_gap = 0 
            elif gap >= EXIT_MIN_GAP:
                if gap > peak_gap: peak_gap = gap
                elif gap < peak_gap * (1 - PULLBACK_RATE):
                    client.futures_create_order(symbol=SYMBOL_UPPER, side='SELL', type='MARKET', quantity=amt, reduceOnly=True)
                    last_pos_check = 0 
                    stats["total_trades"] += 1
                    send_tg(f"💰 *ФИКС ЛОНГ*")
                    peak_gap = 0

        elif amt < 0: # SHORT
            reverse_level = ENTRY_MIN_GAP * REVERSE_LEVEL_COEFF
            if peak_gap <= -EXIT_MIN_GAP and gap >= reverse_level:
                 client.futures_create_order(symbol=SYMBOL_UPPER, side='BUY', type='MARKET', quantity=abs(amt), reduceOnly=True)
                 last_pos_check = 0
                 send_tg(f"⚠️ *РЕВЕРС SHORT*")
                 peak_gap = 0
            elif gap <= -EXIT_MIN_GAP:
                if gap < peak_gap: peak_gap = gap
                elif gap > peak_gap * (1 - PULLBACK_RATE):
                    client.futures_create_order(symbol=SYMBOL_UPPER, side='BUY', type='MARKET', quantity=abs(amt), reduceOnly=True)
                    last_pos_check = 0
                    stats["total_trades"] += 1
                    send_tg(f"💰 *ФИКС ШОРТ*")
                    peak_gap = 0

    except Exception as e:
        print(f"⚠️ Ошибка логики: {e}", flush=True)

def start_socket():
    url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    def on_message(ws, msg):
        js = json.loads(msg)
        if js['k']['x']: process_candle(float(js['k']['c']))
    def on_error(ws, err): print(f"WS Error: {err}")
    def on_close(ws, c, m): 
        print("WS Closed. Reconnecting...")
        time.sleep(5)
        start_socket()
    
    ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
    ws.run_forever()

threading.Thread(target=start_socket, daemon=True).start()

@app.route('/')
def idx():
    try:
        current_ip = requests.get('https://api.ipify.org').text
        start = time.time()
        client.futures_ping()
        latency = (time.time() - start) * 1000
        return f"<h1>Snake V6.2.1</h1><p>IP: {current_ip}</p><p>Ping: {latency:.2f} ms</p>"
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
