import os
import json
import asyncio
import threading
import requests
import ccxt
import pandas as pd
from flask import Flask
import websockets

# --- НАСТРОЙКИ СТРАТЕГИИ ---
SYMBOL = 'SOL/USDC'
TIMEFRAME = '5m'
INITIAL_BALANCE = 100.0
LEVERAGE = 20
MARGIN_PER_TRADE = 10.0
POSITION_SIZE_USD = MARGIN_PER_TRADE * LEVERAGE  # $200

STOP_LOSS_USD = 0.20
TAKE_PROFIT_1_USD = 0.20
TAKE_PROFIT_2_USD = 1.00

MAKER_FEE = 0.0000
TAKER_FEE = 0.0005

EMA_PERIOD = 200
VOLUME_MULTIPLIER = 1.5
VOLUME_SMA_PERIOD = 20

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "paper_state.json"

# --- FLASK WEB SERVER (для поддержания активности на Render) ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "Bot is alive and running with WebSockets!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- TELEGRAM SENDER ---
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram Notice] Token/ChatID not set. Msg: {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, json=payload, timeout=5)
        data = res.json()
        if not data.get("ok"):
            print(f"❌ Telegram Error: {data.get('description')}")
        else:
            print("✅ Сообщение в Telegram доставлено.")
    except Exception as e:
        print(f"Ошибка сети Telegram: {e}")

# --- STATE MANAGEMENT ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance": INITIAL_BALANCE,
        "position": None,
        "last_processed_timestamp": 0,
        "total_trades": 0,
        "win_take2": 0,
        "losses": 0,
        "breakevens": 0,
        "total_fees": 0.0
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")

