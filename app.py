import os
import math
import json
import asyncio
import threading
import requests
import time
import websockets
from flask import Flask

# =====================================================================
# --- НАСТРОЙКИ ТОРГОВОГО АЛГОРИТМА ---
# =====================================================================
SYMBOL = 'SOL/USDC'
INITIAL_BALANCE = 100.0
LEVERAGE = 20
MARGIN_PER_TRADE = 10.0
POSITION_SIZE_USD = MARGIN_PER_TRADE * LEVERAGE  # $200 в рынке

# 1. Настройки сбора статистики (Warm-up Phase)
COLLECTION_PERIOD_HOURS = 24            # Сколько часов просто собирать стакан перед стартом торгов
MIN_EVENTS_TO_START_TRADING = 100        # Минимальное кол-во зафиксированных импульсов для перехода к торговле

# 2. Настройки агрегации стакана
ORDERBOOK_AGG_STEP = 0.1                # Шаг группировки под реальный стакан Solana

# 3. Базовые пороги детекта стен при начальном сборе
INITIAL_WALL_THRESHOLD_USD = 500_000  # Детектим потенциальные стены от $1M во время обучения
EATEN_WALL_THRESHOLD_USD = 100_000      # Порог проедания ($100k)

# 4. Фильтр "Тонкого стакана за стеной"
THIN_BOOK_CHECK_LEVELS = 3              
MAX_BEHIND_WALL_VOL_USD = 600_000       

# 5. Риск-менеджмент
TAKE_PROFIT_USD = 0.60                  
STOP_LOSS_OFFSET = 0.30                 

# 6. Настройки безубытка (Break-Even)
BREAKEVEN_TRIGGER_USD = 0.30            
BREAKEVEN_OFFSET_USD = 0.08             

# 7. Кулдаун и Комиссии
LEVEL_COOLDOWN_SEC = 10                 
TAKER_FEE = 0.0003

# 8. Системные файлы
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "paper_state.json"
HISTORY_FILE = "impulses_history.json"

# =====================================================================
# --- FLASK WEB SERVER ---
# =====================================================================
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "Wall Eating Bot is Running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# =====================================================================
# --- TELEGRAM NOTIFIER ---
# =====================================================================
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram Notice] {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=5)
        data = res.json()
        if not data.get("ok"):
            print(f"❌ Telegram Error: {data.get('description')}")
    except Exception as e:
        print(f"Ошибка отправки сообщения в Telegram: {e}")

# =====================================================================
# --- STATE & HISTORY MANAGEMENT ---
# =====================================================================
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
        "cooldown_until": 0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_fees": 0.0,
        "start_timestamp": int(time.time())
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(history_data):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения истории импульсов: {e}")

# =====================================================================
# --- МОДУЛЬ АНАЛИЗА И АДАПТАЦИИ ---
# =====================================================================
def log_impulse_event(direction, wall_price, peak_vol, start_price, max_price_reached, duration_sec):
    history = load_history()
    impulse_size = (max_price_reached - start_price) if direction == 'LONG' else (start_price - max_price_reached)

    event = {
        "timestamp": int(time.time()),
        "direction": direction,
        "wall_price": wall_price,
        "peak_volume_usd": round(peak_vol, 2),
        "start_price": round(start_price, 2),
        "max_price_reached": round(max_price_reached, 2),
        "impulse_size_usd": round(impulse_size, 4),
        "duration_seconds": round(duration_sec, 2),
        "is_successful_breakout": impulse_size >= TAKE_PROFIT_USD
    }
    
    history.append(event)
    save_history(history)
    
    print(f"📝 [СБОР] Стена {direction} ${wall_price} (Пик: ${peak_vol/1e6:.2f}M). "
          f"Импульс: ${impulse_size:.2f} за {duration_sec:.1f}с. Успех: {event['is_successful_breakout']}")

