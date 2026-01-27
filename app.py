import os
        df = pd.DataFrame(bars, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        rsi 
            pos_data = {p['positionSide']: abs(float(p['positionAmt'])) for p in positions if p['symbol'] == clean_symbol}
            long_amt = pos_data.get('LONG', 0)
            short_amt = pos_data.get('SHORT', 0)
            curr_p = exchange.fetch_ticker(SYMBOL)['last']

            # 2. ЕСЛИ ПОЗИЦИЙ НЕТ — ЧИСТИМ ОРДЕРА И СТАРТУЕМ
            if long_amt == 0 and short_amt == 0:
                # Отменяем все зависшие лимитки перед новым циклом
                exchange.cancel_all_orders(SYMBOL)
                
                side, reason = get_market_sentiment()
                raw_qty = (TRADE_AMOUNT_CURRENCY * LEVERAGE) / curr_p
                qty = float(exchange.amount_to_precision(SYMBOL, raw_qty))
                if qty < 0.01: qty = 0.01
                
                if side == "SHORT":
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_p, params={'positionSide': 'SHORT'})
                    send_tg(f"📉 *Новый цикл: SHORT* по `{curr_p}`. Тейк: `{tp_p}`")
                else:
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_p = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_p, params={'positionSide': 'LONG'})
                    send_tg(f"📈 *Новый цикл: LONG* по `{curr_p}`. Тейк: `{tp_p}`")

            # 3. ЛОГИКА ЗАМКА (ХЕДЖ)
            if short_amt > 0 and long_amt == 0:
                pos_info = [p for p in positions if p['symbol'] == clean_symbol and p['positionSide'] == 'SHORT'][0]
                entry_s = float(pos_info.get('entryPrice', 0))
                if entry_s > 0 and curr_p >= (entry_s + STEP):
                    qty = float(exchange.amount_to_precision(SYMBOL, short_amt))
                    exchange.create_order(SYMBOL, 'market', 'buy', qty, params={'positionSide': 'LONG'})
                    tp_l = float(exchange.price_to_precision(SYMBOL, curr_p + PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'sell', qty, tp_l, params={'positionSide': 'LONG'})
                    send_tg(f"🔒 *ЗАМОК: Добавлен Лонг* по `{curr_p}`")

            if long_amt > 0 and short_amt == 0:
                pos_info = [p for p in positions if p['symbol'] == clean_symbol and p['positionSide'] == 'LONG'][0]
                entry_l = float(pos_info.get('entryPrice', 0))
                if entry_l > 0 and curr_p <= (entry_l - STEP):
                    qty = float(exchange.amount_to_precision(SYMBOL, long_amt))
                    exchange.create_order(SYMBOL, 'market', 'sell', qty, params={'positionSide': 'SHORT'})
                    tp_s = float(exchange.price_to_precision(SYMBOL, curr_p - PROFIT_GOAL))
                    exchange.create_order(SYMBOL, 'limit', 'buy', qty, tp_s, params={'positionSide': 'SHORT'})
                    send_tg(f"🔒 *ЗАМОК: Добавлен Шорт* по `{curr_p}`")

        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg:
                time.sleep(60)
            else:
                send_tg(f"⚠️ *Ошибка:* `{err_msg[:50]}`")
                time.sleep(20)
        
        time.sleep(30) # Оптимальный баланс между скоростью и лимитами

threading.Thread(target=bot_worker, daemon=True).start()

@app.route('/')
def health(): return "Bot Active", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
