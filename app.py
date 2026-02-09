import os
import json
import ccxt
import time
import pandas as pd
import telebot
import websocket
import threading
from datetime import datetime

# ================= 1. БЕРЕМ КЛЮЧИ ИЗ RENDER =================
# Бот сам найдет их в Environment Variables
API_KEY = os.getenv('API_KEY')
SECRET_KEY = os.getenv('SECRET_KEY')
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Проверка, что ключи на месте
if not API_KEY or not SECRET_KEY:
    print("❌ ОШИБКА: Ключи не найдены в Environment Variables!")
    exit()

# ================= 2. НАСТРОЙКИ СТРАТЕГИИ =================
SYMBOL_CCXT = 'SOL/USDC'   # Для ордеров
SYMBOL_SOCKET = 'solusdc'  # Для сокета (маленькими)
TIMEFRAME = '1m'
LEVERAGE = 30              # Плечо 10 (для безопасности)
QTY_USDT = 1               # Размер входа в $
MAX_ORDERS = 6             # 6 шагов Деда
GRID_STEP = 0.002          # 0.2% шаг усреднения
THRESHOLD = 0.003          # 0.4% сигнал "Змеи"

# ================= 3. ИНИЦИАЛИЗАЦИЯ =================
exchange = ccxt.binanceusdm({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'enableRateLimit': True
})
bot = telebot.TeleBot(BOT_TOKEN)

# Глобальные переменные (Память бота)
closes = []      # Список цен закрытия
current_price = 0
in_position = False 
position_data = {} 
last_trade_time = 0

