import os, requests, time
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- НАСТРОЙКИ КОНВЕЙЕРА (PAPER) ---
SYMBOL = 'BNBUSDC'
WALL_SIZE = 900      # Плотность "Миллионер"
RANGE_MAX = 0.015    # Макс. разброс между стенками
AGGREGATION = 0.5    # Группировка стакана

# --- НАША МАТЕМАТИКА 1 к 3 ---
START_SL = 0.035     # 3.5%
FINAL_TP = 0.105     # 10.5%
BE_LEVEL = 0.035     # Перенос в Б/У при 3.5%

# Память для бумажных сделок
active_trades = {}

def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    return Client(api_key, api_secret) if api_key and api_secret else None

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

def find_whale_walls(data):
    for p, q in data:
        p_val = float(p)
        vol = sum([float(raw_q) for raw_p, raw_q in data if abs(float(raw_p) - p_val) <= AGGREGATION])
        if vol >= WALL_SIZE: return p_val, vol
    return None, 0

@app.route('/')
def run_bot():
    global active_trades
    client = get_binance_client()
    if not client: return "API Keys Missing", 500
    
    try:
        # 1. ПРОВЕРКА АКТИВНОЙ БУМАЖНОЙ СДЕЛКИ
        if SYMBOL in active_trades:
            trade = active_trades[SYMBOL]
            curr_p = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            
            # Считаем PNL
            if trade['side'] == 'LONG':
                pnl_pct = (curr_p - trade['entry']) / trade['entry']
            else:
                pnl_pct = (trade['entry'] - curr_p) / trade['entry']

            # Логика БЕЗУБЫТКА
            if pnl_pct >= BE_LEVEL and not trade['is_be']:
                trade['stop'] = trade['entry']
                trade['is_be'] = True
                send_tg(f"🛡 *BNB*: Стоп перенесен в БЕЗУБЫТОК (+3.5% пройдены)")

            # Закрытие по ТЕЙКУ
            if pnl_pct >= FINAL_TP:
                send_tg(f"✅ *ПРОФИТ BNB*: +10.5% 💰")
                del active_trades[SYMBOL]
                return "Take Profit hit"

            # Закрытие по СТОПУ
            if (trade['side'] == 'LONG' and curr_p <= trade['stop']) or \
               (trade['side'] == 'SHORT' and curr_p >= trade['stop']):
                res = "0% (Б/У)" if trade['is_be'] else "-3.5%"
                send_tg(f"❌ *СТОП BNB*: {res}")
                del active_trades[SYMBOL]
                return "Stop Loss hit"

            return f"BNB в сделке. Текущий PNL: {pnl_pct*100:.2f}%"

        # 2. ПОИСК НОВОЙ СДЕЛКИ (СКАНЕР СТАКАНА)
        depth = client.futures_order_book(symbol=SYMBOL, limit=100)
        bid_p, bid_vol = find_whale_walls(depth['bids'])
        ask_p, ask_vol = find_whale_walls(depth['asks'])

        if bid_p and ask_p:
            gap = (ask_p - bid_p) / bid_p
            curr_p = float(depth['bids'][0][0])
            
            if gap <= RANGE_MAX:
                # Вход от нижней стенки
                if curr_p <= bid_p + (ask_p - bid_p) * 0.2:
                    entry_p = bid_p + 0.10
                    stop_p = entry_p * (1 - START_SL)
                    active_trades[SYMBOL] = {
                        'side': 'LONG', 'entry': entry_p, 'stop': stop_p, 'is_be': False
                    }
                    send_tg(f"⚡️ *БУМАЖНЫЙ LONG BNB*\nВход: `{entry_p}`\nСтена снизу: `{bid_vol:.0f} BNB`")
                
                # Вход от верхней стенки
                elif curr_p >= ask_p - (ask_p - bid_p) * 0.2:
                    entry_p = ask_p - 0.10
                    stop_p = entry_p * (1 + START_SL)
                    active_trades[SYMBOL] = {
                        'side': 'SHORT', 'entry': entry_p, 'stop': stop_p, 'is_be': False
                    }
                    send_tg(f"⚡️ *БУМАЖНЫЙ SHORT BNB*\nВход: `{entry_p}`\nСтена сверху: `{ask_vol:.0f} BNB`")

        return "Сканирую стакан BNB на наличие китов..."

    except Exception as e:
        return f"Ошибка: {e}", 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
