import os, json, time, threading, requests
from flask import Flask
from binance.client import Client
import websocket # pip install websocket-client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL_UPPER = "SOLUSDC"
SYMBOL_LOWER = "solusdc" 
THRESHOLD = 0.002       # 0.2%
LEVERAGE = 20           
MARGIN_STEP = 1.0       

# Клиент для ордеров
try:
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
except:
    print("⚠️ API ключи не найдены или неверны")

# Память
closes = []
last_log_time = 0

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": f"[{SYMBOL_UPPER}] {text}", "parse_mode": "Markdown"})
        except: pass

# --- ПРОСТАЯ МАТЕМАТИКА EMA ---
def calculate_ema(prices, days):
    if len(prices) < days: return prices[-1]
    ema = prices[0]
    multiplier = 2 / (days + 1)
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

# --- ЛОГИКА ---
def process_candle(close_price):
    global closes, last_log_time
    
    closes.append(close_price)
    if len(closes) > 50: closes.pop(0)

    # Пока копим историю - просто пишем пульс
    if len(closes) < 10:
        print(f"📥 Накапливаю историю: {len(closes)}/26 свечей...", flush=True)
        return

    # Считаем
    f_now = calculate_ema(closes, 7)
    s_now = calculate_ema(closes, 25)
    diff = (f_now - s_now) / s_now 

    # Лог раз в минуту или при сильном сигнале
    if time.time() - last_log_time > 60:
        msg = f"💓 ПУЛЬС: Цена {close_price} | Gap: {diff:.5f} (Порог {THRESHOLD})"
        print(msg, flush=True) # flush=True заставляет Render писать лог сразу!
        last_log_time = time.time()

    # ТУТ ТВОЯ ЛОГИКА ВХОДОВ (упрощенно для теста)
    if abs(diff) >= THRESHOLD:
        print(f"🔥 СИГНАЛ! Gap: {diff:.5f}", flush=True)
        # execute_entry(...) - раскомментируешь, когда увидишь логи

# --- ПРЯМОЙ СОКЕТ (Самый надежный метод) ---
def on_message(ws, message):
    try:
        json_msg = json.loads(message)
        kline = json_msg['k']
        is_closed = kline['x'] # Свеча закрылась?
        current_price = float(kline['c'])
        
        # ЧТОБЫ ТЫ УВИДЕЛ, ЧТО ОН ЖИВОЙ:
        # Пишем в лог каждые 10 секунд даже если свеча не закрыта
        if int(time.time()) % 10 == 0:
             print(f"👀 Тик цены: {current_price}", flush=True)

        if is_closed:
            print(f"🕯 Свеча ЗАКРЫТА: {current_price}", flush=True)
            process_candle(current_price)
            
    except Exception as e:
        print(f"Ошибка чтения: {e}", flush=True)

def on_error(ws, error):
    print(f"❌ Ошибка сокета: {error}", flush=True)

def on_close(ws, close_status_code, close_msg):
    print("⚠️ Сокет закрыт. Перезапускаю через 5 сек...", flush=True)
    time.sleep(5)
    start_socket()

def on_open(ws):
    print("✅ Соединение с Binance установлено! Полетели данные...", flush=True)
    send_tg("Бот перезагружен и подключен к потоку!")

def start_socket():
    # Прямая ссылка на стрим фьючерсов
    socket_url = f"wss://fstream.binance.com/ws/{SYMBOL_LOWER}@kline_1m"
    ws = websocket.WebSocketApp(socket_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.run_forever()

# Запускаем в фоне
threading.Thread(target=start_socket, daemon=True).start()

# --- FLASK ---
@app.route('/')
def index(): return "Snake Bot is Alive"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
