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

# Simple CSS used on every page to give a dark theme and vertical buttons
STYLE = """
<style>
    body { background-color: #121212; color: #fff; font-family: Arial, sans-serif; }
    button { display: block; margin: 10px 0; background-color: #333; color: #fff;
             padding: 8px 12px; border: 1px solid #555; }
    input { background-color: #222; color: #fff; border: 1px solid #555; }
    table td { padding: 4px; }
    a { color: #80b3ff; }
</style>
"""


def _page(content: str) -> str:
    """Wrap ``content`` with HTML that applies ``STYLE``."""
    return f"<!doctype html><html><head>{STYLE}</head><body>{content}</body></html>"


def _get_trader():
    """Return a ``BybitOptionsTrader`` instance using saved credentials.

    The web menu originally required the user to create a trade before any
    other button worked because the ``trader`` object was only created when the
    trade form was submitted.  This helper tries to build the trader from the
    API key and secret stored in ``trade_config.json`` so actions like "Show" or
    "Cancel" can function immediately after launching the app.
    """

    global trader
    if trader is None:
        try:
            with open("trade_config.json", encoding="utf-8") as f:
                cfg = json.load(f)
            key = cfg.get("api_key", "")
            secret = cfg.get("api_secret", "")
            if key and secret:
                trader = optionstrader.BybitOptionsTrader(
                    key, secret, optionstrader.BASE_URL
                )
        except Exception:
            pass
    return trader


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
    return _page(
        """
        <h1>Options Trader</h1>
        <button onclick=\"location.href='/trade'\">Create Trade</button>
        <button onclick=\"location.href='/show'\">Show Open Orders/Positions</button>
        <button onclick=\"location.href='/cancel'\">Cancel All Orders/Positions</button>
        <button onclick=\"location.href='/edit'\">Edit Open Order</button>
        <button onclick=\"location.href='/export_recent'\">Export Trade History (7 days)</button>
        <button onclick=\"location.href='/export_all'\">Export All Trade History</button>
        <button onclick=\"location.href='/delivery_recent'\">Export Delivery History (7 days)</button>
        <button onclick=\"location.href='/delivery_all'\">Export All Delivery History</button>
        <button onclick=\"location.href='/reduce'\">Place Reduce-Only Exits</button>
        <button onclick=\"location.href='/demo_balance'\">Adjust Demo Balance</button>
        """
    )


@app.route("/demo_balance", methods=["GET", "POST"])
def demo_balance():
    path = "trade_config.json"
    if request.method == "POST":
        bal_str = request.form.get("balance", "").strip()
        try:
            new_bal = float(bal_str)
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
            cfg["demo_balance"] = new_bal
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            return _page(
                f"Demo balance set to {new_bal} USDT.<br><a href='/'>Back</a>"
            )
        except ValueError:
            return _page("Invalid value; balance unchanged.<br><a href='/'>Back</a>")
    else:
        bal = 0.0
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
                bal = cfg.get("demo_balance", 0.0)
        except Exception:
            pass
        html = render_template_string(
            """
            <h2>Adjust Demo Balance</h2>
            <form method='post'>
            New Balance: <input name='balance' value='{{bal}}'><br>
            <button type='submit'>Submit</button>
            </form>
            <a href='/'>Back</a>
            """,
            bal=bal,
        )
        return _page(html)


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
        symbol = form.get("symbol", "")
        if not symbol:
            symbol = optionstrader.build_option_symbol(
                form.get("base", ""),
                form.get("strike", ""),
                form.get("option_type", ""),
                form.get("expiry", ""),
                form.get("quote", "USDT"),
            )
        if qty <= 0 and risk_usd > 0:
            tick = optionstrader.fetch_option_ticker(symbol)
            price = float(tick.get("markPrice", 0) or 0)
            qty = optionstrader.compute_order_qty(risk_usd, price)
        cfg = {
            "symbol": symbol,
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
        return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")
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
    html = render_template_string(
        """
        <h2>Create Trade</h2>
        <p>Current Balance: {{balance}} USDT</p>
        <form method='post'>
        <table>
        <tr><td>Base</td><td><input name='base'></td></tr>
        <tr><td>Strike</td><td><input name='strike'></td></tr>
        <tr><td>Call/Put</td><td><input name='option_type'></td></tr>
        <tr><td>Expiry (D/M/YY)</td><td><input name='expiry'></td></tr>
        <tr><td>Quote</td><td><input name='quote' value='USDT'></td></tr>
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
    return _page(html)


@app.route("/show")
def show():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.show_open(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


@app.route("/cancel")
def cancel():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.cancel_all(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


@app.route("/edit", methods=["GET", "POST"])
def edit():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    if request.method == "POST":
        oid = request.form.get("order_id", "")
        price = request.form.get("price")
        qty = request.form.get("qty")
        price_val = float(price) if price else None
        qty_val = float(qty) if qty else None
        t.amend_order(oid, price_val, qty_val)
        return _page("Order amended.<br><a href='/'>Back</a>")
    html = render_template_string(
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
    return _page(html)


@app.route("/export_recent")
def export_recent():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.export_recent_trade_history(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


@app.route("/export_all")
def export_all():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.export_all_trade_history(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


@app.route("/delivery_recent")
def delivery_recent():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.export_recent_delivery_history(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


@app.route("/delivery_all")
def delivery_all():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.export_all_delivery_history(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


@app.route("/reduce")
def reduce():
    t = _get_trader()
    if t is None:
        return _page("No trader available. Place a trade first.<br><a href='/'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        optionstrader.set_profit_targets(t)
    return _page("<pre>" + buf.getvalue() + "</pre><a href='/'>Back</a>")


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

