import os, requests, time
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ ---
MODE = "PAPER" 
DOLLAR_PER_TRADE = 5.0 # Сумма на одну монету

# Независимая корзина (3 Long / 3 Short)
BASKET_CONFIG = [
    {'symbol': 'BTCUSDC', 'side': 'LONG'},
    {'symbol': 'ETHUSDC', 'side': 'SHORT'},
    {'symbol': 'ZECUSDC', 'side': 'LONG'},
    {'symbol': 'SOLUSDC', 'side': 'SHORT'},
    {'symbol': 'LINKUSDC', 'side': 'LONG'},
    {'symbol': 'XRPUSDC', 'side': 'SHORT'}
]

# Математика 1 к 3
START_SL = 0.035     # Стоп 3.5%
FINAL_TP = 0.105     # Тейк 10.5%
TRAIL_STEP = 0.030   # Шаг трейлинга 3%

# Память бота (не сбрасывается между вызовами Flask в рамках одной сессии)
if 'active_trades' not in globals():
    active_trades = {}
if 'cycle_count' not in globals():
    cycle_count = 0

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

@app.route('/')
def run_conveyor():
    global active_trades, cycle_count
    client = Client(os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
    
    # 1. ПРОВЕРКА: Ждем ли мы завершения старого цикла?
    if active_trades:
        symbols_to_remove = []
        for sym, trade in active_trades.items():
            try:
                curr_p = float(client.futures_symbol_ticker(symbol=sym)['price'])
                is_long = trade['side'] == "LONG"
                pnl = (curr_p - trade['entry'])/trade['entry'] if is_long else (trade['entry'] - curr_p)/trade['entry']
                
                # Условия выхода
                hit_tp = (curr_p >= trade['take']) if is_long else (curr_p <= trade['take'])
                hit_sl = (curr_p <= trade['stop']) if is_long else (curr_p >= trade['stop'])
                
                if hit_tp or hit_sl:
                    status = "✅ ТЕЙК" if hit_tp else "🍎 СТОП"
                    send_tg(f"{status} по {sym} ({trade['side']})\nPNL: `{pnl*100:.2f}%`")
                    symbols_to_remove.append(sym)
                else:
                    # Логика скользящего стопа
                    steps = int(pnl / TRAIL_STEP)
                    if steps >= 1:
                        new_stop_offset = (steps - 1) * TRAIL_STEP
                        if steps == 1: new_stop_offset = 0.002 # Б/У
                        new_stop = round(trade['entry'] * (1 + new_stop_offset) if is_long else trade['entry'] * (1 - new_stop_offset), 4)
                        
                        if (is_long and new_stop > trade['stop']) or (not is_long and new_stop < trade['stop']):
                            trade['stop'] = new_stop
                            send_tg(f"🛡 {sym}: Стоп подтянут в `{new_stop}`")
            except: continue

        for sym in symbols_to_remove:
            del active_trades[sym]

        if not active_trades:
            send_tg("🏁 *ЦИКЛ ЗАВЕРШЕН*. Все сделки закрыты. Жду 5 минут перед новым кругом...")
            return "Цикл завершен. Очистка..."
        
        return f"В работе {len(active_trades)} сделок. Ждем завершения цикла."

    # 2. ЗАПУСК НОВОГО ЦИКЛА
    cycle_count += 1
    send_tg(f"🌀 *ЗАПУСК ЦИКЛА №{cycle_count}*")
    
    for config in BASKET_CONFIG:
        sym = config['symbol']
        try:
            curr_p = float(client.futures_symbol_ticker(symbol=sym)['price'])
            side = config['side']
            stop_p = round(curr_p * (1 - START_SL) if side == "LONG" else curr_p * (1 + START_SL), 4)
            take_p = round(curr_p * (1 + FINAL_TP) if side == "LONG" else curr_p * (1 - FINAL_TP), 4)
            
            active_trades[sym] = {
                'side': side, 'entry': curr_p, 'stop': stop_p, 'take': take_p
            }
        except: continue
    
    send_tg(f"✅ Все 6 позиций открыты (PAPER). Поехали!")
    return f"Цикл №{cycle_count} запущен."

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
