import ccxt
import time
import requests

# --- БЛОК НАСТРОЕК (ЗАПОЛНИ СВОИ ДАННЫЕ) ---
API_KEY = 'ТВОЙ_BINANCE_API_KEY'
API_SECRET = 'ТВОЙ_BINANCE_SECRET'
TELEGRAM_TOKEN = 'ТОКЕН_ТВОЕГО_БОТА'
TELEGRAM_CHAT_ID = 'ТВОЙ_CHAT_ID'

SYMBOL = "BNB/USDT"
QTY_BNB = 0.50            
WALL_SIZE = 800          # Размер стены кита
REJECTION_OFFSET = 0.0015 # Отскок 0.15% для входа после прокола
STOP_LOSS_PCT = 0.008     # Стоп 0.8%
TP_LIMIT_PCT = 0.007      # Лимитка на прибыль 0.7%

# Подключение к бирже
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def send_tg(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': message})
    except: pass

def get_market_data():
    orderbook = exchange.fetch_order_book(SYMBOL)
    ticker = exchange.fetch_ticker(SYMBOL)
    return orderbook, ticker['last']

def close_all_orders():
    # Отменяет все висящие ордера по символу
    exchange.cancel_all_orders(SYMBOL)
    print("🧹 Все старые ордера удалены.")

def open_hunter_trade(side, price):
    # 1. Вход по рынку
    order = exchange.create_market_order(SYMBOL, side, QTY_BNB)
    entry_price = float(order['price']) if order['price'] else price
    
    send_tg(f"🚀 ВХОД {side.upper()} по {entry_price}\n🎯 Цель: {TP_LIMIT_PCT*100}%")
    
    # 2. Выставляем Лимитку и Стоп
    tp_side = "sell" if side == "buy" else "buy"
    tp_price = entry_price * (1 + TP_LIMIT_PCT) if side == "buy" else entry_price * (1 - TP_LIMIT_PCT)
    sl_price = entry_price * (1 - STOP_LOSS_PCT) if side == "buy" else entry_price * (1 + STOP_LOSS_PCT)
    
    # Лимитка (Maker)
    exchange.create_order(SYMBOL, "LIMIT", tp_side, QTY_BNB, tp_price, {'reduceOnly': True})
    # Стоп (Market)
    exchange.create_order(SYMBOL, "STOP_MARKET", tp_side, QTY_BNB, None, {'stopPrice': sl_price, 'reduceOnly': True})
    
    return entry_price

def main():
    send_tg("🤖 Бот-Охотник запущен. Ищу выносы стопов...")
    while True:
        try:
            # Проверяем, нет ли уже открытой позиции
            pos = exchange.fetch_positions([SYMBOL])
            if float(pos[0]['contracts']) != 0:
                time.sleep(10) # Если позиция есть, просто ждем
                continue

            orderbook, current_price = get_market_data()
            
            # Ищем стену для SHORT (сверху)
            for wall in orderbook['asks']:
                if wall[1] >= WALL_SIZE and current_price >= wall[0]:
                    send_tg(f"⚠️ Стена {wall[0]} пробита! Жду разворота для SHORTa...")
                    # Цикл ожидания отскока
                    while True:
                        p = exchange.fetch_ticker(SYMBOL)['last']
                        if p <= wall[0] * (1 - REJECTION_OFFSET):
                            close_all_orders()
                            open_hunter_trade("sell", p)
                            break
                        time.sleep(1)

            # Ищем стену для LONG (снизу)
            for wall in orderbook['bids']:
                if wall[1] >= WALL_SIZE and current_price <= wall[0]:
                    send_tg(f"⚠️ Стена {wall[0]} пробита вниз! Жду возврата для LONGa...")
                    while True:
                        p = exchange.fetch_ticker(SYMBOL)['last']
                        if p >= wall[0] * (1 + REJECTION_OFFSET):
                            close_all_orders()
                            open_hunter_trade("buy", p)
                            break
                        time.sleep(1)

        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
