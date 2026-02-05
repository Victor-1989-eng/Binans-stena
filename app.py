import os, time, requests, sys, threading
import numpy as np
from flask import Flask

try:
    from binance.client import Client
    try:
        from binance.streams import ThreadedWebsocketManager
    except ImportError:
        from binance import ThreadedWebsocketManager
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    sys.exit(1)

app = Flask(__name__)

# --- НАСТРОЙКИ ---
LEVERAGE = 75
MARGIN_USDC = 1.2 
PROFIT_PERCENT = 0.0025 
EMA_FAST = 7
EMA_SLOW = 99
GAP_THRESHOLD = 0.0005 # Снизил до 0.05%

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

# --- МАТЕМАТИКА ---
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
    try:
        info = client.futures_exchange_info()
        pairs = {}
        trading_symbols = []
        for s in info['symbols']:
            if s['symbol'].endswith('USDC') and s['status'] == 'TRADING':
                min_notional = 5.0
                for f in s['filters']:
                    if f['filterType'] == 'NOTIONAL': min_notional = float(f['notional'])
                
                if (MARGIN_USDC * LEVERAGE) >= min_notional:
                    pairs[s['symbol']] = {
                        'q_prec': int(s['quantityPrecision']),
                        'p_prec': int(s['pricePrecision']),
                        'min_notional': min_notional
                    }
                    trading_symbols.append(s['symbol'])
        return pairs, trading_symbols
    except Exception as e:
        print(f"Ошибка получения пар: {e}")
        return {}, []

def initialize_market_data(symbols):
    print(f"📊 Загрузка глубокой истории (500 свечей) для {len(symbols)} пар...")
    count = 0
    for symbol in symbols:
        try:
            # Берем 500 свечей для идеальной точности EMA 99
            klines = client.futures_klines(symbol=symbol, interval='1m', limit=500)
            closes = [float(k[4]) for k in klines]
            
            if len(closes) < 150: continue

            ema_f = calculate_initial_ema(closes, EMA_FAST)
            ema_s = calculate_initial_ema(closes, EMA_SLOW)
            
            ema_cache[symbol] = {'fast': ema_f, 'slow': ema_s, 'prev_fast': ema_f, 'prev_slow': ema_s}
            count += 1
            if count % 10 == 0: time.sleep(0.5)
        except: continue
    return count

def handle_socket_message(msg):
    global active_symbol
    
    if not isinstance(msg, dict) or 'e' not in msg or 'k' not in msg: return
    if msg['e'] != 'kline': return
    kline = msg['k']
    if not kline.get('x'): return 
    
    symbol = msg['s']
    close_price = float(kline['c'])
    
    if symbol in ema_cache:
        data = ema_cache[symbol]
        data['prev_fast'], data['prev_slow'] = data['fast'], data['slow']
        data['fast'] = update_ema(data['fast'], close_price, EMA_FAST)
        data['slow'] = update_ema(data['slow'], close_price, EMA_SLOW)
        
        f_now, f_prev = data['fast'], data['prev_fast']
        s_now, s_prev = data['slow'], data['prev_slow']
        
        # Проверка пересечения
        side = None
        if f_prev <= s_prev and f_now > s_now: side = "LONG"
        elif f_prev >= s_prev and f_now < s_now: side = "SHORT"
        
        if side:
            gap = abs(f_now - s_now) / s_now
            print(f"🎯 КРЕСТ на {symbol}: {side} | Зазор: {gap:.6f} | Порог: {GAP_THRESHOLD}")
            
            if active_symbol:
                print(f"🚫 Пропуск {symbol}: Уже открыта позиция {active_symbol}")
                return

            if gap >= GAP_THRESHOLD:
                execute_trade(symbol, side, close_price)
            else:
                print(f"⏳ Пропуск {symbol}: Слишком малый зазор.")

def execute_trade(symbol, side, price):
    global active_symbol
    try:
        active_symbol = symbol 
        info = usdc_pairs_info[symbol]
        
        # Настройка плеча
        try:
            brackets = client.futures_leverage_bracket(symbol=symbol)
            max_lev = int(brackets[0]['brackets'][0]['initialLeverage'])
            client.futures_change_leverage(symbol=symbol, leverage=max_lev)
        except: max_lev = LEVERAGE

        qty = round((MARGIN_USDC * max_lev) / price, info['q_prec'])
        
        # Проверка минимального объема
        if (qty * price) < info['min_notional']:
            print(f"❌ Объем {qty*price} меньше минимального {info['min_notional']} для {symbol}")
            active_symbol = None
            return

        order = client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        entry_price = float(order.get('avgPrice', price))

        dist = entry_price * PROFIT_PERCENT
        tp_price = round(entry_price + dist if side == "LONG" else entry_price - dist, info['p_prec'])
        
        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
                                    type='LIMIT', timeInForce='GTC', quantity=qty, price=tp_price, reduceOnly=True)
        
        send_tg(f"🚀 *ВХОД {symbol}* ({side})\nПлечо: {max_lev}x\nЦена: `{entry_price}`\nТейк: `{tp_price}`")
    except Exception as e:
        print(f"Ошибка входа: {e}")
        active_symbol = None

def position_monitor():
    global active_symbol
    while True:
        if active_symbol:
            try:
                pos = client.futures_position_information(symbol=active_symbol)
                if float(pos[0]['positionAmt']) == 0:
                    send_tg(f"💰 *{active_symbol} закрыта!* Ищу новый вход...")
                    active_symbol = None
            except: pass
        time.sleep(10)

def start_bot():
    global usdc_pairs_info
    usdc_pairs_info, symbols_list = get_usdc_pairs()
    if not symbols_list: return
    
    send_tg(f"🤖 *SPEEDSTER v7.9.7 DEBUG*\nЗапуск сканера...")
    count = initialize_market_data(symbols_list)
    print(f"✅ Готово к работе с {count} парами.")

    twm = ThreadedWebsocketManager(api_key=os.environ.get("BINANCE_API_KEY"), api_secret=os.environ.get("BINANCE_API_SECRET"))
    twm.start()
    streams = [f"{s.lower()}@kline_1m" for s in symbols_list]
    twm.start_multiplex_socket(callback=handle_socket_message, streams=streams)
    twm.join()

def run_all():
    time.sleep(2)
    start_bot()

if __name__ == "__main__":
    threading.Thread(target=position_monitor, daemon=True).start()
    threading.Thread(target=run_all, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

@app.route('/')
def health(): return "OK"
