import os
import time
import json
import threading
from datetime import datetime, timezone
import requests
import ccxt
import pandas as pd
from flask import Flask

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

MAKER_FEE = 0.0000  # 0.00%
TAKER_FEE = 0.0005  # 0.05%

EMA_PERIOD = 200
VOLUME_MULTIPLIER = 1.5
VOLUME_SMA_PERIOD = 20

# --- TELEGRAM & STATE SETTINGS ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "paper_state.json"

# --- FLASK WEB SERVER (для поддержания активности на Render) ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "Bot is alive and running!", 200

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
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Ошибка отправки Telegram: {e}")

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

# --- MAIN BOT TRADING LOOP ---
def bot_loop():
    print("🚀 Бумажный торговый бот запущен!")
    exchange = ccxt.binance()
    state = load_state()

    send_telegram(
        f"🤖 <b>Paper Trading Бот запущен!</b>\n"
        f"Пара: {SYMBOL} | ТФ: {TIMEFRAME}\n"
        f"Баланс: ${state['balance']:.2f}\n"
        f"Размер позиции: ${POSITION_SIZE_USD:.2f}"
    )

    while True:
        try:
            # Получаем свечи (с запасом под EMA 200)
            ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=250)
            if not ohlcv or len(ohlcv) < EMA_PERIOD + 2:
                time.sleep(10)
                continue

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['ema200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
            df['vol_sma'] = df['volume'].rolling(window=VOLUME_SMA_PERIOD).mean()

            current_candle = df.iloc[-1]      # Текущая неформованная свеча
            last_closed = df.iloc[-2]          # Последняя ЗАКРЫТАЯ свеча
            prev_closed = df.iloc[-3]          # Предпоследняя закрытая свеча

            position = state['position']

            # --- 1. ПРОВЕРКА ОТКРЫТОЙ ПОЗИЦИИ ПО ТЕКУЩЕЙ ЦЕНЕ/СВЕЧЕ ---
            if position is not None:
                high_p = current_candle['high']
                low_p = current_candle['low']
                entry = position['entry']
                sl = position['sl']
                tp1_hit = position['tp1_hit']
                pos_type = position['type']

                # --- LONG ---
                if pos_type == 'LONG':
                    # Проверка Тейк-1 (Перевод в безубыток)
                    if not tp1_hit and high_p >= entry + TAKE_PROFIT_1_USD:
                        position['tp1_hit'] = True
                        position['sl'] = entry
                        state['position'] = position
                        save_state(state)
                        send_telegram(
                            f"🎯 <b>LONG TP1 Достигнут!</b>\n"
                            f"Цена вошла в +${TAKE_PROFIT_1_USD}. Стоп-лосс перенесен в Безубыток (${entry:.2f})"
                        )

                    # Проверка Тейк-2
                    elif high_p >= entry + TAKE_PROFIT_2_USD:
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
                            f"Текущий Баланс: <b>${state['balance']:.2f}</b>"
                        )
                        continue

                    # Проверка Стоп-лосс / Безубыток
                    elif low_p <= sl:
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
                                f"Выход по безубытку: ${exit_price:.2f}\n"
                                f"Комиссия Taker: -${fee:.2f}\n"
                                f"Текущий Баланс: <b>${state['balance']:.2f}</b>"
                            )
                        else:
                            state['losses'] += 1
                            save_state(state)
                            send_telegram(
                                f"🛑 <b>LONG ЗАКРЫТ ПО СТОП-ЛОССУ</b>\n"
                                f"Вход: ${entry:.2f} ➔ Выход: ${exit_price:.2f}\n"
                                f"Убыток: <b>-${abs(net_pnl):.2f}</b>\n"
                                f"Текущий Баланс: <b>${state['balance']:.2f}</b>"
                            )
                        continue

                # --- SHORT ---
                elif pos_type == 'SHORT':
                    # Проверка Тейк-1
                    if not tp1_hit and low_p <= entry - TAKE_PROFIT_1_USD:
                        position['tp1_hit'] = True
                        position['sl'] = entry
                        state['position'] = position
                        save_state(state)
                        send_telegram(
                            f"🎯 <b>SHORT TP1 Достигнут!</b>\n"
                            f"Цена прошла -${TAKE_PROFIT_1_USD}. Стоп-лосс перенесен в Безубыток (${entry:.2f})"
                        )

                    # Проверка Тейк-2
                    elif low_p <= entry - TAKE_PROFIT_2_USD:
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
                            f"Текущий Баланс: <b>${state['balance']:.2f}</b>"
                        )
                        continue

                    # Проверка Стоп-лосс / Безубыток
                    elif high_p >= sl:
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
                                f"Выход по безубытку: ${exit_price:.2f}\n"
                                f"Комиссия Taker: -${fee:.2f}\n"
                                f"Текущий Баланс: <b>${state['balance']:.2f}</b>"
                            )
                        else:
                            state['losses'] += 1
                            save_state(state)
                            send_telegram(
                                f"🛑 <b>SHORT ЗАКРЫТ ПО СТОП-ЛОССУ</b>\n"
                                f"Вход: ${entry:.2f} ➔ Выход: ${exit_price:.2f}\n"
                                f"Убыток: <b>-${abs(net_pnl):.2f}</b>\n"
                                f"Текущий Баланс: <b>${state['balance']:.2f}</b>"
                            )
                        continue

            # --- 2. ПОИСК НОВЫХ СИГНАЛОВ (Проверяем только при закрытии новой 5м свечи) ---
            last_ts = int(last_closed['timestamp'])
            if state['position'] is None and last_ts > state['last_processed_timestamp']:
                state['last_processed_timestamp'] = last_ts

                volume_filter = last_closed['volume'] > (last_closed['vol_sma'] * VOLUME_MULTIPLIER)
                round_level = round(prev_closed['close'])

                # Пробой вверх (LONG)
                if prev_closed['close'] < round_level and last_closed['close'] > round_level:
                    if last_closed['close'] > last_closed['ema200'] and volume_filter:
                        entry = round_level + 0.05
                        sl = entry - STOP_LOSS_USD
                        state['position'] = {
                            'type': 'LONG',
                            'entry': entry,
                            'sl': sl,
                            'tp1_hit': False
                        }
                        save_state(state)
                        send_telegram(
                            f"📈 <b>ОТКРЫТА PAPER-ПОЗИЦИЯ: LONG</b>\n"
                            f"Пара: {SYMBOL}\n"
                            f"Вход (Лимит): <b>${entry:.2f}</b>\n"
                            f"Стоп-Лосс: ${sl:.2f}\n"
                            f"Тейк-1 (Безубыток): ${entry + TAKE_PROFIT_1_USD:.2f}\n"
                            f"Тейк-2: ${entry + TAKE_PROFIT_2_USD:.2f}"
                        )

                # Пробой вниз (SHORT)
                elif prev_closed['close'] > round_level and last_closed['close'] < round_level:
                    if last_closed['close'] < last_closed['ema200'] and volume_filter:
                        entry = round_level - 0.05
                        sl = entry + STOP_LOSS_USD
                        state['position'] = {
                            'type': 'SHORT',
                            'entry': entry,
                            'sl': sl,
                            'tp1_hit': False
                        }
                        save_state(state)
                        send_telegram(
                            f"📉 <b>ОТКРЫТА PAPER-ПОЗИЦИЯ: SHORT</b>\n"
                            f"Пара: {SYMBOL}\n"
                            f"Вход (Лимит): <b>${entry:.2f}</b>\n"
                            f"Стоп-Лосс: ${sl:.2f}\n"
                            f"Тейк-1 (Безубыток): ${entry - TAKE_PROFIT_1_USD:.2f}\n"
                            f"Тейк-2: ${entry - TAKE_PROFIT_2_USD:.2f}"
                        )

        except Exception as e:
            print(f"Ошибка в цикле бота: {e}")

        # Проверка каждые 10 секунд для быстрого срабатывания TP/SL в реальном времени
        time.sleep(10)

if __name__ == "__main__":
    # Запускаем Web-сервер Flask в фоновом потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Запускаем торговый цикл
    bot_loop()
