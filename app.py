import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ ---
SYMBOLS = ['SOLUSDC', 'BTCUSDC', 'ETHUSDC', 'BNBUSDC']
LEVERAGE = 75
MARGIN_USDC = 1.0 
TF_ENTRY = '15m' 
TF_EXIT = '1m'   
EMA_FAST = 7    
EMA_MED = 25    
EMA_SLOW = 99   
MIN_GAP = 0.0004 

# Глобальные переменные управления
is_running = True  # Состояние бота (Вкл/Выкл)
fix_counts = {s: 0 for s in SYMBOLS}
ready_to_fix = {s: True for s in SYMBOLS}

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

# --- ФУНКЦИИ TELEGRAM ---
def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def handle_commands():
    """Фоновый поток для чтения команд из Telegram"""
    global is_running
    token = os.environ.get("TELEGRAM_TOKEN")
    last_update_id = 0
    
    print("🤖 Командный центр Telegram запущен")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=30"
            response = requests.get(url).json()
            
            if "result" in response:
                for update in response["result"]:
                    last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        cmd = update["message"]["text"].lower()
                        
                        if cmd == "/stop":
                            is_running = False
                            # Принудительно закрываем всё при стопе
                            emergency_close_all()
                            send_tg("⛔️ *БОТ ОСТАНОВЛЕН*\nВсе позиции закрыты, сканер выключен.")
                        
                        elif cmd == "/start":
                            is_running = True
                            send_tg("✅ *БОТ ЗАПУЩЕН*\nНачинаю поиск сигналов на 15м...")
                        
                        elif cmd == "/status":
                            status = "РАБОТАЕТ" if is_running else "СПИТ"
                            send_tg(f"ℹ️ *СТАТУС*: `{status}`\nПлечо: `{LEVERAGE}x`\nДепозит: `${MARGIN_USDC}`")
        except:
            time.sleep(5)

def emergency_close_all():
    """Закрыть все позиции по всем символам сразу"""
    for symbol in SYMBOLS:
        try:
            pos = client.futures_position_information(symbol=symbol)
            active = [p for p in pos if float(p['positionAmt']) != 0]
            if active:
                amt = float(active[0]['positionAmt'])
                side = 'SELL' if amt > 0 else 'BUY'
                client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=abs(amt), reduceOnly=True)
        except Exception as e: print(f"Error during emergency close {symbol}: {e}")

# --- ОСНОВНОЙ ЦИКЛ СКАНЕРА ---
def get_ema_data(symbol, tf):
    klines = client.futures_klines(symbol=symbol, interval=tf, limit=150)
    closes = pd.Series([float(k[4]) for k in klines])
    f = closes.ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
    m = closes.ewm(span=EMA_MED, adjust=False).mean().iloc[-1]
    s = closes.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-1]
    f_prev = closes.ewm(span=EMA_FAST, adjust=False).mean().iloc[-2]
    s_prev = closes.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-2]
    return f, m, s, f_prev, s_prev

def run_scanner():
    while True:
        if not is_running:
            time.sleep(5)
            continue
            
        for symbol in SYMBOLS:
            try:
                pos = client.futures_position_information(symbol=symbol)
                active = [p for p in pos if float(p['positionAmt']) != 0]

                if active:
                    f1, m1, s1, _, _ = get_ema_data(symbol, TF_EXIT)
                    p = active[0]
                    amt = float(p['positionAmt'])
                    side = "LONG" if amt > 0 else "SHORT"
                    
                    # 🚨 АВАРИЯ
                    if (side == "LONG" and f1 <= s1) or (side == "SHORT" and f1 >= s1):
                        client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                  type='MARKET', quantity=abs(amt), reduceOnly=True)
                        fix_counts[symbol] = 0
                        send_tg(f"🚨 *{symbol}* АВАРИЯ! Позиция закрыта.")
                        continue

                    if (side == "LONG" and f1 > m1) or (side == "SHORT" and f1 < m1):
                        ready_to_fix[symbol] = True

                    if ((side == "LONG" and f1 < m1) or (side == "SHORT" and f1 > m1)) and ready_to_fix[symbol]:
                        if fix_counts[symbol] < 5:
                            fix_counts[symbol] += 1
                            ready_to_fix[symbol] = False
                            qty_to_close = abs(amt) * 0.2 if fix_counts[symbol] < 5 else abs(amt)
                            
                            if "BTC" in symbol: qty_to_close = round(qty_to_close, 3)
                            elif "ETH" in symbol: qty_to_close = round(qty_to_close, 2)
                            else: qty_to_close = round(qty_to_close, 1)

                            client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY', 
                                                      type='MARKET', quantity=qty_to_close, reduceOnly=True)
                            send_tg(f"💵 *{symbol}* Фикс №{fix_counts[symbol]} (20%)")
                else:
                    fix_counts[symbol] = 0
                    ready_to_fix[symbol] = True
                    f15, _, s15, f15_prev, s15_prev = get_ema_data(symbol, TF_ENTRY)
                    gap = abs(f15 - s15) / s15
                    if f15_prev <= s15_prev and f15 > s15 and gap >= MIN_GAP:
                        execute_trade(symbol, "LONG")
                    elif f15_prev >= s15_prev and f15 < s15 and gap >= MIN_GAP:
                        execute_trade(symbol, "SHORT")
            except Exception as e: print(f"Ошибка {symbol}: {e}")
            time.sleep(1)

def execute_trade(symbol, side):
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    qty = (MARGIN_USDC * LEVERAGE) / price
    if "BTC" in symbol: qty = round(qty, 3)
    elif "ETH" in symbol: qty = round(qty, 2)
    else: qty = round(qty, 1)
    try:
        client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', type='MARKET', quantity=qty)
        send_tg(f"🚀 *{symbol}* ВХОД {side}")
    except Exception as e: print(f"Error: {e}")

# Запуск потоков
threading.Thread(target=run_scanner, daemon=True).start()
threading.Thread(target=handle_commands, daemon=True).start()

@app.route('/')
def health(): return "Genius v6.2 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
