import os
import time
import threading
from datetime import datetime
import requests
import ccxt
from flask import Flask

# Инициализация Flask для Render (чтобы сервис не падал по таймауту портов)
app = Flask(__name__)

@app.route('/')
def home():
    return f"Бот активен. Текущее время сервера: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

# === ПОЛУЧЕНИЕ НАСТРОЕК ИЗ ОКРУЖЕНИЯ RENDER ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВАШ_ТОКЕН_ЕСЛИ_НЕ_ЧЕРЕЗ_ENV")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "ВАШ_ID_ЕСЛИ_НЕ_ЧЕРЕЗ_ENV")

# === НАСТРОЙКИ СТРАТЕГИИ ===
SYMBOL = "SOL/USDC"   # Торгуемая пара
LEVERAGE = 38         # Имитируемое плечо для расчета прибыли

# Глобальные переменные для отслеживания виртуальной позиции
# Статусы: None, "LONG_OPENED", "SHORT_OPENED"
active_trade = None  

def send_telegram(message):
    """Отправка уведомлений в Telegram"""
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def get_aggregated_orderbook():
    """Получение стакана Binance и жесткая агрегация строго по $1"""
    exchange = ccxt.binance({'enableRateLimit': True})
    try:
        # Берем глубокий стакан, чтобы после округления до $1 у нас остались объемы
        orderbook = exchange.fetch_order_book(SYMBOL, limit=500)
    except Exception as e:
        print(f"Ошибка получения стакана: {e}")
        return None, None, None

    bids_raw = orderbook['bids']  # Покупатели [[цена, объем], ...]
    asks_raw = orderbook['asks']  # Продавцы [[цена, объем], ...]

    if not bids_raw or not asks_raw:
        return None, None, None

    current_price = float(bids_raw[0][0])

    # Агрегатор для Продавцов (Asks) -> Округление вверх до целого доллара
    aggregated_asks = {}
    for price, vol in asks_raw:
        agg_price = int(price) + 1  # Округляем до верхней целой плиты (например, 73.25 -> 74)
        aggregated_asks[agg_price] = aggregated_asks.get(agg_price, 0.0) + float(vol)

    # Агрегатор для Покупателей (Bids) -> Округление вниз до целого доллара
    aggregated_bids = {}
    for price, vol in bids_raw:
        agg_price = int(price)  # Округляем до нижней целой плиты (например, 72.80 -> 72)
        aggregated_bids[agg_price] = aggregated_bids.get(agg_price, 0.0) + float(vol)

    # Сортируем: аски по возрастанию цены, биды по убыванию цены
    sorted_asks = sorted([[k, v] for k, v in aggregated_asks.items()], key=lambda x: x[0])
    sorted_bids = sorted([[k, v] for k, v in aggregated_bids.items()], key=lambda x: x[0], reverse=True)

    return current_price, sorted_asks, sorted_bids