# --- WEBSOCKET MAIN LOOP ---
async def start_websocket():
    state = load_state()

    # 1. Разовый запрос истории свечей через REST API при старте
    print("📥 Скачиваем историю свечей (1 раз при запуске)...")
    exchange = ccxt.binance({'enableRateLimit': True})
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=250)

    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['ema200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    df['vol_sma'] = df['volume'].rolling(window=VOLUME_SMA_PERIOD).mean()

    print("✅ История загружена. Индикаторы посчитаны.")
    send_telegram(
        f"⚡ <b>Paper Bot (WebSockets) запущен!</b>\n"
        f"Пара: {SYMBOL} | ТФ: {TIMEFRAME}\n"
        f"Баланс: ${state['balance']:.2f}\n"
        f"Режим: 100% WebSockets (0 запросов REST API)"
    )

    ws_symbol = SYMBOL.replace('/', '').lower()
    ws_url = f"wss://stream.binance.com:9443/ws/{ws_symbol}@kline_{TIMEFRAME}"

    # 2. Бесконечный цикл чтения потока WebSockets
    while True:
        try:
            print(f"🔌 Подключение к Binance WebSocket: {ws_url}")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print("✅ WebSocket подключен! Слушаем реал-тайм тики...")

                async for message in ws:
                    data = json.loads(message)
                    kline = data.get('k', {})
                    if not kline:
                        continue

                    # Данные текущей свечи из сокета
                    o = float(kline['o'])
                    h = float(kline['h'])
                    l = float(kline['l'])
                    c = float(kline['c'])
                    v = float(kline['v'])
                    ts = int(kline['t'])
                    is_closed = kline['x']  # True, если 5m свеча завершилась

                    position = state['position']

                    # --- 1. ПРОВЕРКА ПОЗИЦИИ В РЕАЛЬНОМ ВРЕМЕНИ (НА КАЖДОМ ТИКЕ) ---
                    if position is not None:
                        entry = position['entry']
                        sl = position['sl']
                        tp1_hit = position['tp1_hit']
                        pos_type = position['type']

                        if pos_type == 'LONG':
                            # Проверка Тейк-1
                            if not tp1_hit and h >= entry + TAKE_PROFIT_1_USD:
                                position['tp1_hit'] = True
                                position['sl'] = entry
                                state['position'] = position
                                save_state(state)
                                send_telegram(
                                    f"🎯 <b>LONG TP1 Достигнут!</b>\n"
                                    f"Цена: ${h:.2f} (+$0.20). Стоп перенесен в Безубыток (${entry:.2f})"
                                )

                            # Проверка Тейк-2
                            elif h >= entry + TAKE_PROFIT_2_USD:
                                exit_price = entry + TAKE_PROFIT_2_USD
                                gross_pnl = (POSITION_SIZE_USD / entry) * (exit_price - entry)
                                fee = (POSITION_SIZE_USD * MAKER_FEE) * 2
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['total_fees'] += fee
                                state['win_take2'] += 1
                                state['total_trades'] += 1
                                state['position'] = None
                                save_state(state)

                                send_telegram(
                                    f"🎉 <b>LONG ЗАКРЫТ ПО ТЕЙК-2 (Maker)</b>\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_price:.2f}\n"
                                    f"Профит: <b>+${net_pnl:.2f}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                            # Проверка Стоп-лосс / Безубыток
                            elif l <= sl:
                                exit_price = sl
                                gross_pnl = (POSITION_SIZE_USD / entry) * (exit_price - entry)
                                fee = (POSITION_SIZE_USD * MAKER_FEE) + (POSITION_SIZE_USD * TAKER_FEE)
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['total_fees'] += fee
                                state['total_trades'] += 1
                                state['position'] = None

                                if tp1_hit:
                                    state['breakevens'] += 1
                                    save_state(state)
                                    send_telegram(
                                        f"🟡 <b>LONG ЗАКРЫТ В БЕЗУБЫТОК</b>\n"
                                        f"Выход: ${exit_price:.2f}\n"
                                        f"Комиссия: -${fee:.2f}\n"
                                        f"Баланс: <b>${state['balance']:.2f}</b>"
                                    )
                                else:
                                    state['losses'] += 1
                                    save_state(state)
                                    send_telegram(
                                        f"🛑 <b>LONG ЗАКРЫТ ПО СТОП-ЛОССУ</b>\n"
                                        f"Вход: ${entry:.2f} ➔ Выход: ${exit_price:.2f}\n"
                                        f"Убыток: <b>-${abs(net_pnl):.2f}</b>\n"
                                        f"Баланс: <b>${state['balance']:.2f}</b>"
                                    )

                        elif pos_type == 'SHORT':
                            # Проверка Тейк-1
                            if not tp1_hit and l <= entry - TAKE_PROFIT_1_USD:
                                position['tp1_hit'] = True
                                position['sl'] = entry
                                state['position'] = position
                                save_state(state)
                                send_telegram(
                                    f"🎯 <b>SHORT TP1 Достигнут!</b>\n"
                                    f"Цена: ${l:.2f} (-$0.20). Стоп перенесен в Безубыток (${entry:.2f})"
                                )

                            # Проверка Тейк-2
                            elif l <= entry - TAKE_PROFIT_2_USD:
                                exit_price = entry - TAKE_PROFIT_2_USD
                                gross_pnl = (POSITION_SIZE_USD / entry) * (entry - exit_price)
                                fee = (POSITION_SIZE_USD * MAKER_FEE) * 2
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['total_fees'] += fee
                                state['win_take2'] += 1
                                state['total_trades'] += 1
                                state['position'] = None
                                save_state(state)

                                send_telegram(
                                    f"🎉 <b>SHORT ЗАКРЫТ ПО ТЕЙК-2 (Maker)</b>\n"
                                    f"Вход: ${entry:.2f} ➔ Выход: ${exit_price:.2f}\n"
                                    f"Профит: <b>+${net_pnl:.2f}</b>\n"
                                    f"Баланс: <b>${state['balance']:.2f}</b>"
                                )

                            # Проверка Стоп-лосс / Безубыток
                            elif h >= sl:
                                exit_price = sl
                                gross_pnl = (POSITION_SIZE_USD / entry) * (entry - exit_price)
                                fee = (POSITION_SIZE_USD * MAKER_FEE) + (POSITION_SIZE_USD * TAKER_FEE)
                                net_pnl = gross_pnl - fee

                                state['balance'] += net_pnl
                                state['total_fees'] += fee
                                state['total_trades'] += 1
                                state['position'] = None

                                if tp1_hit:
                                    state['breakevens'] += 1
                                    save_state(state)
                                    send_telegram(
                                        f"🟡 <b>SHORT ЗАКРЫТ В БЕЗУБЫТОК</b>\n"
                                        f"Выход: ${exit_price:.2f}\n"
                                        f"Комиссия: -${fee:.2f}\n"
                                        f"Баланс: <b>${state['balance']:.2f}</b>"
                                    )
                                else:
                                    state['losses'] += 1
                                    save_state(state)
                                    send_telegram(
                                        f"🛑 <b>SHORT ЗАКРЫТ ПО СТОП-ЛОССУ</b>\n"
                                        f"Вход: ${entry:.2f} ➔ Выход: ${exit_price:.2f}\n"
                                        f"Убыток: <b>-${abs(net_pnl):.2f}</b>\n"
                                        f"Баланс: <b>${state['balance']:.2f}</b>"
                                    )

                    # --- 2. ПОИСК СИГНАЛОВ (ТОЛЬКО ПРИ ЗАКРЫТИИ СВЕЧИ) ---
                    if is_closed and ts > state['last_processed_timestamp']:
                        state['last_processed_timestamp'] = ts

                        # Добавляем закрытую свечу в DataFrame
                        new_row = pd.DataFrame([{
                            'timestamp': ts,
                            'open': o,
                            'high': h,
                            'low': l,
                            'close': c,
                            'volume': v
                        }])
                        df = pd.concat([df, new_row], ignore_index=True)

                        # Пересчитываем индикаторы
                        df['ema200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
                        df['vol_sma'] = df['volume'].rolling(window=VOLUME_SMA_PERIOD).mean()

                        last_closed = df.iloc[-1]
                        prev_closed = df.iloc[-2]

                        if state['position'] is None:
                            volume_filter = last_closed['volume'] > (last_closed['vol_sma'] * VOLUME_MULTIPLIER)
                            round_level = round(prev_closed['close'])

                            # Пробой вверх (LONG)
                            if prev_closed['close'] < round_level and last_closed['close'] > round_level:
                                if last_closed['close'] > last_closed['ema200'] and volume_filter:
                                    entry = round_level + 0.05
                                    sl_val = entry - STOP_LOSS_USD
                                    state['position'] = {
                                        'type': 'LONG',
                                        'entry': entry,
                                        'sl': sl_val,
                                        'tp1_hit': False
                                    }
                                    save_state(state)
                                    send_telegram(
                                        f"📈 <b>ОТКРЫТА ПОЗИЦИЯ: LONG</b>\n"
                                        f"Вход: <b>${entry:.2f}</b>\n"
                                        f"Стоп: ${sl_val:.2f}\n"
                                        f"Тейк-1: ${entry + TAKE_PROFIT_1_USD:.2f}\n"
                                        f"Тейк-2: ${entry + TAKE_PROFIT_2_USD:.2f}"
                                    )

                            # Пробой вниз (SHORT)
                            elif prev_closed['close'] > round_level and last_closed['close'] < round_level:
                                if last_closed['close'] < last_closed['ema200'] and volume_filter:
                                    entry = round_level - 0.05
                                    sl_val = entry + STOP_LOSS_USD
                                    state['position'] = {
                                        'type': 'SHORT',
                                        'entry': entry,
                                        'sl': sl_val,
                                        'tp1_hit': False
                                    }
                                    save_state(state)
                                    send_telegram(
                                        f"📉 <b>ОТКРЫТА ПОЗИЦИЯ: SHORT</b>\n"
                                        f"Вход: <b>${entry:.2f}</b>\n"
                                        f"Стоп: ${sl_val:.2f}\n"
                                        f"Тейк-1: ${entry - TAKE_PROFIT_1_USD:.2f}\n"
                                        f"Тейк-2: ${entry - TAKE_PROFIT_2_USD:.2f}"
                                    )

        except Exception as e:
            print(f"⚠️ Разрыв соединения WebSocket: {e}. Переподключение через 5 сек...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    # Фоновый Web-сервер Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Асинхронный запуск WebSockets
    asyncio.run(start_websocket())
