import os, time, requests, sys, threading
import numpy as np
from flask import Flask

# --- БЛОК БЕЗОПАСНОГО ИМПОРТА ---
try:
    from binance.client import Client
    # Пытаемся импортировать менеджер сокетов разными способами
    try:
        from binance.streams import ThreadedWebsocketManager
    except ImportError:
        from binance import ThreadedWebsocketManager
except ImportError as e:
    print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Библиотека не найдена. Детали: {e}")
    sys.exit(1)

app = Flask(__name__)

# --- НАСТРОЙКИ ---
LEVERAGE = 75
MARGIN_USDC = 1.2 
PROFIT_PERCENT = 0.0025  # 0.25% движения цены
EMA_FAST = 7
EMA_SLOW = 99
GAP_THRESHOLD = 0.001 

# Глобальные переменные
active_symbol = None
ema_cache = {}
usdc_pairs_info = {}

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

# --- МАТЕМАТИКА (NumPy) ---
def calculate_initial_ema(values, span):
    values = np.array(values)
    alpha = 2 / (span + 1)
    ema = values[0]
    for value in values[1:]:
        ema = (value * alpha) + (ema * (1 - alpha))
    return ema

def update_ema(prev_ema, close_price, span):
    alpha = 2 / (span + 1)
    return (close_price * alpha) + (prev_ema * (1 - alpha))

# --- ЛОГИКА ---
def get_usdc_pairs():
    """Ищет пары USDC и проверяет лимиты"""
    try:
        info = client.futures_exchange_info()
        pairs = {}
        trading_pairs = []
        
        for s in info['symbols']:
            if s['symbol'].endswith('USDC') and s['status'] == 'TRADING':
                min_notional = 5.0
                for f in s['filters']:
                    if f['filterType'] == 'NOTIONAL': min_notional = float(f['notional'])
                
                # Фильтруем те, куда можем зайти с нашим балансом
                if (MARGIN_USDC * LEVERAGE) >= min_notional:
                    pairs[s['symbol']] = {
                        'q_prec': int(s['quantityPrecision']),
                        'p_prec': int(s['pricePrecision']),
                        'min_notional': min_notional
                    }
                    trading_pairs.append(s['symbol'])
        return pairs, trading_pairs
    except Exception as e:
        print(f"Ошибка получения пар: {e}")
        return {}, []

def initialize_market_data(symbols):
    """Скачивает историю для старта"""
    print(f"📊 Загрузка истории для {len(symbols)} пар...")
    count = 0
    for symbol in symbols:
        try:
            klines = client.futures_klines(symbol=symbol, interval='1m', limit=150)
            closes = [float(k[4]) for k in klines]
            
            if len(closes) < 100: continue

            ema_f = calculate_initial_ema(closes, EMA_FAST)
            ema_s = calculate_initial_ema(closes, EMA_SLOW)
            
            ema_cache[symbol] = {'fast': ema_f, 'slow': ema_s, 'prev_fast': ema_f, 'prev_slow': ema_s}
            count += 1
            # Небольшая пауза, чтобы не забить API при старте
            if count % 5 == 0: time.sleep(0.5)
        except: pass
    return count

def handle_socket_message(msg):
    """Обработка сигнала от биржи"""
    global active_symbol
    
    if msg['e'] != 'kline': return
    kline = msg['k']
    if not kline['x']: return # Ждем закрытия свечи
    
    symbol = msg['s']
    close_price = float(kline['c'])
    
    # Если мы в сделке - игнорируем другие сигналы
    if active_symbol: return

    if symbol in ema_cache:
        data = ema_cache[symbol]
        
        # Запоминаем старые значения
        data['prev_fast'] = data['fast']
        data['prev_slow'] = data['slow']
        
        # Считаем новые
        data['fast'] = update_ema(data['fast'], close_price, EMA_FAST)
        data['slow'] = update_ema(data['slow'], close_price, EMA_SLOW)
        
        # Проверяем пересечение
        f_now, f_prev = data['fast'], data['prev_fast']
        s_now, s_prev = data['slow'], data['prev_slow']
        gap = abs(f_now - s_now) / s_now
        
        side = None
        if f_prev <= s_prev and f_now > s_now and gap >= GAP_THRESHOLD: side = "LONG"
        elif f_prev >= s_prev and f_now < s_now and gap >= GAP_THRESHOLD: side = "SHORT"
        
        if side: execute_trade(symbol, side, close_price)

def execute_trade(symbol, side, price):
    global active_symbol
    
    # Еще одна проверка позиции перед входом
    try:
        pos = client.futures_position_information(symbol=symbol)
        amt = float(pos[0]['positionAmt'])
        if amt != 0: return 
    except: return

    try:
        print(f"⚡ СИГНАЛ {symbol} {side}")
        active_symbol = symbol 
        
        info = usdc_pairs_info[symbol]
        
        # Макс плечо
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
        
        send_tg(f"🚀 *ВХОД {symbol}* (Speedster)\nПлечо: {max_lev}x\nЦена: `{entry_price}`\nТейк: `{tp_price}`")

    except Exception as e:
        print(f"Ошибка входа: {e}")
        send_tg(f"❌ Ошибка входа {symbol}: {e}")
        active_symbol = None # Снимаем блокировку, если ошибка

def position_monitor():
    """Следит, закрылась ли сделка"""
    global active_symbol
    while True:
        if active_symbol:
            try:
                pos = client.futures_position_information(symbol=active_symbol)
                amt = float(pos[0]['positionAmt'])
                if amt == 0:
                    send_tg(f"💰 *Сделка {active_symbol} закрыта!* Ищу новую...")
                    active_symbol = None
            except: pass
        time.sleep(5)

def start_bot():
    global usdc_pairs_info
    
    # 1. Поиск пар
    usdc_pairs_info, symbols_list = get_usdc_pairs()
    if not symbols_list:
        print("❌ Не найдено пар USDC! Проверь API.")
        return

    msg_pairs = ", ".join([s.replace('USDC','') for s in symbols_list])
    send_tg(f"🤖 *Завод v7.9 SPEEDSTER Запущен*\nВерсия Python: {sys.version.split()[0]}\nПары: {len(symbols_list)} шт.\n\n📝 Список: {msg_pairs}")
    
    # 2. Загрузка истории
    count = initialize_market_data(symbols_list)
    
    # 3. Вебсокеты
    twm = ThreadedWebsocketManager(api_key=os.environ.get("BINANCE_API_KEY"), api_secret=os.environ.get("BINANCE_API_SECRET"))
    twm.start()
    
    streams = [f"{s.lower()}@kline_1m" for s in symbols_list]
    twm.start_multiplex_socket(callback=handle_socket_message, streams=streams)
    
    print("⚡ WebSocket слушает рынок...")
    twm.join()

# Запуск потоков
threading.Thread(target=start_bot, daemon=True).start()
threading.Thread(target=position_monitor, daemon=True).start()

@app.route('/')
def health(): return "Speedster v7.9 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
