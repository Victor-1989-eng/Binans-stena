import os
import requests
from flask import Flask
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

# --- ФУНКЦИЯ ДЛЯ БЕЗОПАСНОГО ПОЛУЧЕНИЯ КЛЮЧЕЙ ---
def get_binance_client():
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    
    # ПРОВЕРКА: Если ключей нет, мы узнаем об этом из логов
    if not api_key or not api_secret:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ключи не найдены в системе!")
        print(f"Проверка API_KEY: {'Найдено' if api_key else 'ПУСТО'}")
        print(f"Проверка API_SECRET: {'Найдено' if api_secret else 'ПУСТО'}")
        return None
        
    return Client(api_key, api_secret)

# Инициализируем переменные, которые нужны боту
SYMBOL = 'BNBUSDT'
LEVERAGE = 75
QTY_BNB = 0.24  
# ... (остальные настройки) ...

@app.route('/')
def run_bot():
    # Создаем клиента ПРЯМО ВНУТРИ функции при каждом запуске
    client = get_binance_client()
    
    if client is None:
        return "Ошибка: Ключи API не настроены в Render (Environment Variables)", 500

    try:
        # Узнаем наш IP для Binance
        my_ip = requests.get('https://api.ipify.org').text
        print(f"🌐 МОЙ IP СЕЙЧАС: {my_ip}")
        
        # Теперь делаем запрос к бирже
        pos = client.futures_position_information(symbol=SYMBOL)
        return f"Связь с Binance установлена! IP: {my_ip}. Сканирую стакан..."
        
    except Exception as e:
        print(f"❌ Ошибка Binance: {e}")
        return f"Binance отклонил запрос: {e}. Проверь IP: {my_ip}", 400
