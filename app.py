import os
import time
import threading
from datetime import datetime, timedelta
import requests
import ccxt
import pandas as pd
from flask import Flask

# Инициализация Flask для Render
app = Flask(__name__)

# === ПОЛУЧЕНИЕ НАСТРОЕК ИЗ ОКРУЖЕНИЯ RENDER ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВАШ_ТОКЕН")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "ВАШ_ID")

# === НАСТРОЙКИ СТРАТЕГИИ ===
SYMBOL = "SOL/USDC"   # Анализируемая пара
LEVERAGE = 38         # Плечо для расчета доходности
COMMISSION = 0.0005  # 0.05% комиссия Binance за рыночный ордер

def send_telegram(message):
    """Отправка уведомлений в Telegram"""
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def run_weekly_backtest():
    """Функция скачивания истории и анализа стратегии за неделю"""
    print("Запуск исторического анализатора...")
    send_telegram(f"⏳ *Запуск анализа за 7 дней...* \nСкачиваю поминутную историю {SYMBOL} с Binance. Это займет около 30 секунд...")
    
    exchange = ccxt.binance()
    # Вычисляем точку старта — ровно 7 дней назад
    since = exchange.parse8601((datetime.utcnow() - timedelta(days=7)).isoformat())
    all_candles = []

    # Цикл скачивания минутных свечей порциями
    while since < exchange.milliseconds():
        try:
            candles = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', since=since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 60000  
        except Exception as e:
            print(f"Ошибка загрузки истории: {e}")
            break

    if not all_candles:
        send_telegram("❌ Ошибка: Не удалось загрузить исторические данные с Binance.")
        return

    # Формируем датафрейм
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

    # Виртуальные переменные симулятора
    balance = 100.0  # Стартовый депозит $100
    active_trade = None
    total_trades = 0
    win_trades = 0
    loss_trades = 0
    be_trades = 0  # Сделки, закрытые в безубыток
    
    details_msg = "📋 *Хронология пробоев за неделю:*\n\n"

    # Прогон алгоритма по каждой минуте прошедшей недели
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        current_price = row['close']
        
        # 1. Если позиция открыта в уме — проверяем ее условия
        if active_trade:
            if active_trade['side'] == 'LONG':
                # Проверка Стоп-Лосса
                if row['low'] <= active_trade['stop_loss']:
                    if active_trade['tp1_triggered']:
                        be_trades += 1
                        details_msg += f"🔹 {row['datetime'].strftime('%d.%m %H:%M')} | ЛОНГ остаток закрыт в БУ ($0)\n"
                    else:
                        loss_pct = (((active_trade['stop_loss'] - active_trade['entry_price']) / active_trade['entry_price']) - (COMMISSION * 2)) * LEVERAGE
                        balance += balance * loss_pct
                        loss_trades += 1
                        details_msg += f"🔴 {row['datetime'].strftime('%d.%m %H:%M')} | ЛОНГ Стоп-лосс: `{loss_pct:.1f}%` (Цена: {active_trade['stop_loss']})\n"
                    active_trade = None
                    continue
                
                # Проверка Тейк-Профита 1
                if not active_trade['tp1_triggered'] and row['high'] >= active_trade['take_profit_1']:
                    active_trade['tp1_triggered'] = True
                    active_trade['stop_loss'] = active_trade['entry_price']  # Перенос в БУ
                    profit_pct1 = (((active_trade['take_profit_1'] - active_trade['entry_price']) / active_trade['entry_price']) - (COMMISSION * 2)) * LEVERAGE * 0.5
                    balance += balance * profit_pct1
                    
                # Проверка Тейк-Профита 2
                if active_trade and active_trade['tp1_triggered'] and row['high'] >= active_trade['take_profit_2']:
                    profit_pct2 = (((active_trade['take_profit_2'] - active_trade['entry_price']) / active_trade['entry_price']) - (COMMISSION * 2)) * LEVERAGE * 0.5
                    balance += balance * profit_pct2
                    win_trades += 1
                    details_msg += f"🟢 {row['datetime'].strftime('%d.%m %H:%M')} | ЛОНГ Тейк-2: Полный профит 🎉\n"
                    active_trade = None
                    continue

            elif active_trade['side'] == 'SHORT':
                # Проверка Стоп-Лосса
                if row['high'] >= active_trade['stop_loss']:
                    if active_trade['tp1_triggered']:
                        be_trades += 1
                        details_msg += f"🔹 {row['datetime'].strftime('%d.%m %H:%M')} | ШОРТ остаток закрыт в БУ ($0)\n"
                    else:
                        loss_pct = (((active_trade['entry_price'] - active_trade['stop_loss']) / active_trade['entry_price']) - (COMMISSION * 2)) * LEVERAGE
                        balance += balance * loss_pct
                        loss_trades += 1
                        details_msg += f"🔴 {row['datetime'].strftime('%d.%m %H:%M')} | ШОРТ Стоп-лосс: `{loss_pct:.1f}%` (Цена: {active_trade['stop_loss']})\n"
                    active_trade = None
                    continue
                
                # Проверка Тейк-Профита 1
                if not active_trade['tp1_triggered'] and row['low'] <= active_trade['take_profit_1']:
                    active_trade['tp1_triggered'] = True
                    active_trade['stop_loss'] = active_trade['entry_price']  # Перенос в БУ
                    profit_pct1 = (((active_trade['entry_price'] - active_trade['take_profit_1']) / active_trade['entry_price']) - (COMMISSION * 2)) * LEVERAGE * 0.5
                    balance += balance * profit_pct1
                    
                # Проверка Тейк-Профита 2
                if active_trade and active_trade['tp1_triggered'] and row['low'] <= active_trade['take_profit_2']:
                    profit_pct2 = (((active_trade['entry_price'] - active_trade['take_profit_2']) / active_trade['entry_price']) - (COMMISSION * 2)) * LEVERAGE * 0.5
                    balance += balance * profit_pct2
                    win_trades += 1
                    details_msg += f"🟢 {row['datetime'].strftime('%d.%m %H:%M')} | ШОРТ Тейк-2: Полный профит 🎉\n"
                    active_trade = None
                    continue
            continue

        # 2. Логика обнаружения пробоя целого уровня ($1) по дельте движения цены
        price_change = row['close'] - prev_row['close']
        
        # Симуляция пробоя ЛОНГ (Резкое пересечение круглого доллара снизу вверх)
        if price_change > 0.40:
            potential_wall = int(current_price)
            if prev_row['close'] < potential_wall <= row['close']:
                entry = potential_wall + 0.05
                active_trade = {
                    "side": "LONG", "entry_price": entry, "stop_loss": entry - 0.30,
                    "take_profit_1": entry + 0.90, "take_profit_2": entry + 3.00, "tp1_triggered": False
                }
                total_trades += 1

        # Симуляция пробоя ШОРТ (Резкое пересечение круглого доллара сверху вниз)
        elif price_change < -0.40:
            potential_wall = int(current_price) + 1
            if prev_row['close'] > potential_wall >= row['close']:
                entry = potential_wall - 0.05
                active_trade = {
                    "side": "SHORT", "entry_price": entry, "stop_loss": entry + 0.30,
                    "take_profit_1": entry - 0.90, "take_profit_2": entry - 3.00, "tp1_triggered": False
                }
                total_trades += 1

    # Формируем итоговую статистику
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
    profit_total_pct = (balance - 100.0)
    
    summary_msg = (
        f"📊 *ИТОГИ ТЕСТИРОВАНИЯ СТРАТЕГИИ ЗА 7 ДНЕЙ*\n"
        f"Пара: `{SYMBOL}` | Плечо: `{LEVERAGE}x`\n"
        f"Период: с {(datetime.now() - timedelta(days=7)).strftime('%d.%m')} по {datetime.now().strftime('%d.%m')}\n\n"
        f"• Всего сигналов на пробой: `{total_trades}`\n"
        f"• Закрыто по Тейк-2 (В плюс): `{win_trades}`\n"
        f"• Сработало Стопов (В убыток): `{loss_trades}`\n"
        f"• Закрыто в Безубыток (BU): `{be_trades}`\n"
        f"• Реальный Win Rate: `{win_rate:.1f}%`\n\n"
        f"💰 *ФИНАНСОВЫЙ РЕЗУЛЬТАТ:*\n"
        f"• Стартовый баланс: `$100`\n"
        f"• Конечный баланс: `${balance:.2f}`\n"
        f"• Чистая доходность: `+{profit_total_pct:.2f}%`"
    )
    
    # Отправляем сначала хронологию, если сделок было немного, либо сразу итог
    if total_trades > 0:
        # Разбиваем сообщение, если хронология слишком длинная для ТГ
        if len(details_msg) < 3000:
            send_telegram(details_msg)
    
    send_telegram(summary_msg)

@app.route('/')
def home():
    # Кнопка ручного запуска анализатора через браузер
    threading.Thread(target=run_weekly_backtest, daemon=True).start()
    return "Запущен глубокий исторический анализ за 7 дней. Отчет отправляется в ваш Telegram..."

if __name__ == "__main__":
    # Автоматический запуск бэктеста сразу при деплое сервера
    threading.Thread(target=run_weekly_backtest, daemon=True).start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
