import os, time, threading, requests
import pandas as pd
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ ---
MARGIN_USDC = 1.2 # Чуть поднял, чтобы проходить фильтры на большем числе пар
PROFIT_PERCENT = 0.0025 
EMA_FAST = 7
EMA_SLOW = 99
GAP_THRESHOLD = 0.001 

client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def get_all_usdc_pairs():
    """Сканирует биржу и находит все USDC пары, их плечи и лимиты"""
    try:
        info = client.futures_exchange_info()
        usdc_pairs = []
        for s in info['symbols']:
            # Берем только USDC, которые торгуются (TRADING)
            if s['symbol'].endswith('USDC') and s['status'] == 'TRADING':
                min_notional = 5.0
                for f in s['filters']:
                    if f['filterType'] == 'NOTIONAL': min_notional = float(f['notional'])
                
                usdc_pairs.append({
                    'symbol': s['symbol'],
                    'q_prec': int(s['quantityPrecision']),
                    'p_prec': int(s['pricePrecision']),
                    'min_notional': min_notional
                })
        return usdc_pairs
    except Exception as e:
        print(f"Ошибка при получении списка пар: {e}")
        return []

def run_scanner():
    print("🔍 Инициализация Auto-Hunter v7.8...")
    all_pairs = get_all_usdc_pairs()
    send_tg(f"✅ Найдено {len(all_pairs)} пар USDC. Начинаю охоту по кругу!")

    while True:
        try:
            # 1. Проверяем, нет ли уже открытой позиции
            pos_info = client.futures_position_information()
            active = [p for p in pos_info if float(p['positionAmt']) != 0]

            if active:
                # Если позиция есть, ждем и проверяем на разворот (Аварию)
                p = active[0]
                symbol, amt = p['symbol'], float(p['positionAmt'])
                
                klines = client.futures_klines(symbol=symbol, interval='1m', limit=150)
                closes = pd.Series([float(k[4]) for k in klines])
                f_now = closes.ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
                s_now = closes.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-1]

                if (amt > 0 and f_now < s_now) or (amt < 0 and f_now > s_now):
                    client.futures_cancel_all_open_orders(symbol=symbol)
                    client.futures_create_order(symbol=symbol, side='SELL' if amt > 0 else 'BUY', 
                                              type='MARKET', quantity=abs(amt), reduceOnly=True)
                    send_tg(f"⚠️ *{symbol}* Закрыто по Аварии (разворот тренда)")
                
                time.sleep(10)
                continue

            # 2. Если позиций нет, идем по кругу всех пар
            for pair in all_pairs:
                symbol = pair['symbol']
                
                # Получаем данные
                klines = client.futures_klines(symbol=symbol, interval='1m', limit=150)
                closes = pd.Series([float(k[4]) for k in klines])
                f = closes.ewm(span=EMA_FAST, adjust=False).mean()
                s = closes.ewm(span=EMA_SLOW, adjust=False).mean()
                
                f_now, f_prev = f.iloc[-1], f.iloc[-2]
                s_now, s_prev = s.iloc[-1], s.iloc[-2]
                gap = abs(f_now - s_now) / s_now

                side = None
                if f_prev <= s_prev and f_now > s_now and gap >= GAP_THRESHOLD: side = "LONG"
                elif f_prev >= s_prev and f_now < s_now and gap >= GAP_THRESHOLD: side = "SHORT"

                if side:
                    # Узнаем макс. плечо для этой конкретной пары
                    brackets = client.futures_leverage_bracket(symbol=symbol)
                    max_leverage = int(brackets[0]['brackets'][0]['initialLeverage'])
                    
                    # Пытаемся выставить плечо (если оно отличается)
                    try: client.futures_change_leverage(symbol=symbol, leverage=max_leverage)
                    except: pass

                    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
                    total_vol = MARGIN_USDC * max_leverage

                    if total_vol < pair['min_notional']:
                        continue # Пропускаем, если объема не хватает

                    # ВХОД
                    qty = round(total_vol / price, pair['q_prec'])
                    order = client.futures_create_order(symbol=symbol, side='BUY' if side=="LONG" else 'SELL', 
                                                      type='MARKET', quantity=qty)
                    entry_price = float(order['avgPrice']) if 'avgPrice' in order else price

                    # ТЕЙК
                    dist = entry_price * PROFIT_PERCENT
                    tp_price = round(entry_price + dist if side == "LONG" else entry_price - dist, pair['p_prec'])
                    
                    client.futures_create_order(symbol=symbol, side='SELL' if side=="LONG" else 'BUY',
                                              type='LIMIT', timeInForce='GTC', quantity=qty, price=tp_price, reduceOnly=True)
                    
                    send_tg(f"🎯 *ВХОД {symbol}* (Плечо {max_leverage}x)\nВход: `{entry_price}`\nТейк: `{tp_price}`")
                    break # Зашли в сделку — выходим из цикла поиска пар

                time.sleep(1.2) # Чтобы не словить бан IP

        except Exception as e:
            print(f"Ошибка в цикле: {e}")
            time.sleep(30) # При ошибке (например, интернет) отдыхаем

threading.Thread(target=run_scanner, daemon=True).start()

@app.route('/')
def health(): return "Auto-Hunter 7.8 Active"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
