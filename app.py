import os, time, threading, requests, json
from flask import Flask
from binance.client import Client
from unicorn_binance_websocket_api.manager import BinanceWebSocketApiManager

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOL = "SOLUSDC"
# СНИЗИЛИ ПОРОГ ДЛЯ ТЕСТА!
THRESHOLD = 0.002       # 0.2% (Попробуем так, чтобы он начал заходить)
STEP_DIFF = 0.002       
MAX_STEPS = 6           
LEVERAGE = 20           # Поставь 10-20 для безопасности!
MARGIN_STEP = 1.0       # Размер первого входа в $

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

# Память
current_steps = 0
last_entry_diff = 0
last_log_time = 0

def send_tg(text):
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if token and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": chat_id, "text": f"[{SYMBOL}] {text}", "parse_mode": "Markdown"})
        except: pass

def get_ema(values, span):
    if len(values) < span: return values[-1] # Если мало данных, возвращаем последнюю цену
    series = pd.Series(values)
    return series.ewm(span=span, adjust=False).mean().iloc[-1]

# Чтобы не тянуть pandas ради одной функции, простая математика:
def calculate_ema(prices, days, smoothing=2):
    ema = [sum(prices[:days]) / days]
    for price in prices[days:]:
        ema.append((price * (smoothing / (1 + days))) + (ema[-1] * (1 - (smoothing / (1 + days)))))
    return ema[-1]

def execute_entry(side, price):
    try:
        # client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE) # Раскомментируй если надо менять плечо каждый раз
        qty = round((MARGIN_STEP * LEVERAGE) / price, 2) # Округлим до 2 знаков, для SOL пойдет
        # Проверка минимального лота (для SOL это обычно 1 монета на споте, на фьючах меньше)
        if qty < 0.1: qty = 0.1 
        
        client.futures_create_order(symbol=SYMBOL, side=side, type='MARKET', quantity=qty)
        send_tg(f"✅ *ВХОД {side}* | Цена: `{price}` | Объем: {qty}")
    except Exception as e:
        print(f"Ошибка ордера: {e}")
        send_tg(f"❌ Не смог войти: {e}")

def process_logic(curr_p, closes):
    global current_steps, last_entry_diff, last_log_time
    
    # 1. Считаем индикаторы
    if len(closes) < 26: return # Ждем накопления истории
    
    # Ручной расчет EMA без pandas (быстрее и легче)
    f_now = calculate_ema(closes, 7)
    s_now = calculate_ema(closes, 25)
    
    diff = (f_now - s_now) / s_now 
    
    # --- ЛОГГЕР "ПУЛЬС" (Каждую минуту пишет в лог Render) ---
    if time.time() - last_log_time > 60:
        print(f"💓 ПУЛЬС: Цена {curr_p} | EMA7: {f_now:.2f} | EMA25: {s_now:.2f} | GAP: {diff:.5f} (Порог {THRESHOLD})")
        # Если GAP очень близко, но не дотягивает - напишем в ТГ
        if abs(diff) > (THRESHOLD * 0.8):
           pass # Можно включить send_tg(f"👀 Присматриваюсь... Gap: {diff:.5f}")
        last_log_time = time.time()

    # Получаем позицию (этот запрос может замедлять, лучше хранить локально, но для надежности оставим)
    try:
        # ВНИМАНИЕ: Часто долбить API нельзя. 
        # Логику входа проверяем по GAP, а позицию проверяем только если GAP сработал?
        # Нет, нам надо знать направление. 
        # Упростим: считаем что мы знаем позицию, если бот не перезапускался.
        # Но для надежности - запрос.
        pass 
    except: pass

    # --- УПРОЩЕННАЯ ЛОГИКА ДЛЯ ТЕСТА ---
    # Давай проверим просто вход, работает ли он вообще
    if abs(diff) >= THRESHOLD:
        try:
            pos = client.futures_position_information(symbol=SYMBOL)
            active_pos = next((p for p in pos if p['symbol'] == SYMBOL), None)
            amt = float(active_pos['positionAmt']) if active_pos else 0
            
            # ВХОД LONG
            if amt == 0 and diff <= -THRESHOLD:
                execute_entry('BUY', curr_p)
                current_steps = 1
                last_entry_diff = diff
                
            # ВХОД SHORT
            elif amt == 0 and diff >= THRESHOLD:
                execute_entry('SELL', curr_p)
                current_steps = 1
                last_entry_diff = diff
                
            # (Тут можно добавить логику доборов, но давай сначала добьемся первого входа!)
            
        except Exception as e:
            print(f"Ошибка API: {e}")

# --- SOCKET ---
def run_websocket():
    ubwa = BinanceWebSocketApiManager(exchange="binance.com-futures")
    ubwa.create_stream(['kline_1m'], [SYMBOL.lower()])
    print(f"🔌 Сокет запущен. Жду свечей...")
    
    # Накапливаем историю вручную, чтобы не зависеть от тяжелых запросов
    closes_history = [] 

    while True:
        if ubwa.is_update_available():
            oldest_data = ubwa.pop_stream_data_from_stream_buffer()
            if oldest_data:
                data = json.loads(oldest_data)
                try:
                    if 'data' in data and 'k' in data['data']:
                        kline = data['data']['k']
                        is_closed = kline['x'] # Свеча закрылась?
                        close_p = float(kline['c'])
                        
                        # Добавляем в историю ТОЛЬКО закрытые свечи для точного EMA
                        if is_closed:
                            closes_history.append(close_p)
                            if len(closes_history) > 50: closes_history.pop(0)
                            
                            # Запускаем логику
                            process_logic(close_p, closes_history)
                            print(f"Свеча закрыта: {close_p}")
                        
                except Exception as e:
                    print(f"Ошибка парсинга: {e}")
        else:
            time.sleep(0.01)

threading.Thread(target=run_websocket, daemon=True).start()

@app.route('/')
def health(): return "Snake Bot Debug Mode"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
