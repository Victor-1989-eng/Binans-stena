import os, requests
from flask import Flask
from binance.client import Client

app = Flask(__name__)

# Твой проверенный список (смешанный: USDC и USDT)
BASKET = ['BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'BNBUSDC', 'PAXGUSDT', 'XRPUSDC']
START_SL = 0.035 
FINAL_TP = 0.105 

active_trades = {}

def send_tg(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except: pass

@app.route('/')
def run_conveyor():
    global active_trades
    try:
        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        client = Client(api_key, api_secret)
        
        if not active_trades:
            send_tg("🧐 *АНАЛИЗ СМЕШАННОГО РЫНКА (USDC + GOLD)...*")
            analysis = []
            
            # Получаем тикеры и для USDC и для USDT фьючерсов
            all_tickers = client.futures_ticker()
            
            for symbol in BASKET:
                # Ищем данные для каждой монеты из нашего списка в общем ответе API
                ticker_data = next((item for item in all_tickers if item['symbol'] == symbol), None)
                
                if ticker_data:
                    analysis.append({
                        'symbol': symbol,
                        'change': float(ticker_data['priceChangePercent']),
                        'price': float(ticker_data['lastPrice'])
                    })
                else:
                    # Если вдруг PAXGUSDT не найден в фьючерсах, пробуем проверить спот
                    try:
                        spot_ticker = client.get_ticker(symbol=symbol)
                        analysis.append({
                            'symbol': symbol,
                            'change': float(spot_ticker['priceChangePercent']),
                            'price': float(spot_ticker['lastPrice'])
                        })
                    except:
                        send_tg(f"⚠️ Не нашел данные по {symbol}")

            if len(analysis) < 6:
                return f"Ошибка: собрано только {len(analysis)} из 6 монет.", 500

            # Сортируем: 3 сильных (LONG), 3 слабых (SHORT)
            analysis.sort(key=lambda x: x['change'], reverse=True)
            
            longs = analysis[:3]
            shorts = analysis[3:]

            for item in longs:
                open_paper_pos(item, 'LONG')
            for item in shorts:
                open_paper_pos(item, 'SHORT')

            msg = "🚀 *ЗАЛП 3х3 ВЫПОЛНЕН!*\n\n"
            msg += "📈 *LONG (Лидеры):*\n" + "\n".join([f"• {x['symbol']} (+{x['change']}%)" for x in longs])
            msg += "\n\n📉 *SHORT (Аутсайдеры):*\n" + "\n".join([f"• {x['symbol']} ({x['change']}%)" for x in shorts])
            msg += "\n\n💎 *Режим:* БУМАГА"
            send_tg(msg)
            
        return f"В работе: {list(active_trades.keys())}"
    
    except Exception as e:
        return f"Критическая ошибка: {e}", 500

def open_paper_pos(item, side):
    symbol = item['symbol']
    price = item['price']
    stop = round(price * (1 - START_SL) if side == 'LONG' else price * (1 + START_SL), 6)
    take = round(price * (1 + FINAL_TP) if side == 'LONG' else price * (1 - FINAL_TP), 6)
    active_trades[symbol] = {
        'side': side, 'entry': price, 'stop': stop, 'take': take, 'pnl_max': 0
    }

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
