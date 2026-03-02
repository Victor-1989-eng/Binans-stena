import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Секреты
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Список (сделаем его максимально коротким для теста связи)
TEST_ITEMS = ["T4_BAG", "T5_BAG", "T6_BAG", "T4_CAPE", "T5_CAPE", "T4_MAIN_SPEAR"]

current_deals = []
last_error = "Ошибок пока нет. Ждем первый цикл..."

def send_tg(text):
    if TOKEN and CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                          json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except: pass

def scan_logic():
    global current_deals, last_error
    while True:
        new_found = []
        try:
            # Запрашиваем по 1 группе для стабильности
            items_str = ",".join(TEST_ITEMS)
            url = f"https://www.albion-online-data.com/api/v2/stats/prices/{items_str}?locations=Caerleon,BlackMarket&qualities=1,2,3"
            
            print(f"DEBUG: Запрос к {url}", flush=True)
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                last_error = f"API вернул статус {response.status_code}. Возможно, временный бан IP."
            else:
                data = response.json()
                if not data:
                    last_error = "API прислал пустой список. Возможно, никто не обновлял цены сегодня."
                else:
                    last_error = "Данные успешно получены! Обработка..."
                    market_data = {}
                    for row in data:
                        k = (row['item_id'], row['quality'])
                        if k not in market_data: market_data[k] = {}
                        market_data[k][row['location']] = row['sell_price_min']

                    for (item_id, qual), locs in market_data.items():
                        p_city = locs.get('Caerleon', 0)
                        p_bm = locs.get('BlackMarket', 0)
                        if p_city > 0 and p_bm > 0:
                            profit = p_bm - p_city - (p_bm * 0.09)
                            if profit > 1000:
                                new_found.append({"name": item_id, "q": qual, "buy": p_city, "sell": p_bm, "profit": int(profit)})
                    
                    current_deals = sorted(new_found, key=lambda x: x['profit'], reverse=True)
                    last_error = f"Последнее обновление: {time.strftime('%H:%M:%S')}. Найдено сделок: {len(current_deals)}"

        except Exception as e:
            last_error = f"Критическая ошибка потока: {str(e)}"
            print(f"ERROR: {e}", flush=True)
        
        time.sleep(60)

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def index():
    # Выводим статус и ошибки прямо на страницу
    status_html = f"<div style='color: yellow; padding: 10px; border: 1px solid yellow;'>Статус: {last_error}</div>"
    
    if not current_deals:
        return f"<html><body style='background:#121212;color:white;'><h1>🏴‍☠️ Albion Scanner</h1>{status_html}</body></html>"
    
    table_rows = ""
    for d in current_deals:
        table_rows += f"<tr><td>{d['name']}</td><td>{d['q']}</td><td>{d['buy']:,}</td><td>{d['sell']:,}</td><td style='color:#00ff00;'>+{d['profit']:,}</td></tr>"

    return f"""
    <html>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:20px;">
        <h1>🏴‍☠️ Caerleon Flip Dashboard</h1>
        {status_html}
        <table border="1" style="width:100%; border-collapse:collapse; margin-top:20px;">
            <tr style="background:#333;"><th>Item</th><th>Quality</th><th>Buy Market</th><th>Sell BM</th><th>Profit</th></tr>
            {table_rows}
        </table>
    </body>
    </html>
    """

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