def get_adaptive_settings(state):
    """Определяет, готова ли система к торговле и высчитывает динамический порог стены."""
    history = load_history()
    now = time.time()
    elapsed_hours = (now - state.get("start_timestamp", now)) / 3600.0

    is_time_passed = elapsed_hours >= COLLECTION_PERIOD_HOURS
    is_enough_data = len(history) >= MIN_EVENTS_TO_START_TRADING

    if not (is_time_passed and is_enough_data):
        return INITIAL_WALL_THRESHOLD_USD, False, elapsed_hours

    successful_walls = [e["peak_volume_usd"] for e in history if e["is_successful_breakout"]]
    
    if len(successful_walls) < 5:
        all_walls = [e["peak_volume_usd"] for e in history]
        avg_vol = (sum(all_walls) / len(all_walls)) if all_walls else INITIAL_WALL_THRESHOLD_USD
        adaptive_threshold = max(600_000, avg_vol * 0.9)
    else:
        avg_success_vol = sum(successful_walls) / len(successful_walls)
        adaptive_threshold = max(600_000, avg_success_vol * 0.85)

    return round(adaptive_threshold, 2), True, elapsed_hours

# =====================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ СТАКАНА ---
# =====================================================================
def aggregate_orderbook(bids, asks, step=0.1):
    grouped_bids, grouped_asks = {}, {}

    for p_str, q_str in bids:
        price, qty = float(p_str), float(q_str)
        level = round(math.floor(price / step) * step, 4)
        grouped_bids[level] = grouped_bids.get(level, 0.0) + (price * qty)

    for p_str, q_str in asks:
        price, qty = float(p_str), float(q_str)
        level = round(math.floor(price / step) * step, 4)
        grouped_asks[level] = grouped_asks.get(level, 0.0) + (price * qty)

    return grouped_bids, grouped_asks

def is_book_thin_behind(grouped_book, wall_lvl, direction, step, num_levels, max_vol):
    for i in range(1, num_levels + 1):
        check_lvl = round(wall_lvl + (i * step), 4) if direction == 'LONG' else round(wall_lvl - (i * step), 4)
        vol = grouped_book.get(check_lvl, 0.0)
        if vol > max_vol:
            return False, check_lvl, vol
    return True, None, 0.0

def format_pnl_str(pnl: float) -> str:
    return f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