def bot_loop():
    """Основной бесконечный цикл стратегии в фоновом потоке"""
    global active_trade
    print("Фоновый модуль сканирования стакана успешно запущен.")
    
    while True:
        try:
            current_price, asks, bids = get_aggregated_orderbook()
            if not current_price:
                time.sleep(5)
                continue

            # 1. СОПРОВОЖДЕНИЕ ОТКРЫТОЙ В УМЕ СДЕЛКИ
            if active_trade:
                # Проверка для ЛОНГ позиции
                if active_trade['side'] == 'LONG':
                    # Проверяем Стоп-Лосс
                    if current_price <= active_trade['stop_loss']:
                        loss_pct = ((active_trade['stop_loss'] - active_trade['entry_price']) / active_trade['entry_price']) * 100 * LEVERAGE
                        send_telegram(f"💀 *Стоп-Лосс по ЛОНГУ на бумаге!*\nПозиция принудительно закрыта на {current_price}.\nУбыток с плечом {LEVERAGE}х: `{loss_pct:.2f}%`")
                        active_trade = None

                    # Проверяем Тейк-Профит 1
                    elif not active_trade['tp1_triggered'] and current_price >= active_trade['take_profit_1']:
                        active_trade['tp1_triggered'] = True
                        profit_pct = ((active_trade['take_profit_1'] - active_trade['entry_price']) / active_trade['entry_price']) * 100 * LEVERAGE
                        send_telegram(f"💰 *Тейк-Профит 1 (Лонг) выполнен!*\nЗакрыто 50% позиции на {active_trade['take_profit_1']}.\nПрофит части: `+{profit_pct:.2f}%` \nОстаток зафиксируется на {active_trade['take_profit_2']}")

                    # Проверяем Тейк-Профит 2
                    elif active_trade['tp1_triggered'] and current_price >= active_trade['take_profit_2']:
                        profit_pct = ((active_trade['take_profit_2'] - active_trade['entry_price']) / active_trade['entry_price']) * 100 * LEVERAGE
                        send_telegram(f"🟢 *Тейк-Профит 2 (Лонг) выполнен!*\nПозиция полностью закрыта на {active_trade['take_profit_2']}.\nПрофит второй части: `+{profit_pct:.2f}%` 🎉")
                        active_trade = None

                # Проверка для ШОРТ позиции
                elif active_trade['side'] == 'SHORT':
                    # Проверяем Стоп-Лосс
                    if current_price >= active_trade['stop_loss']:
                        loss_pct = ((active_trade['entry_price'] - active_trade['stop_loss']) / active_trade['entry_price']) * 100 * LEVERAGE
                        send_telegram(f"💀 *Стоп-Лосс по ШОРТУ на бумаге!*\nПозиция принудительно закрыта на {current_price}.\nУбыток с плечом {LEVERAGE}х: `{loss_pct:.2f}%`")
                        active_trade = None

                    # Проверяем Тейк-Профит 1
                    elif not active_trade['tp1_triggered'] and current_price <= active_trade['take_profit_1']:
                        active_trade['tp1_triggered'] = True
                        profit_pct = ((active_trade['entry_price'] - active_trade['take_profit_1']) / active_trade['entry_price']) * 100 * LEVERAGE
                        send_telegram(f"💰 *Тейк-Профит 1 (Шорт) выполнен!*\nЗакрыто 50% позиции на {active_trade['take_profit_1']}.\nПрофит части: `+{profit_pct:.2f}%` \nОстаток зафиксируется на {active_trade['take_profit_2']}")

                    # Проверяем Тейк-Профит 2
                    elif active_trade['tp1_triggered'] and current_price <= active_trade['take_profit_2']:
                        profit_pct = ((active_trade['entry_price'] - active_trade['take_profit_2']) / active_trade['entry_price']) * 100 * LEVERAGE
                        send_telegram(f"🟢 *Тейк-Профит 2 (Шорт) выполнен!*\nПозиция полностью закрыта на {active_trade['take_profit_2']}.\nПрофит второй части: `+{profit_pct:.2f}%` 🎉")
                        active_trade = None

                time.sleep(3)
                continue

            # 2. АНАЛИЗ СТАКАНА НА ВХОД (РАБОТАЕТ В ОБЕ СТОРОНЫ)
            # Считаем средний объем целых уровней для вычисления аномальных стен
            all_volumes = [v for p, v in asks[:5]] + [v for p, v in bids[:5]]
            avg_volume = sum(all_volumes) / len(all_volumes)
            wall_threshold = avg_volume * 3.5  # Стеной считаем превышение среднего объема в 3.5 раза

            # ПРОВЕРКА НА ЛОНГ (Ищем крупные плиты у Продавцов сверху)
            for wall_price, volume in asks[:3]:
                if volume > wall_threshold and (wall_price - current_price) <= 1.5:
                    entry = wall_price + 0.10  # Вход чуть выше пробитой целой стены
                    active_trade = {
                        "side": "LONG",
                        "entry_price": entry,
                        "stop_loss": entry - 0.40,
                        "take_profit_1": entry + 1.00,
                        "take_profit_2": entry + 2.50,
                        "tp1_triggered": False,
                        "wall_price": wall_price,
                        "wall_volume": volume
                    }
                    send_telegram(
                        f"🚀 *В уме открыт ЛОНГ на пробой целой стены!*\n\n"
                        f"• Текущая цена: `{current_price}`\n"
                        f"• Пробита стена: `{wall_price}$` (Объем: {volume:,.0f})\n"
                        f"• Цена входа в лонг: `{entry}`\n"
                        f"• Стоп-лосс: `{active_trade['stop_loss']}`\n"
                        f"• Тейк 1 (50%): `{active_trade['take_profit_1']}`\n"
                        f"• Тейк 2 (50%): `{active_trade['take_profit_2']}`"
                    )
                    break

            if active_trade: 
                time.sleep(3)
                continue

            # ПРОВЕРКА НА ШОРТ (Ищем крупные плиты у Покупателей снизу)
            for wall_price, volume in bids[:3]:
                if volume > wall_threshold and (current_price - wall_price) <= 1.5:
                    entry = wall_price - 0.10  # Вход чуть ниже пробитой целой стены поддержки
                    active_trade = {
                        "side": "SHORT",
                        "entry_price": entry,
                        "stop_loss": entry + 0.40,
                        "take_profit_1": entry - 1.00,
                        "take_profit_2": entry - 2.50,
                        "tp1_triggered": False,
                        "wall_price": wall_price,
                        "wall_volume": volume
                    }
                    send_telegram(
                        f"📉 *В уме открыт ШОРТ на пробой целой поддержки!*\n\n"
                        f"• Текущая цена: `{current_price}`\n"
                        f"• Пробита стена: `{wall_price}$` (Объем: {volume:,.0f})\n"
                        f"• Цена входа в шорт: `{entry}`\n"
                        f"• Стоп-лосс: `{active_trade['stop_loss']}`\n"
                        f"• Тейк 1 (50%): `{active_trade['take_profit_1']}`\n"
                        f"• Тейк 2 (50%): `{active_trade['take_profit_2']}`"
                    )
                    break

        except Exception as e:
            print(f"Ошибка в основном цикле бота: {e}")
            
