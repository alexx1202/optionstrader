from flask import Flask, request, jsonify
import optionstrader

app = Flask(__name__)
CONFIG_PATH = 'trade_config.json'

@app.route('/webhook', methods=['POST'])
def webhook():
    cfg = optionstrader.load_trade_config(CONFIG_PATH)
    if not cfg.get('auto_trade'):
        return jsonify({'message': 'auto trade disabled'}), 200
    data = request.get_json(silent=True) or {}
    side = data.get('side', cfg.get('side', 'Buy'))
    symbol = cfg['symbol']
    risk_usd = float(cfg.get('risk_usd', 0))
    qty = cfg['quantity']
    price = 0.0
    if risk_usd:
        symbol, price = optionstrader.choose_symbol_by_risk(symbol, risk_usd, qty)
    if not price:
        tick = optionstrader.fetch_option_ticker(symbol)
        price = float(tick.get('markPrice', 0))
    if risk_usd and price:
        qty = round(risk_usd / price, 6)
    key, secret = optionstrader.get_api_credentials(cfg)
    trader = optionstrader.BybitOptionsTrader(key, secret, optionstrader.BASE_URL)
    trader.place_order(symbol, side, qty)
    return jsonify({'message': 'order sent', 'qty': qty, 'symbol': symbol}), 200

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
