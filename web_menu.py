import os
import threading
import time
import contextlib
import io
import webbrowser
import json
from flask import Flask, request, render_template_string

import optionstrader


app = Flask(__name__)
trader = None


def _open_edge(url: str) -> None:
    """Open ``url`` in Microsoft Edge if available, else the default browser."""
    edge_paths = [
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    ]
    for path in edge_paths:
        if os.path.exists(path):
            webbrowser.register("edge", None, webbrowser.BackgroundBrowser(path))
            webbrowser.get("edge").open(url)
            return
    webbrowser.open(url)


@app.route("/")
def index():
    return render_template_string(
        """
        <h1>Options Trader</h1>
        <button onclick=\"location.href='/trade'\">Create Trade</button>
        <button onclick=\"location.href='/show'\">Show Open Orders/Positions</button>
        <button onclick=\"location.href='/cancel'\">Cancel All Orders/Positions</button>
        <button onclick=\"location.href='/edit'\">Edit Open Order</button>
        <button onclick=\"location.href='/export_recent'\">Export Trade History (7 days)</button>
        <button onclick=\"location.href='/export_all'\">Export All Trade History</button>
        <button onclick=\"location.href='/reduce'\">Place Reduce-Only Exits</button>
        """
    )


@app.route("/trade", methods=["GET", "POST"])
def trade():
    global trader
    if request.method == "POST":
        form = request.form
        api_key = form.get("api_key", "")
        api_secret = form.get("api_secret", "")
        trader = optionstrader.BybitOptionsTrader(api_key, api_secret, optionstrader.BASE_URL)
        balance = trader.get_wallet_balance()
        risk_percent = float(form.get("risk_percent", 0) or 0)
        risk_usd = balance * risk_percent / 100
        qty = float(form.get("quantity", 0) or 0)
        if qty <= 0 and risk_usd > 0:
            tick = optionstrader.fetch_option_ticker(form.get("symbol", ""))
            price = float(tick.get("markPrice", 0) or 0)
            qty = optionstrader.compute_order_qty(risk_usd, price)
        cfg = {
            "symbol": form.get("symbol", ""),
            "side": form.get("side", "Buy"),
            "quantity": qty,
            "limit_price": float(form["limit_price"]) if form.get("limit_price") else None,
            "risk_usd": risk_usd,
            "auto_trade": bool(form.get("auto_trade")),
            "api_key": api_key,
            "api_secret": api_secret,
            "telegram_token": form.get("telegram_token", ""),
            "telegram_chat_id": form.get("telegram_chat_id", ""),
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            optionstrader.execute_trade_from_cfg(cfg)
        return "<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>"
    # GET request: load defaults and show form
    def _load_defaults():
        try:
            with open("trade_config.json", encoding="utf-8") as f:
                cfg = json.load(f)
            return (
                cfg.get("api_key", ""),
                cfg.get("api_secret", ""),
                cfg.get("telegram_token", ""),
                cfg.get("telegram_chat_id", ""),
            )
        except Exception:
            return "", "", "", ""

    api_key, api_secret, telegram_token, telegram_chat_id = _load_defaults()
    balance = 0.0
    if api_key and api_secret:
        temp_trader = optionstrader.BybitOptionsTrader(api_key, api_secret, optionstrader.BASE_URL)
        balance = temp_trader.get_wallet_balance()
    return render_template_string(
        """
        <h2>Create Trade</h2>
        <p>Current Balance: {{balance}} USDT</p>
        <form method='post'>
        <table>
        <tr><td>Symbol</td><td><input name='symbol'></td></tr>
        <tr><td>Side</td><td><input name='side' value='Buy'></td></tr>
        <tr><td>Quantity</td><td><input name='quantity' value='0'></td></tr>
        <tr><td>Limit Price</td><td><input name='limit_price'></td></tr>
        <tr><td>Risk %</td><td><input name='risk_percent' value='0'></td></tr>
        <tr><td>Auto Trade</td><td><input type='checkbox' name='auto_trade'></td></tr>
        <tr><td>API Key</td><td><input name='api_key' value='{{api_key}}'></td></tr>
        <tr><td>API Secret</td><td><input name='api_secret' value='{{api_secret}}'></td></tr>
        <tr><td>Telegram Token</td><td><input name='telegram_token' value='{{telegram_token}}'></td></tr>
        <tr><td>Telegram Chat ID</td><td><input name='telegram_chat_id' value='{{telegram_chat_id}}'></td></tr>
        </table>
        <button type='submit'>Submit Trade</button>
        </form>
        <a href='/'>Back</a>
        """,
        balance=balance,
        api_key=api_key,
        api_secret=api_secret,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )


@app.route("/show")
def show():
    if trader is None:
        return "No trader available. Place a trade first.<br><a href='/'>Back</a>"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.show_open(trader)
    return "<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>"


@app.route("/cancel")
def cancel():
    if trader is None:
        return "No trader available. Place a trade first.<br><a href='/'>Back</a>"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.cancel_all(trader)
    return "<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>"


@app.route("/edit", methods=["GET", "POST"])
def edit():
    if trader is None:
        return "No trader available. Place a trade first.<br><a href='/'>Back</a>"
    if request.method == "POST":
        oid = request.form.get("order_id", "")
        price = request.form.get("price")
        qty = request.form.get("qty")
        price_val = float(price) if price else None
        qty_val = float(qty) if qty else None
        trader.amend_order(oid, price_val, qty_val)
        return "Order amended.<br><a href='/'>Back</a>"
    return render_template_string(
        """
        <h2>Edit Open Order</h2>
        <form method='post'>
        Order ID: <input name='order_id'><br>
        New Price: <input name='price'><br>
        New Qty: <input name='qty'><br>
        <button type='submit'>Submit</button>
        </form>
        <a href='/'>Back</a>
        """
    )


@app.route("/export_recent")
def export_recent():
    if trader is None:
        return "No trader available. Place a trade first.<br><a href='/'>Back</a>"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.export_recent_trade_history(trader)
    return "<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>"


@app.route("/export_all")
def export_all():
    if trader is None:
        return "No trader available. Place a trade first.<br><a href='/'>Back</a>"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.export_all_trade_history(trader)
    return "<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>"


@app.route("/reduce")
def reduce():
    if trader is None:
        return "No trader available. Place a trade first.<br><a href='/'>Back</a>"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.set_profit_targets(trader)
    return "<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>"


def start():
    """Start the web menu and open it in Microsoft Edge."""
    threading.Thread(target=lambda: app.run(port=5000, use_reloader=False), daemon=True).start()
    time.sleep(1)
    _open_edge("http://127.0.0.1:5000/")
    print("Web menu running on http://127.0.0.1:5000/")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    start()