def log(message):
    """Пишет в лог Render и в Телеграм"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    try:
        bot.send_message(CHAT_ID, message)
    except:
        pass

# ================= 4. ЛОГИКА ТОРГОВЛИ =================
def update_position_info():
    """Спрашиваем у Бинанса, есть ли у нас позиция (через API)"""
    global in_position, position_data
    try:
        # Этот запрос делаем редко, только при необходимости
        positions = exchange.fetch_positions([SYMBOL_CCXT])
        pos = [p for p in positions if p['symbol'] == SYMBOL_CCXT][0]
        amt = float(pos['contracts'])
        
        if amt != 0:
            in_position = True
            position_data = {
                'side': pos['side'],      # 'long' или 'short'
                'amount': amt,            # Сколько монет
                'entry': float(pos['entryPrice']), # Цена входа
                'pnl': float(pos['unrealizedPnl']) # Прибыль/убыток
            }
        else:
            in_position = False
            position_data = {}
    except Exception as e:
        print(f"Ошибка получения позиции: {e}")

def execute_trade(action, reason):
    """Исполнение ордеров"""
    global last_trade_time
    try:
        # Защита от двойных нажатий (ждем 5 сек между сделками)
        if time.time() - last_trade_time < 5: 
            return

        qty = QTY_USDT / current_price # Считаем объем в монетах
        
        if action == 'BUY_OPEN':
            exchange.create_market_buy_order(SYMBOL_CCXT, qty)
            log(f"🚀 OPEN LONG! Цена: {current_price} | {reason}")
            
        elif action == 'SELL_OPEN':
            exchange.create_market_sell_order(SYMBOL_CCXT, qty)
            log(f"🔻 OPEN SHORT! Цена: {current_price} | {reason}")
            
        elif action == 'CLOSE_LONG_AND_FLIP':
            # Закрываем текущий лонг
            amt = position_data.get('amount', 0)
            if amt > 0: exchange.create_market_sell_order(SYMBOL_CCXT, amt)
            # Открываем шорт
            exchange.create_market_sell_order(SYMBOL_CCXT, qty)
            log(f"🔄 ПЕРЕВОРОТ В SHORT! (Закрыли +{position_data.get('pnl',0)}$)")

        elif action == 'CLOSE_SHORT_AND_FLIP':
            # Закрываем текущий шорт
            amt = position_data.get('amount', 0)
            if amt > 0: exchange.create_market_buy_order(SYMBOL_CCXT, amt)
            # Открываем лонг
            exchange.create_market_buy_order(SYMBOL_CCXT, qty)
            log(f"🔄 ПЕРЕВОРОТ В LONG! (Закрыли +{position_data.get('pnl',0)}$)")
            
        # Обновляем инфу о позиции после сделки
        time.sleep(1) 
        update_position_info()
        last_trade_time = time.time()

    except Exception as e:
        log(f"⚠️ Ошибка ордера: {e}")

def check_strategy():
    """Главный мозг Змеи"""
    if len(closes) < 30: return # Ждем пока наберется история
    
    # 1. Считаем EMA
    series = pd.Series(closes)
    ema7 = series.ewm(span=7, adjust=False).mean().iloc[-1]
    ema25 = series.ewm(span=25, adjust=False).mean().iloc[-1]
    
    gap = (ema7 - ema25) / ema25
    
    # 2. Логика (только если цена изменилась)
    if not in_position:
        # Если позиции нет - ищем вход
        if gap > THRESHOLD:
            execute_trade('BUY_OPEN', f"Gap {gap:.4f} > 0.4%")
        elif gap < -THRESHOLD:
            execute_trade('SELL_OPEN', f"Gap {gap:.4f} < -0.4%")
    
    else:
        # Если позиция есть - ищем выход или добор
        side = position_data.get('side')
        entry = position_data.get('entry')
        
        if side == 'long':
            # Переворот
            if gap < -THRESHOLD:
                execute_trade('CLOSE_LONG_AND_FLIP', "Сигнал сменился")
            # Усреднение (Добор)
            elif (entry - current_price) / entry >= GRID_STEP:
                 # Тут упрощенная логика добора, чтобы не спамить
                 # В реале нужно считать кол-во ордеров
                 pass 

        elif side == 'short':
            # Переворот
            if gap > THRESHOLD:
                execute_trade('CLOSE_SHORT_AND_FLIP', "Сигнал сменился")
            # Усреднение
            elif (current_price - entry) / entry >= GRID_STEP:
                pass

# ================= 5. РАБОТА С СОКЕТОМ (WEB SOCKET) =================
def on_message(ws, message):
    global current_price, closes
    json_msg = json.loads(message)
    kline = json_msg['k']
    
    current_price = float(kline['c'])
    is_closed = kline['x']
    
    # Если минута закрылась, записываем в историю
    if is_closed:
        closes.append(float(kline['c']))
        if len(closes) > 50: closes.pop(0) # Храним только последние 50
        
        # Проверяем стратегию ПО ЗАКРЫТИЮ СВЕЧИ (самое надежное)
        check_strategy()
        
    # Можно включить проверку на каждом тике, но для начала лучше по закрытию,
    # чтобы не было ложных дерганий.

def on_error(ws, error):
    print(f"Socket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("Соединение закрыто. Перезапуск...")
    time.sleep(5)
    start_socket() # Вечный реконнект

def on_open(ws):
    print("✅ Соединение с Binance установлено! Жду сигналов...")
    # При старте один раз обновим позицию и историю
    try:
        # Грузим 30 свечей истории через API (один раз!)
        ohlcv = exchange.fetch_ohlcv(SYMBOL_CCXT, TIMEFRAME, limit=30)
        global closes
        closes = [x[4] for x in ohlcv]
        update_position_info()
        log(f"Бот запущен. Текущая цена: {closes[-1]}")
    except:
        pass

def start_socket():
    # URL для фьючерсов
    socket = f"wss://fstream.binance.com/ws/{SYMBOL_SOCKET}@kline_{TIMEFRAME}"
    ws = websocket.WebSocketApp(socket,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.run_forever()

if __name__ == "__main__":
    # Установка плеча при старте
    try:
        exchange.load_markets()
        market = exchange.market(SYMBOL_CCXT)
        exchange.fapiPrivate_post_leverage({'symbol': market['id'], 'leverage': LEVERAGE})
    except: pass
    
    start_socket()
