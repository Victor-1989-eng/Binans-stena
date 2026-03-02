import os, requests, time, threading
from flask import Flask, render_template_string

app = Flask(__name__)

# Секреты из настроек Render
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Короткий список для теста (чтобы точно загрузилось быстро)
ITEM_GROUPS = [
    "T4_BAG,T5_BAG,T6_BAG,T4_CAPE,T5_CAPE,T6_CAPE",
    "T4_MAIN_SPEAR,T5_MAIN_SPEAR,T6_MAIN_SPEAR"
]

# Глобальный список сделок
current_deals = []

def send_tg(text):
    if TOKEN and CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except:
            pass

def scan_logic():
    global current_deals
    while True:
        new_found = []
        for group in ITEM_GROUPS:
            try:
                # Используем прямой IP или стабильный домен API
                url = f"https://www.albion-online-data.com/api/v2/stats/prices/{group}?locations=Caerleon,BlackMarket&qualities=1,2,3"
                response = requests.get(url, timeout=20)
                
                if response.status_code == 200:
                    data = response.json()
                    # Группируем цены
                    market_data = {}
                    for row in data:
                        k = (row['item_id'], row['quality'])
                        if k not in market_data: market_data[k] = {}
                        market_data[k][row['location']] = row['sell_price_min']

                    for (item_id, qual), locs in market_data.items():
                        p_city = locs.get('Caerleon', 0)
                        p_bm = locs.get('BlackMarket', 0)

                        if p_city > 0 and p_bm > 0:
                            # Чистая прибыль (налог 9% для запаса)
                            profit = p_bm - p_city - (p_bm * 0.09)
                            
                            if profit > 5000:
                                res = {"name": item_id, "q": qual, "buy": p_city, "sell": p_bm, "profit": int(profit)}
                                new_found.append(res)
                                
                                # Жирный профит в ТГ
                                if profit > 30000:
                                    send_tg(f"💰 *Albion Profit*\n`{item_id}`\nКупи: {p_city:,}\nЧР: {p_bm:,}\n*Earned {int(profit):,} silver*")
            except Exception as e:
                print(f"Ошибка потока: {e}")
            time.sleep(5) # Пауза между запросами
        
        # Сортируем и обновляем основной список
        current_deals = sorted(new_found, key=lambda x: x['profit'], reverse=True)
        time.sleep(60)

# Запускаем фоновый процесс
threading.Thread(target=scan_logic, daemon=True).start()

@app.route('/')
def index():
    # Простейший HTML без сложных функций, чтобы не падало
    if not current_deals:
        return "<html><body style='background:#121212;color:white;'><h1>Бот собирает данные... Обновите через 30 секунд.</h1></body></html>"
    
    table_rows = ""
    for d in current_deals:
        table_rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{d['q']}</td>
            <td>{d['buy']:,}</td>
            <td>{d['sell']:,}</td>
            <td style='color:#00ff00;'>+{d['profit']:,}</td>
        </tr>"""

    html = f"""
    <html>
    <body style="background:#121212; color:white; font-family:sans-serif; padding:20px;">
        <h1>🏴‍☠️ Caerleon Flip Dashboard</h1>
        <table border="1" style="width:100%; border-collapse:collapse;">
            <tr style="background:#333;">
                <th>Item</th><th>Quality</th><th>Buy Market</th><th>Sell BM</th><th>Profit</th>
            </tr>
            {table_rows}
        </table>
    </body>
    </html>
    """
    return html

if __name__ == '__main__':
    # Render требует привязки к порту из переменной окружения
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
