import os
import math
import json
import asyncio
import threading
import time
import websockets
import aiohttp
import ccxt.async_support as ccxt
from flask import Flask

# =====================================================================
# --- НАСТРОЙКИ БОЕВОЙ ТОРГОВЛИ ---
# =====================================================================
SYMBOL = 'SOL/USDC'
SYMBOL_WS = 'solusdc'

# Флаг реальной торговли (True = реальные ордера, False = тестовый режим)
ENABLE_REAL_TRADING = os.environ.get("ENABLE_REAL_TRADING", "False").lower() == "true"

INITIAL_BALANCE = 100.0
LEVERAGE = 20
MARGIN_PER_TRADE = 10.0
POSITION_SIZE_USD = MARGIN_PER_TRADE * LEVERAGE  # $200 в рынке

# 1. Сбор статистики и диапазоны стен
COLLECTION_PERIOD_HOURS = 24            
MIN_EVENTS_TO_START_TRADING = 20       
ORDERBOOK_AGG_STEP = 0.1                
INITIAL_WALL_THRESHOLD_USD = 1_000_000    # Минимальный начальный порог ($500k)
MAX_WALL_THRESHOLD_USD = 2_000_000      # Верхний предел (отсекает тяжелые $2.5M+ стены)
EATEN_WALL_THRESHOLD_USD = 100_000      # Порог считающейся «съеденной» стены

# 2. Фильтр Спуфинга (Анти-манипуляция)
MIN_SPOOF_EXECUTION_RATIO = 0.40        # 40% от объема стены должно быть выкуплено по факту

# 3. Фильтр "Тонкого стакана за стеной"
THIN_BOOK_CHECK_LEVELS = 3              
MAX_BEHIND_WALL_VOL_USD = 600_000       

# 4. Риск-менеджмент
TAKE_PROFIT_USD = 0.60                  
STOP_LOSS_OFFSET = 0.30                 
BREAKEVEN_TRIGGER_USD = 0.30            
BREAKEVEN_OFFSET_USD = 0.08             
LEVEL_COOLDOWN_SEC = 10                 
TAKER_FEE = 0.0003                      

# 5. Ключи и Системные файлы
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY", "")
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
    return "Wall Eating Bot v4.1 Filtered Range Ready", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# =====================================================================
# --- КЭШ СОСТОЯНИЯ И АСИНХРОННЫЙ I/O ---
# =====================================================================
class BotStateCache:
    def __init__(self):
        self.state = self._load_state_disk()
        self.history = self._load_history_disk()
        self.lock = asyncio.Lock()

    def _load_state_disk(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "balance": INITIAL_BALANCE, "position": None, "cooldown_until": 0,
            "total_trades": 0, "wins": 0, "losses": 0, "total_fees": 0.0,
            "start_timestamp": int(time.time())
        }

    def _load_history_disk(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    async def save_async(self):
        """Неблокирующая фоновая запись на диск"""
        async with self.lock:
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.history, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"⚠️ Ошибка записи на диск: {e}")

bot_cache = BotStateCache()

async def get_public_ip(http_session: aiohttp.ClientSession) -> str:
    """Получает внешний IP-адрес сервера для привязки в Binance API Whitelist"""
    try:
        async with http_session.get("https://api.ipify.org?format=json", timeout=3) as resp:
            data = await resp.json()
            return data.get("ip", "Не определен")
    except Exception:
        return "Не удалось определить"

