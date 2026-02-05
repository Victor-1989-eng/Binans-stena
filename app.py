import os, time, requests, sys
import numpy as np
from flask import Flask
from binance.client import Client
from binance.streams import ThreadedWebsocketManager

app = Flask(__name__)

# --- НАСТРОЙКИ v7.9 ---
LEVERAGE = 75
MARGIN_USDC = 1.2 
PROFIT_PERCENT = 0.0045 
EMA_FAST = 7
EMA_SLOW = 99
GAP_THRESHOLD = 0.001 

# Глобальные переменные состояния
active_symbol = None # Если не None, значит мы в сделке
ema_cache = {}       # Хранилище текущих EMA для всех пар: {'BTCUSDC': {'fast': X, 'slow': Y}}
usdc_pairs_info = {} # Информация о точности и лимитах пар

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

# --- МАТЕМАТИКА NUMPY ---
def calculate_initial_ema(values, span):
    """Быстрый расчет EMA через Numpy для старта"""
    values = np.array(values)
    alpha = 2 / (span + 1)
    ema = values[0]
    for value in values[1:]:
        ema = (value * alpha) + (ema * (1 - alpha))
    return ema

def update_ema(prev_ema, close_price, span):
    """Мгновенное обновление EMA при новой свече"""
    alpha = 2 / (span + 1)
    return (close_price * alpha) + (prev_ema * (1 - alpha))

# --- ЛОГИКА БОТА ---
def get_usdc_pairs():
    """Находит все USDC пары и их параметры"""
    info = client.futures_exchange_info()
    pairs = {}
    trading_pairs = []
    
    for s in info['symbols']:
        if s['symbol'].endswith('USDC') and s['status'] == 'TRADING':
            # Фильтр Notional (мин сумма входа)
            min_notional = 5.0
            for f in s['filters']:
                if f['filterType'] == 'NOTIONAL': min_notional = float(f['notional'])
            
            # Проверяем, хватает ли нам баланса (примерно)
            if (MARGIN_USDC * LEVERAGE) >= min_notional:
                pairs[s['symbol']] = {
                    'q_prec': int(s['quantityPrecision']),
                    'p_prec': int(s['pricePrecision']),
                    'min_notional': min_notional
                }
                trading_pairs.append(s['symbol'])
    
    return pairs, trading_pairs

def initialize_market_data(symbols):
    """Скачивает историю и готовит EMA для всех пар"""
    print(f"📊 Загружаю историю для {len(symbols)} пар...")
    
    count = 0
    for symbol in symbols:
        try:
            # Берем 150 свечей для разгона EMA 99
            klines = client.futures_klines(symbol=symbol, interval='1m', limit=150)
            closes = [float(k[4]) for k in klines]
            
            if len(closes) < 100: continue

            # Считаем стартовые EMA через Numpy
            ema_f = calculate_initial_ema(closes, EMA_FAST)
            ema_s = calculate_initial_ema(closes, EMA_SLOW)
            
            ema_cache[symbol] = {'fast': ema_f, 'slow': ema_s, 'prev_fast': ema_f, 'prev_slow': ema_s}
            count += 1
            time.sleep(0.1) # Микро-пауза чтобы не забанили при старте
        except Exception as e:
            print(f"Ошибка иниц. {symbol}: {e}")

    return count

def handle_socket_message(msg):
    """
    Эта функция вызывается биржей КАЖДУЮ СЕКУНДУ для каждой пары.
    Мы реагируем только когда свеча ЗАКРЫВАЕТСЯ (x=True).
    """
    global active_symbol
    
    if msg['e'] != 'kline': return
    kline = msg['k']
    
    # Нам нужны только закрытые свечи для принятия решений
    if not kline['x']: return 
    
    symbol = msg['s']
    close_price = float(kline['c'])
    
    # 1. Если мы уже в сделке, игнорируем сигналы, но следим за Аварией
    if active_symbol:
        if symbol == active_symbol:
            # Проверяем Аварию (обновляем EMA и смотрим пересечение)
            update_bot_memory(symbol, close_price)
            check_emergency_exit(symbol, close_price)
        return

    # 2. Обновляем память бота (EMA)
    if symbol not in ema_cache: return
    update_bot_memory(symbol, close_price)

    # 3. Ищем сигнал на вход
    check_entry_signal(symbol, close_price)

def update_bot_memory(symbol, price):
    """Обновляет значения EMA в памяти"""
    data = ema_cache[symbol]
    
    # Сохраняем "прошлые" значения перед обновлением (для проверки пересечения)
    data['prev_fast'] = data['fast']
    data['prev_slow'] = data['slow']
    
    # Считаем новые
    data['fast'] = update_ema(data['fast'], price, EMA_FAST)
    data['slow'] = update_ema(data['slow'], price, EMA_SLOW)