# =====================================================================
# --- ОСНОВНОЙ ЛУП WEBSOCKET БОТА ---
# =====================================================================
async def start_orderbook_ws():
    state = load_state()
    wall_threshold, is_trading_active, elapsed_h = get_adaptive_settings(state)

    mode_str = "🟢 БУМАЖНАЯ ТОРГОВЛЯ" if is_trading_active else f"🟡 СБОР ДАННЫХ ({elapsed_h:.1f}/{COLLECTION_PERIOD_HOURS} ч.)"
    send_telegram(
        f"⚡ <b>Wall Breakout Bot v3.2 Запущен!</b>\n"
        f"Режим: <b>{mode_str}</b>\n"
        f"Пара: {SYMBOL} | Шаг стакана: <b>${ORDERBOOK_AGG_STEP}</b>\n"
        f"Баланс: ${state['balance']:.2f}"
    )

    ws_symbol = SYMBOL.replace('/', '').lower()
    ws_url = f"wss://fstream.binance.com/ws/{ws_symbol}@depth20@100ms"

    tracked_ask_walls = {}
    tracked_bid_walls = {}
    level_cooldowns = {}
    active_breakouts = []
    
    # Флаг, отслеживающий, выводили ли мы уже стартовые настройки торговли
    trading_settings_announced = False

    while True:
        try:
            print(f"🔌 Подключение к WebSocket стакана Futures: {ws_url}")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print("✅ Стакан подключен!")

                async for message in ws:
                    data = json.loads(message)
                    bids, asks = data.get('b', []), data.get('a', [])

                    if not bids or not asks:
                        continue

                    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
                    current_price = (best_bid + best_ask) / 2.0

                    # 0. Обновление состояния режимов
                    wall_threshold, is_trading_active, elapsed_h = get_adaptive_settings(state)

                    # --- ОПОВЕЩЕНИЕ О ВЫБРАННЫХ НАСТРОЙКАХ ТОРГОВЛИ ---
                    if is_trading_active and not trading_settings_announced:
                        history = load_history()
                        succ_walls = [e for e in history if e.get("is_successful_breakout")]
                        
                        msg = (
                            f"🚀 <b>РЕЖИМ ТОРГОВЛИ АКТИВИРОВАН!</b>\n\n"
                            f"📊 <b>Адаптивные параметры обучения:</b>\n"
                            f"• Расчитанный порог стены: <b>${wall_threshold:,.2f}</b>\n"
                            f"• Всего импульсов в базе: <b>{len(history)}</b>\n"
                            f"• Успешных пробоев: <b>{len(succ_walls)}</b>\n"
                            f"• Время обучения: <b>{elapsed_h:.1f} ч.</b>\n\n"
                            f"⚙️ <b>Рабочие настройки торговли:</b>\n"
                            f"• Плечо / Позиция: <b>{LEVERAGE}x (${POSITION_SIZE_USD:.0f})</b>\n"
                            f"• Take Profit: <b>+${TAKE_PROFIT_USD}</b>\n"
                            f"• Stop Loss: <b>-${STOP_LOSS_OFFSET}</b>\n"
                            f"• Триггер безубытка: <b>+${BREAKEVEN_TRIGGER_USD}</b> (сдвиг +${BREAKEVEN_OFFSET_USD})\n"
                            f"• Лимит плотности за стеной: <b>${MAX_BEHIND_WALL_VOL_USD:,.0f}</b>"
                        )
                        send_telegram(msg)
                        trading_settings_announced = True

                    # Агрегация
                    g_bids, g_asks = aggregate_orderbook(bids, asks, step=ORDERBOOK_AGG_STEP)

                    # -------------------------------------------------
                    # 1. ОБРАБОТКА ИМПУЛЬСОВ И ОБУЧЕНИЕ ПОСТ-ФАКТУМ
                    # -------------------------------------------------
                    now = time.time()
                    for breakout in active_breakouts[:]:
                        if breakout['direction'] == 'LONG':
                            breakout['max_price'] = max(breakout['max_price'], current_price)
                        else:
                            breakout['max_price'] = min(breakout['max_price'], current_price)

                        if now - breakout['breakout_time'] >= 15:
                            log_impulse_event(
                                direction=breakout['direction'],
                                wall_price=breakout['wall_price'],
                                peak_vol=breakout['peak_vol'],
                                start_price=breakout['start_price'],
                                max_price_reached=breakout['max_price'],
                                duration_sec=now - breakout['breakout_time']
                            )
                            active_breakouts.remove(breakout)

                    # -------------------------------------------------
                    # 2. УПРАВЛЕНИЕ ПОЗИЦИЕЙ (ТОЛЬКО В РЕЖИМЕ ТОРГОВЛИ)
                    # -------------------------------------------------
                    pos = state.get('position')
                    if pos:
                        p_type, entry = pos['type'], pos['entry']
                        move = (current_price - entry) if p_type == 'LONG' else (entry - current_price)

                        if not pos.get('is_breakeven') and move >= BREAKEVEN_TRIGGER_USD:
                            pos['is_breakeven'] = True
                            pos['sl'] = (entry + BREAKEVEN_OFFSET_USD) if p_type == 'LONG' else (entry - BREAKEVEN_OFFSET_USD)
                            save_state(state)
                            send_telegram(f"🛡️ <b>[БЕЗУБЫТОК]</b> Новый SL: ${pos['sl']:.2f}")

                        is_tp = move >= TAKE_PROFIT_USD
                        is_sl = (current_price <= pos['sl']) if p_type == 'LONG' else (current_price >= pos['sl'])

                        if is_tp or is_sl:
                            exit_price = pos['tp'] if is_tp else pos['sl']
                            raw_pnl = (exit_price - entry) * (POSITION_SIZE_USD / entry) if p_type == 'LONG' else (entry - exit_price) * (POSITION_SIZE_USD / entry)
                            fee = POSITION_SIZE_USD * TAKER_FEE * 2
                            net_pnl = raw_pnl - fee

                            state['balance'] += net_pnl
                            state['total_trades'] += 1
                            state['total_fees'] += fee
                            state['wins'] += 1 if net_pnl > 0 else 0
                            state['losses'] += 1 if net_pnl <= 0 else 0

                            res_icon = "🎯 ТЕЙК-ПРОФИТ" if is_tp else ("🛡️ БЕЗУБЫТОК" if pos.get('is_breakeven') and net_pnl >= 0 else "❌ СТОП-ЛОСС")
                            send_telegram(
                                f"{res_icon}\nВыход: <b>${exit_price:.2f}</b> | PnL: <b>{format_pnl_str(net_pnl)}</b>\n"
                                f"Баланс: <b>${state['balance']:.2f}</b>"
                            )

                            state['position'] = None
                            state['cooldown_until'] = time.time() + 5
                            save_state(state)

                    # -------------------------------------------------
                    # 3. МОНИТОРИНГ СТЕН И СИГНАЛОВ
                    # -------------------------------------------------
                    
                    # Обработка ASKS (LONG)
                    for ask_lvl in list(tracked_ask_walls.keys()):
                        ask_vol = g_asks.get(ask_lvl, 0.0)
                        
                        if ask_vol <= EATEN_WALL_THRESHOLD_USD:
                            peak_vol = tracked_ask_walls[ask_lvl]
                            
                            if current_price >= ask_lvl:
                                if time.time() > level_cooldowns.get(ask_lvl, 0):
                                    active_breakouts.append({
                                        "direction": "LONG", "wall_price": ask_lvl, "peak_vol": peak_vol,
                                        "start_price": current_price, "max_price": current_price, "breakout_time": time.time()
                                    })
                                    level_cooldowns[ask_lvl] = time.time() + LEVEL_COOLDOWN_SEC

                                    if is_trading_active and not state.get('position') and time.time() >= state.get('cooldown_until', 0):
                                        is_thin, b_lvl, b_vol = is_book_thin_behind(g_asks, ask_lvl, 'LONG', ORDERBOOK_AGG_STEP, THIN_BOOK_CHECK_LEVELS, MAX_BEHIND_WALL_VOL_USD)
                                        if is_thin:
                                            entry_p = current_price
                                            state['position'] = {
                                                'type': 'LONG', 'entry': entry_p, 'sl': entry_p - STOP_LOSS_OFFSET,
                                                'tp': entry_p + TAKE_PROFIT_USD, 'wall_price': ask_lvl, 'is_breakeven': False
                                            }
                                            save_state(state)
                                            send_telegram(f"📈 <b>ВХОД В LONG</b> (Пробито ${ask_lvl:.2f})\nВход: ${entry_p:.2f}")

                            del tracked_ask_walls[ask_lvl]

                    for ask_lvl, ask_vol in g_asks.items():
                        if ask_lvl >= current_price and ask_vol >= wall_threshold:
                            tracked_ask_walls[ask_lvl] = max(tracked_ask_walls.get(ask_lvl, 0), ask_vol)


                    # Обработка BIDS (SHORT)
                    for bid_lvl in list(tracked_bid_walls.keys()):
                        bid_vol = g_bids.get(bid_lvl, 0.0)
                        
                        if bid_vol <= EATEN_WALL_THRESHOLD_USD:
                            peak_vol = tracked_bid_walls[bid_lvl]
                            
                            if current_price <= bid_lvl:
                                if time.time() > level_cooldowns.get(bid_lvl, 0):
                                    active_breakouts.append({
                                        "direction": "SHORT", "wall_price": bid_lvl, "peak_vol": peak_vol,
                                        "start_price": current_price, "max_price": current_price, "breakout_time": time.time()
                                    })
                                    level_cooldowns[bid_lvl] = time.time() + LEVEL_COOLDOWN_SEC

                                    if is_trading_active and not state.get('position') and time.time() >= state.get('cooldown_until', 0):
                                        is_thin, b_lvl, b_vol = is_book_thin_behind(g_bids, bid_lvl, 'SHORT', ORDERBOOK_AGG_STEP, THIN_BOOK_CHECK_LEVELS, MAX_BEHIND_WALL_VOL_USD)
                                        if is_thin:
                                            entry_p = current_price
                                            state['position'] = {
                                                'type': 'SHORT', 'entry': entry_p, 'sl': entry_p + STOP_LOSS_OFFSET,
                                                'tp': entry_p - TAKE_PROFIT_USD, 'wall_price': bid_lvl, 'is_breakeven': False
                                            }
                                            save_state(state)
                                            send_telegram(f"📉 <b>ВХОД В SHORT</b> (Пробито ${bid_lvl:.2f})\nВход: ${entry_p:.2f}")

                            del tracked_bid_walls[bid_lvl]

                    for bid_lvl, bid_vol in g_bids.items():
                        if bid_lvl <= current_price and bid_vol >= wall_threshold:
                            tracked_bid_walls[bid_lvl] = max(tracked_bid_walls.get(bid_lvl, 0), bid_vol)

        except Exception as e:
            print(f"⚠️ Ошибка соединения стакана: {e}. Переподключение через 5 сек...")
            await asyncio.sleep(5)

# =====================================================================
# --- ТОЧКА ВХОДА ---
# =====================================================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    asyncio.run(start_orderbook_ws())
