import os, requests, time, threading
from flask import Flask

app = Flask(__name__)

# Секреты
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Список популярных предметов
TEST_ITEMS = "T4_BAG,T5_BAG,T6_BAG,T4_CAPE,T5_CAPE,T4_MAIN_SPEAR,T5_MAIN_SPEAR,T4_MAIN_BOW"

current_deals = []
last_error = "Ожидание данных..."

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
            url = f"https://www.albion-online-data.com/api/v2/stats/prices/{TEST_ITEMS}?locations=Caerleon,BlackMarket&qualities=1,2,3"
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if not data or not isinstance(data, list):
                    last_error = "API вернул пустой список или некорректный формат."
                else:
                    market_data = {}
                    for row in data:
                        # Проверка, что в строке есть все нужные ключи, чтобы не было ошибки 'location'
                        if all(k in row for k in ('item_id', 'quality', 'location', 'sell_price_min')):
                            k = (row['item_id'], row['quality'])
                            if k not in market_data: market_data[k] = {}
                            market_data[k][row['location']] = row['sell_price_min']

                    for (item_id, qual), locs in market_data.items():
                        p_city = locs.get('Caerleon', 0)
                        p_bm = locs.get('BlackMarket', 0)
                        
                        # Если обе цены больше нуля
                        if p_city > 0 and p_bm > 0:
                            profit = p_bm - p_city - (p_bm * 0.09)
                            if profit > 2000:
                                new_found.append({
                                    "name": item_id, 
                                    "q": qual, 
                                    "buy": p_city, 
                                    "sell": p_bm, 
                                    "profit": int(profit)
                                })
                                if profit > 50000:
                                    send_tg(f"💰 *Black Market Alert!*\n`{item_id}`\nProfit: {int(profit):,} silver")
                    
                    current_deals = sorted(new_found, key=lambda x: x['profit'], reverse=True)
                    last_error = f"Обновлено в {time.strftime('%H:%M:%S')}. Найдено сделок: {len(current_deals)}"
            else:
                last_error = f"Ошибка API: статус {response.status_code}"

        except Exception as e:
            last_error = f"Ошибка обработки: {str(e)}"
        
        time.sleep(60)

threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def index():
    status_style = "color: #00ff00;" if "Обновлено" in last_error else "color: yellow;"
    
    table_rows = ""
    for d in current_deals:
        table_rows += f"<tr><td>{d['name']}</td><td>{d['q']}</td><td>{d['buy']:,}</td><td>{d['sell']:,}</td><td style='color:#00ff00;'>+{d['profit']:,}</td></tr>"

    return f"""
    <html>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:20px;">
        <h2>🏴‍☠️ Albion Caerleon Scanner</h2>
        <div style="padding: 10px; border: 1px solid #444; margin-bottom: 20px; {status_style}">
            {last_error}
        </div>
        <table border="1" style="width:100%; border-collapse:collapse;">
            <tr style="background:#333;"><th>Предмет</th><th>Кач-во</th><th>Рынок</th><th>ЧР</th><th>Профит</th></tr>
            {table_rows if table_rows else '<tr><td colspan="5" style="text-align:center;">Пока нет выгодных сделок. Листай рынок в игре!</td></tr>'}
        </table>
    </body>
    </html>
    """

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