def check_entry_signal(symbol, price):
    global active_symbol
    data = ema_cache[symbol]
    
    f_now, f_prev = data['fast'], data['prev_fast']
    s_now, s_prev = data['slow'], data['prev_slow']
    
    gap = abs(f_now - s_now) / s_now
    
    side = None
    # Golden Cross (Быстрая пробила медленную снизу вверх)
    if f_prev <= s_prev and f_now > s_now and gap >= GAP_THRESHOLD: side = "LONG"
    # Death Cross (Быстрая пробила медленную сверху вниз)
    elif f_prev >= s_prev and f_now < s_now and gap >= GAP_THRESHOLD: side = "SHORT"
    
    if side:
        execute_trade(symbol, side, price)

def execute_trade(symbol, side, price):
    global active_symbol
    
    # Двойная проверка: точно ли нет позиций?
    try:
        pos = client.futures_position_information(symbol=symbol)
        amt = float(pos[0]['positionAmt'])
        if amt != 0: return # Уже есть позиция
    except: return

    try:
        print(f"⚡ СИГНАЛ {symbol} {side}")
        active_symbol = symbol # Блокируем поиск других сделок
        
        info = usdc_pairs_info[symbol]
        
        # Ставим Макс Плечо
        try:
            brackets = client.futures_leverage_bracket(symbol=symbol)
            max_lev = brackets[0]['brackets'][0]['initialLeverage']
            client.futures_change_leverage(symbol=symbol, leverage=max_lev)
        except: max_lev = LEVERAGE

        # Расчет входа
        qty = round((MARGIN_USDC * max_lev) / price, info['q_prec'])
        
        # ОРДЕР ВХОД
        order = client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        entry_price = float(order['avgPrice'])

        # РАСЧЕТ ТЕЙКА
        dist = entry_price * PROFIT_PERCENT
        tp_price = round(entry_price + dist if side == "LONG" else entry_price - dist, info['p_prec'])
        
        # ОРДЕР ТЕЙК
        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
                                    type='LIMIT', timeInForce='GTC', quantity=qty, price=tp_price, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {symbol}* (Websocket)\nПлечо: {max_lev}x\nЦена: `{entry_price}`\nТейк: `{tp_price}`")
        
        # Запускаем поток слежения за закрытием (чтобы освободить active_symbol)
        active_symbol = symbol

    except Exception as e:
        print(f"Ошибка входа: {e}")
        active_symbol = None

def check_emergency_exit(symbol, price):
    global active_symbol
    # Простейшая проверка: если мы в лонге, а тренд сменился на шорт (EMA пересеклись обратно)
    data = ema_cache[symbol]
    
    # Для этого нужно знать направление нашей позиции. 
    # В упрощенной версии: если линии пересеклись ВООБЩЕ в любую сторону - закрываем.
    # Но лучше проверять позицию через API раз в минуту, а не по сокетам.
    pass 

# Отдельный поток проверяет статус текущей сделки (закрылась или нет)
def position_monitor():
    global active_symbol
    while True:
        if active_symbol:
            try:
                pos = client.futures_position_information(symbol=active_symbol)
                amt = float(pos[0]['positionAmt'])
                if amt == 0:
                    send_tg(f"💰 *Сделка {active_symbol} закрыта!* Ищу новую...")
                    active_symbol = None
                else:
                    # Тут можно добавить логику Аварийного выхода
                    pass
            except: pass
        time.sleep(5)

# --- ЗАПУСК ---
def start_bot():
    global usdc_pairs_info
    
    # 1. Получаем список пар
    usdc_pairs_info, symbols_list = get_usdc_pairs()
    msg_pairs = ", ".join([s.replace('USDC','') for s in symbols_list])
    send_tg(f"🤖 *Завод v7.9 SPEEDSTER*\nПары: {len(symbols_list)} шт.\nТехнология: WebSocket + NumPy\n\n📝 Список: {msg_pairs}")
    
    # 2. Инициализируем историю (REST API)
    count = initialize_market_data(symbols_list)
    send_tg(f"📊 История загружена для {count} пар. Подключаю вебсокеты...")

    # 3. Запускаем Вебсокеты
    twm = ThreadedWebsocketManager(api_key=os.environ.get("BINANCE_API_KEY"), api_secret=os.environ.get("BINANCE_API_SECRET"))
    twm.start()

    # Подписываемся на 1-минутные свечи для ВСЕХ найденных пар
    streams = [f"{s.lower()}@kline_1m" for s in symbols_list]
    # Binance позволяет до 1024 стримов в одном соединении, у нас ~40, все ок.
    twm.start_multiplex_socket(callback=handle_socket_message, streams=streams)
    
    print("⚡ WebSocket поток запущен! Ожидание сигналов...")
    twm.join()

# Запускаем бота в фоне
threading.Thread(target=start_bot, daemon=True).start()
threading.Thread(target=position_monitor, daemon=True).start()

@app.route('/')
def health(): return "Speedster v7.9 (NumPy+WS) Running"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