async def send_telegram_async(http_session: aiohttp.ClientSession, message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram Notice] {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        async with http_session.post(url, json=payload, timeout=5) as resp:
            res = await resp.json()
            if not res.get("ok"):
                print(f"❌ Telegram Error: {res.get('description')}")
    except Exception as e:
        print(f"Ошибка отправки Telegram: {e}")

# =====================================================================
# --- BINANCE FUTURES EXECUTION ENGINE (CCXT) ---
# =====================================================================
class ExecutionEngine:
    def __init__(self):
        self.exchange = None
        if ENABLE_REAL_TRADING:
            self.exchange = ccxt.binance({
                'apiKey': BINANCE_API_KEY,
                'secret': BINANCE_SECRET_KEY,
                'options': {'defaultType': 'future'},
                'enableRateLimit': True
            })

    async def init_market(self):
        if ENABLE_REAL_TRADING and self.exchange:
            try:
                await self.exchange.set_leverage(LEVERAGE, SYMBOL)
                print(f"✅ Плечо {LEVERAGE}x установлено на Binance Futures для {SYMBOL}")
            except Exception as e:
                print(f"❌ Ошибка инициализации биржи: {e}")

    async def execute_market_order(self, side: str, amount_sol: float):
        if not ENABLE_REAL_TRADING or not self.exchange:
            return {"id": "PAPER_ORDER", "status": "closed"}
        try:
            order = await self.exchange.create_order(
                symbol=SYMBOL, type='market', side=side, amount=amount_sol
            )
            return order
        except Exception as e:
            print(f"🚨 Ошибка выполнения ордера {side}: {e}")
            return None

    async def close(self):
        if self.exchange:
            await self.exchange.close()

executor = ExecutionEngine()

# =====================================================================
# --- ВСПОМОГАТЕЛЬНАЯ ЛОГИКА АНАЛИЗА И АДАПТАЦИИ ---
# =====================================================================
def get_adaptive_settings():
    """Динамический расчет рабочего коридора объемов (мин/макс)"""
    now = time.time()
    state = bot_cache.state
    history = bot_cache.history[-200:] # Скользящее окно 200 импульсов

    elapsed_hours = (now - state.get("start_timestamp", now)) / 3600.0
    is_time_passed = elapsed_hours >= COLLECTION_PERIOD_HOURS
    is_enough_data = len(history) >= MIN_EVENTS_TO_START_TRADING

    if not (is_time_passed and is_enough_data):
        return INITIAL_WALL_THRESHOLD_USD, MAX_WALL_THRESHOLD_USD, False, elapsed_hours

    successful_walls = [e["peak_volume_usd"] for e in history if e.get("is_successful_breakout")]
    
    if len(successful_walls) < 5:
        all_walls = [e["peak_volume_usd"] for e in history]
        avg_vol = (sum(all_walls) / len(all_walls)) if all_walls else INITIAL_WALL_THRESHOLD_USD
        min_thresh = max(400_000, avg_vol * 0.85)
        max_thresh = MAX_WALL_THRESHOLD_USD
    else:
        avg_success_vol = sum(successful_walls) / len(successful_walls)
        min_thresh = max(400_000, avg_success_vol * 0.8)
        # Динамический верхний предел: не дает торговать слишком тяжелые плотности
        max_thresh = min(MAX_WALL_THRESHOLD_USD, avg_success_vol * 1.4)

    return round(min_thresh, 2), round(max_thresh, 2), True, elapsed_hours

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

# =====================================================================
# --- ОСНОВНОЙ async ЛУП WEBSOCKET ---
# =====================================================================
async def start_orderbook_ws():
    await executor.init_market()
    
    async with aiohttp.ClientSession() as http_session:
        min_thresh, max_thresh, is_trading_active, elapsed_h = get_adaptive_settings()
        mode_str = ("🔴 НАСТОЯЩИЕ ДЕНЬГИ" if ENABLE_REAL_TRADING else "🟢 БУМАЖНАЯ ТОРГОВЛЯ") if is_trading_active else f"🟡 СБОР ДАННЫХ ({elapsed_h:.1f}/{COLLECTION_PERIOD_HOURS} ч.)"
        
        public_ip = await get_public_ip(http_session)
        port = os.environ.get("PORT", "8080")
        
        await send_telegram_async(
            http_session,
            f"⚡ <b>Wall Breakout Bot v4.1 READY!</b>\n"
            f"Режим: <b>{mode_str}</b>\n"
            f"Пара: {SYMBOL} | Защита от спуфинга: <b>{MIN_SPOOF_EXECUTION_RATIO*100}%</b>\n"
            f"Баланс: ${bot_cache.state['balance']:.2f}\n\n"
            f"🌐 <b>Сетевые эндпоинты:</b>\n"
            f"• Внешний IP сервера: <code>{public_ip}</code>\n"
            f"• Binance API Endpoint: <code>https://fapi.binance.com</code>\n"
            f"• Health Check: <code>http://{public_ip}:{port}/health</code>"
        )

        ws_url = f"wss://fstream.binance.com/stream?streams={SYMBOL_WS}@depth20@100ms/{SYMBOL_WS}@aggTrade"

        tracked_ask_walls = {}
        tracked_bid_walls = {}
        level_cooldowns = {}
        active_breakouts = []
        trading_settings_announced = False

        while True:
            try:
                print(f"🔌 Подключение к комбинированному WS: {ws_url}")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    print("✅ Стакан и Лента сделок успешно подключены!")

                    async for message in ws:
                        payload = json.loads(message)
                        stream_type = payload.get('stream', '')
                        data = payload.get('data', {})

                        # -------------------------------------------------
                        # A. ОБРАБОТКА ЛЕНТЫ СДЕЛОК (Защита от спуфинга)
                        # -------------------------------------------------
                        if 'aggTrade' in stream_type:
                            trade_price = float(data['p'])
                            trade_qty = float(data['q'])
                            trade_vol_usd = trade_price * trade_qty
                            
                            trade_lvl = round(math.floor(trade_price / ORDERBOOK_AGG_STEP) * ORDERBOOK_AGG_STEP, 4)

                            if trade_lvl in tracked_ask_walls:
                                tracked_ask_walls[trade_lvl]['executed_vol'] += trade_vol_usd
                            if trade_lvl in tracked_bid_walls:
                                tracked_bid_walls[trade_lvl]['executed_vol'] += trade_vol_usd
                            continue

                        # -------------------------------------------------
                        # B. ОБРАБОТКА СТАКАНА И ТОРГОВОЙ ЛОГИКИ
                        # -------------------------------------------------
                        bids, asks = data.get('b', []), data.get('a', [])
                        if not bids or not asks:
                            continue

                        best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
                        current_price = (best_bid + best_ask) / 2.0

                        min_thresh, max_thresh, is_trading_active, elapsed_h = get_adaptive_settings()

                        if is_trading_active and not trading_settings_announced:
                            succ_walls = [e for e in bot_cache.history if e.get("is_successful_breakout")]
                            msg = (
                                f"🚀 <b>ТОРГОВЛЯ АКТИВИРОВАНА ({'REAL' if ENABLE_REAL_TRADING else 'PAPER'})!</b>\n\n"
                                f"📊 <b>Рабочий коридор стен:</b>\n"
                                f"• Диапазон: <b>${min_thresh:,.0f} — ${max_thresh:,.0f}</b>\n"
                                f"• Импульсов в базе: <b>{len(bot_cache.history)}</b>\n"
                                f"• Успешных пробоев: <b>{len(succ_walls)}</b>\n\n"
                                f"⚙️ <b>Параметры исполнения:</b>\n"
                                f"• Позиция: <b>{LEVERAGE}x (${POSITION_SIZE_USD:.0f})</b>\n"
                                f"• Фильтр спуфинга: <b>Мин. {MIN_SPOOF_EXECUTION_RATIO*100}% объема в сделках</b>"
                            )
                            await send_telegram_async(http_session, msg)
                            trading_settings_announced = True

                        g_bids, g_asks = aggregate_orderbook(bids, asks, step=ORDERBOOK_AGG_STEP)

                        # 1. Запись импульсов
                        now = time.time()
                        for breakout in active_breakouts[:]:
                            if breakout['direction'] == 'LONG':
                                breakout['max_price'] = max(breakout['max_price'], current_price)
                            else:
                                breakout['max_price'] = min(breakout['max_price'], current_price)

                            if now - breakout['breakout_time'] >= 15:
                                impulse_size = (breakout['max_price'] - breakout['start_price']) if breakout['direction'] == 'LONG' else (breakout['start_price'] - breakout['max_price'])
                                is_succ = impulse_size >= TAKE_PROFIT_USD
                                
                                bot_cache.history.append({
                                    "timestamp": int(now), "direction": breakout['direction'],
                                    "wall_price": breakout['wall_price'], "peak_volume_usd": round(breakout['peak_vol'], 2),
                                    "start_price": round(breakout['start_price'], 2), "max_price_reached": round(breakout['max_price'], 2),
                                    "impulse_size_usd": round(impulse_size, 4), "is_successful_breakout": is_succ
                                })
                                asyncio.create_task(bot_cache.save_async())
                                active_breakouts.remove(breakout)

                        # 2. Сопровождение открытой позиции
                        pos = bot_cache.state.get('position')
                        if pos:
                            p_type, entry = pos['type'], pos['entry']
                            move = (current_price - entry) if p_type == 'LONG' else (entry - current_price)

                            if not pos.get('is_breakeven') and move >= BREAKEVEN_TRIGGER_USD:
                                pos['is_breakeven'] = True
                                pos['sl'] = (entry + BREAKEVEN_OFFSET_USD) if p_type == 'LONG' else (entry - BREAKEVEN_OFFSET_USD)
                                asyncio.create_task(bot_cache.save_async())
                                await send_telegram_async(http_session, f"🛡️ <b>[БЕЗУБЫТОК]</b> SL перенесен на ${pos['sl']:.2f}")

                            is_tp = move >= TAKE_PROFIT_USD
                            is_sl = (current_price <= pos['sl']) if p_type == 'LONG' else (current_price >= pos['sl'])

                            if is_tp or is_sl:
                                exit_price = pos['tp'] if is_tp else pos['sl']
                                close_side = 'sell' if p_type == 'LONG' else 'buy'
                                
                                sol_amount = POSITION_SIZE_USD / entry
                                await executor.execute_market_order(close_side, sol_amount)

                                raw_pnl = (exit_price - entry) * sol_amount if p_type == 'LONG' else (entry - exit_price) * sol_amount
                                fee = POSITION_SIZE_USD * TAKER_FEE * 2
                                net_pnl = raw_pnl - fee

                                bot_cache.state['balance'] += net_pnl
                                bot_cache.state['total_trades'] += 1
                                bot_cache.state['total_fees'] += fee
                                bot_cache.state['wins'] += 1 if net_pnl > 0 else 0
                                bot_cache.state['losses'] += 1 if net_pnl <= 0 else 0

                                res_icon = "🎯 ТЕЙК-ПРОФИТ" if is_tp else ("🛡️ БЕЗУБЫТОК" if pos.get('is_breakeven') and net_pnl >= 0 else "❌ СТОП-ЛОСС")
                                await send_telegram_async(
                                    http_session,
                                    f"{res_icon}\nВыход: <b>${exit_price:.2f}</b> | PnL: <b>${net_pnl:.2f}</b>\n"
                                    f"Баланс: <b>${bot_cache.state['balance']:.2f}</b>"
                                )

                                bot_cache.state['position'] = None
                                bot_cache.state['cooldown_until'] = time.time() + 5
                                asyncio.create_task(bot_cache.save_async())

                        # 3. Детекция стен ASKS (LONG)
                        for ask_lvl in list(tracked_ask_walls.keys()):
                            ask_vol = g_asks.get(ask_lvl, 0.0)
                            
                            if ask_vol <= EATEN_WALL_THRESHOLD_USD:
                                wall_data = tracked_ask_walls[ask_lvl]
                                peak_vol = wall_data['peak_vol']
                                exec_vol = wall_data['executed_vol']
                                
                                required_exec_vol = peak_vol * MIN_SPOOF_EXECUTION_RATIO
                                is_real_eat = exec_vol >= required_exec_vol

                                if current_price >= ask_lvl:
                                    if is_real_eat:
                                        if time.time() > level_cooldowns.get(ask_lvl, 0):
                                            active_breakouts.append({
                                                "direction": "LONG", "wall_price": ask_lvl, "peak_vol": peak_vol,
                                                "start_price": current_price, "max_price": current_price, "breakout_time": time.time()
                                            })
                                            level_cooldowns[ask_lvl] = time.time() + LEVEL_COOLDOWN_SEC

                                            if is_trading_active and not bot_cache.state.get('position') and time.time() >= bot_cache.state.get('cooldown_until', 0):
                                                is_thin, _, _ = is_book_thin_behind(g_asks, ask_lvl, 'LONG', ORDERBOOK_AGG_STEP, THIN_BOOK_CHECK_LEVELS, MAX_BEHIND_WALL_VOL_USD)
                                                if is_thin:
                                                    entry_p = current_price
                                                    sol_qty = POSITION_SIZE_USD / entry_p
                                                    
                                                    order_res = await executor.execute_market_order('buy', sol_qty)
                                                    if order_res:
                                                        bot_cache.state['position'] = {
                                                            'type': 'LONG', 'entry': entry_p, 'sl': entry_p - STOP_LOSS_OFFSET,
                                                            'tp': entry_p + TAKE_PROFIT_USD, 'wall_price': ask_lvl, 'is_breakeven': False
                                                        }
                                                        asyncio.create_task(bot_cache.save_async())
                                                        await send_telegram_async(http_session, f"📈 <b>ВХОД В LONG</b>\nСтена ${ask_lvl:.2f} (${peak_vol/1e3:.0f}k) выкуплена (Сделок: ${exec_vol/1e3:.0f}k)\nВход: ${entry_p:.2f}")
                                    else:
                                        print(f"🛡️ [СПУФИНГ ОТФИЛЬТРОВАН] Стена ${ask_lvl} снята ордерами (Выкуплено только ${exec_vol/1e3:.0f}k из нужных ${required_exec_vol/1e3:.0f}k)")

                                del tracked_ask_walls[ask_lvl]

                        # Трекинг только тех стен ASKS, что попадают в коридор min_thresh ... max_thresh
                        for ask_lvl, ask_vol in g_asks.items():
                            if ask_lvl >= current_price and (min_thresh <= ask_vol <= max_thresh):
                                if ask_lvl not in tracked_ask_walls:
                                    tracked_ask_walls[ask_lvl] = {'peak_vol': ask_vol, 'executed_vol': 0.0}
                                else:
                                    tracked_ask_walls[ask_lvl]['peak_vol'] = max(tracked_ask_walls[ask_lvl]['peak_vol'], ask_vol)

                        # 4. Детекция стен BIDS (SHORT)
                        for bid_lvl in list(tracked_bid_walls.keys()):
                            bid_vol = g_bids.get(bid_lvl, 0.0)
                            
                            if bid_vol <= EATEN_WALL_THRESHOLD_USD:
                                wall_data = tracked_bid_walls[bid_lvl]
                                peak_vol = wall_data['peak_vol']
                                exec_vol = wall_data['executed_vol']
                                
                                required_exec_vol = peak_vol * MIN_SPOOF_EXECUTION_RATIO
                                is_real_eat = exec_vol >= required_exec_vol

                                if current_price <= bid_lvl:
                                    if is_real_eat:
                                        if time.time() > level_cooldowns.get(bid_lvl, 0):
                                            active_breakouts.append({
                                                "direction": "SHORT", "wall_price": bid_lvl, "peak_vol": peak_vol,
                                                "start_price": current_price, "max_price": current_price, "breakout_time": time.time()
                                            })
                                            level_cooldowns[bid_lvl] = time.time() + LEVEL_COOLDOWN_SEC

                                            if is_trading_active and not bot_cache.state.get('position') and time.time() >= bot_cache.state.get('cooldown_until', 0):
                                                is_thin, _, _ = is_book_thin_behind(g_bids, bid_lvl, 'SHORT', ORDERBOOK_AGG_STEP, THIN_BOOK_CHECK_LEVELS, MAX_BEHIND_WALL_VOL_USD)
                                                if is_thin:
                                                    entry_p = current_price
                                                    sol_qty = POSITION_SIZE_USD / entry_p
                                                    
                                                    order_res = await executor.execute_market_order('sell', sol_qty)
                                                    if order_res:
                                                        bot_cache.state['position'] = {
                                                            'type': 'SHORT', 'entry': entry_p, 'sl': entry_p + STOP_LOSS_OFFSET,
                                                            'tp': entry_p - TAKE_PROFIT_USD, 'wall_price': bid_lvl, 'is_breakeven': False
                                                        }
                                                        asyncio.create_task(bot_cache.save_async())
                                                        await send_telegram_async(http_session, f"📉 <b>ВХОД В SHORT</b>\nСтена ${bid_lvl:.2f} (${peak_vol/1e3:.0f}k) продавлена (Сделок: ${exec_vol/1e3:.0f}k)\nВход: ${entry_p:.2f}")
                                    else:
                                        print(f"🛡️ [СПУФИНГ ОТФИЛЬТРОВАН] Стена ${bid_lvl} снята ордерами (Продано только ${exec_vol/1e3:.0f}k из нужных ${required_exec_vol/1e3:.0f}k)")

                                del tracked_bid_walls[bid_lvl]

                        # Трекинг только тех стен BIDS, что попадают в коридор min_thresh ... max_thresh
                        for bid_lvl, bid_vol in g_bids.items():
                            if bid_lvl <= current_price and (min_thresh <= bid_vol <= max_thresh):
                                if bid_lvl not in tracked_bid_walls:
                                    tracked_bid_walls[bid_lvl] = {'peak_vol': bid_vol, 'executed_vol': 0.0}
                                else:
                                    tracked_bid_walls[bid_lvl]['peak_vol'] = max(tracked_bid_walls[bid_lvl]['peak_vol'], bid_vol)

            except Exception as e:
                print(f"⚠️ Ошибка соединения WS: {e}. Переподключение через 5 сек...")
                await asyncio.sleep(5)

# =====================================================================
# --- ТОЧКА ВХОДА ---
# =====================================================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    try:
        asyncio.run(start_orderbook_ws())
    finally:
        asyncio.run(executor.close())
