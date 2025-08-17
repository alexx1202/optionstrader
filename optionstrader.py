#!/usr/bin/env python3
"""Trade execution and Greek exposure script.

This tool reads a single JSON configuration specifying the option symbol,
side, quantity and optional limit price. It places entry and exit orders via
the Bybit API, logs all trade details and outputs option Greeks (delta, gamma,
vega and theta) with timestamps, balance and ticker data. Separate log files
are created for general runtime information and trade-specific details.
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
import hmac
import hashlib

import requests
from tabulate import tabulate
import csv
from decimal import Decimal, ROUND_HALF_UP

# === Configuration ===
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api-demo.bybit.com"
RECV_WINDOW = "5000"
SUB_ACCOUNT_NAME = ""
MIN_BALANCE_THRESHOLD = 10.0
DEMO_BALANCE = float(os.getenv("DEMO_BALANCE", 0.0))

# === File setup ===
script_dir = os.path.dirname(os.path.abspath(__file__))
# Log runtime details to a clear, descriptive file name
log_file = os.path.join(script_dir, 'optionstrader.log')
# Store trade-specific information separately
output_file = os.path.join(script_dir, 'trade_output.txt')

# === Logging configuration ===
logger = logging.getLogger('main')
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler(log_file, mode='w')
ch = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
fh.setFormatter(fmt)
ch.setFormatter(fmt)
logger.addHandler(fh)
logger.addHandler(ch)
logger.propagate = False
logger.info("Starting optionstrader.py; logs to %s, output to %s", log_file, output_file)

def print_and_write(lines):
    """Print to console and write to output file."""
    with open(output_file, 'w', encoding='utf-8') as out:
        for line in lines:
            print(line)
            out.write(line + "\n")

def load_trade_config(path):
    """Load and validate trade configuration from a JSON file.

    The function first attempts to read ``path`` as provided. If that fails and
    ``path`` is a relative location, it falls back to searching for the file in
    the directory of this script. This allows the script to be relocated without
    requiring absolute paths in helper scripts like ``run.bat``.
    """
    path = os.path.expanduser(os.path.expandvars(path))
    candidate = path
    if not os.path.isabs(candidate) and not os.path.exists(candidate):
        candidate = os.path.join(script_dir, candidate)
    if not os.path.exists(candidate):
        raise FileNotFoundError(f"Trade config file not found: {path}")
    with open(candidate, encoding='utf-8') as f:
        cfg = json.load(f)
    cfg.setdefault("auto_trade", False)
    cfg.setdefault("risk_usd", 0)
    for field in ("symbol", "side", "quantity"):
        if field not in cfg or cfg[field] in (None, ""):
            raise ValueError(f"Missing required field in config: {field}")
    return cfg

def get_api_credentials(cfg):
    """Return API credentials from environment variables or config."""
    key = os.getenv("BYBIT_API_KEY") or cfg.get("api_key", "")
    secret = os.getenv("BYBIT_API_SECRET") or cfg.get("api_secret", "")
    return key, secret

def get_telegram_credentials(cfg):
    """Return Telegram bot token and chat id from env or config."""
    token = os.getenv("TELEGRAM_TOKEN") or cfg.get("telegram_token", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id", "")
    return token, chat_id

def send_telegram_document(path, token, chat_id, caption=None):
    """Send a file to a Telegram chat using the Bot API."""
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    try:
        with open(path, "rb") as doc:
            requests.post(url, data=data, files={"document": doc}, timeout=10)
        logger.info("Sent %s to Telegram chat %s", path, chat_id)
    except Exception as exc:
        logger.error("Failed to send Telegram document: %s", exc)

# === Greek fetching via public market endpoint ===
def fetch_option_ticker(symbol, base_url=BASE_URL):
    """Return ticker data for a given option symbol."""
    endpoint = "/v5/market/tickers"
    params = {"category": "option", "symbol": symbol}
    qs = urlencode(params)
    url = f"{base_url}{endpoint}?{qs}"
    logger.debug("Fetching ticker: %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("Ticker response: %s", data)
    if data.get("retCode") != 0:
        raise RuntimeError(f"API Error {data['retCode']}: {data.get('retMsg')}")
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"No ticker data for symbol: {symbol}")
    return lst[0]

def fetch_option_instruments(base_coin="BTC", expiry=None, option_type=None, base_url=BASE_URL):
    """Return a list of option symbols for the given filters."""
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option", "baseCoin": base_coin}
    if expiry:
        params["expDate"] = expiry
    if option_type:
        opt = option_type
        if opt.upper() in ("P", "PUT"):
            opt = "Put"
        elif opt.upper() in ("C", "CALL"):
            opt = "Call"
        params["optionType"] = opt

    instruments = []
    cursor = None
    while True:
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        if cursor:
            qs += f"&cursor={cursor}"
        url = f"{base_url}{endpoint}?{qs}"
        logger.debug("Fetching instruments: %s", url)
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("Instruments response: %s", data)
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"API Error {data['retCode']}: {data.get('retMsg')}"
            )
        instruments.extend(data.get("result", {}).get("list", []))
        cursor = data.get("result", {}).get("nextPageCursor")
        if not cursor:
            break
    return instruments


_tick_size_cache = {}


def get_tick_size(symbol, base_url=BASE_URL):
    """Return the minimum price increment for ``symbol``."""
    if symbol in _tick_size_cache:
        return _tick_size_cache[symbol]
    endpoint = "/v5/market/instruments-info"
    params = {"category": "option", "symbol": symbol}
    qs = urlencode(params)
    url = f"{base_url}{endpoint}?{qs}"
    logger.debug("Fetching tick size: %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("Tick size response: %s", data)
    if data.get("retCode") != 0:
        raise RuntimeError(
            f"API Error {data['retCode']}: {data.get('retMsg')}"
        )
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"No instrument data for symbol: {symbol}")
    tick = float(lst[0].get("lotSizeFilter", {}).get("tickSize", 0))
    _tick_size_cache[symbol] = tick
    return tick


def round_to_tick(price, symbol):
    """Round ``price`` to the nearest tick for ``symbol``."""
    tick = get_tick_size(symbol)
    if not tick:
        raise ValueError(f"Tick size for {symbol} is zero or missing")
    price_dec = Decimal(str(price))
    tick_dec = Decimal(str(tick))
    rounded = (price_dec / tick_dec).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick_dec
    return float(rounded)

MIN_ORDER_QTY = 0.01

def _parse_expiry(token):
    """Return datetime for an expiry token like '7JUN25' or '07JUN25'."""
    tok = token.upper()
    if len(tok) == 6:  # single-digit day
        tok = '0' + tok
    try:
        return datetime.strptime(tok, "%d%b%y")
    except ValueError:
        return None

def build_option_symbol(base, strike, option_type, expiry, quote):
    """Return a Bybit option symbol built from individual parts.

    Parameters are case-insensitive. ``expiry`` should be given as ``day/month/year``
    (for example ``7/6/25`` for 7 June 2025). The function converts the pieces
    into the ``BASE-DAYMONYY-STRIKE-C/P-QUOTE`` format required by Bybit.
    """
    base = str(base or "").upper()
    quote = str(quote or "").upper()
    opt = str(option_type or "").upper()
    if opt:
        opt = opt[0]  # Use only first letter: 'C' or 'P'
    # Normalize strike to an integer-like string
    try:
        strike_val = int(float(strike))
    except (ValueError, TypeError):
        strike_val = 0
    strike_str = str(strike_val)

    # Parse expiry in day/month/year form
    exp = str(expiry or "").replace("-", "/").strip()
    try:
        d, m, y = exp.split("/")
        day = int(d)
        month = int(m)
        year = int(y)
    except ValueError:
        day = month = 1
        year = 1970
    if year < 100:
        year += 2000
    dt = datetime(year, month, day)
    month_token = dt.strftime("%b").upper()
    expiry_token = f"{day}{month_token}{str(year)[2:]}"

    return f"{base}-{expiry_token}-{strike_str}-{opt}-{quote}"

def compute_order_qty(risk_usd, price, min_qty=MIN_ORDER_QTY):
    """Return the order quantity rounded to the exchange increment."""
    if not risk_usd or not price:
        return 0.0
    qty = risk_usd / price
    if qty < min_qty:
        qty = min_qty
    # round to nearest allowed increment (0.01)
    steps = round(qty / min_qty)
    qty = steps * min_qty
    return round(qty, 2)

def choose_symbol_by_risk(base_symbol, risk_usd, qty, base_url=BASE_URL):
    """Return the option symbol from the earliest expiry whose mark price is closest to risk/qty."""
    if not risk_usd or not qty:
        return base_symbol, 0.0
    parts = base_symbol.split('-')
    if len(parts) < 5:
        return base_symbol, 0.0
    base_coin, expiry_token, _strike, opt_type, _quote = parts
    instruments = fetch_option_instruments(base_coin, option_type=opt_type, base_url=base_url)
    if not instruments:
        return base_symbol, 0.0

    # API filtering by option type is not always reliable; enforce it here
    instruments = [i for i in instruments
                   if i.get('symbol', '').split('-')[3].upper() == opt_type.upper()]
    if not instruments:
        return base_symbol, 0.0

    def expiry_from_symbol(sym):
        p = sym.split('-')
        if len(p) > 1:
            dt = _parse_expiry(p[1])
            if dt:
                return dt
        return datetime.max

    desired_expiry = _parse_expiry(expiry_token)
    if desired_expiry:
        same_expiry = [i for i in instruments if expiry_from_symbol(i.get('symbol', '')) == desired_expiry]
        if same_expiry:
            instruments = same_expiry

    instruments.sort(key=lambda inst: expiry_from_symbol(inst.get('symbol', '')))
    first_expiry = expiry_from_symbol(instruments[0].get('symbol', ''))
    filtered = [inst for inst in instruments if expiry_from_symbol(inst.get('symbol', '')) == first_expiry]
    target = risk_usd / qty
    best_sym = base_symbol
    best_price = 0.0
    best_diff = float('inf')
    for inst in filtered:
        sym = inst.get('symbol')
        if not sym:
            continue
        tick = fetch_option_ticker(sym, base_url)
        price = float(tick.get('markPrice', 0))
        diff = abs(price - target)
        if diff < best_diff:
            best_diff = diff
            best_sym = sym
            best_price = price
    return best_sym, best_price

# === Options trading ===
class ApiException(Exception):
    """Custom exception for Bybit API errors."""


class BybitOptionsTrader:
    """Simple wrapper around Bybit's options REST API."""

    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    def _generate_signature(self, timestamp, body_or_query):
        """Return HMAC SHA256 signature for a request."""
        payload = f"{timestamp}{self.api_key}{RECV_WINDOW}{body_or_query}"
        return hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def _send_request(self, method, path, body=None, query=""):
        """Send an authenticated request to the API and return parsed JSON."""
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + query
        ts = str(int(time.time() * 1000))
        body_str = json.dumps(body, separators=(',', ':')) if body else ''
        to_sign = query if method=='GET' else body_str
        sig = self._generate_signature(ts, to_sign)
        headers = {
            "Content-Type":"application/json",
            "X-BAPI-API-KEY":self.api_key,
            "X-BAPI-SIGN":sig,
            "X-BAPI-TIMESTAMP":ts,
            "X-BAPI-RECV-WINDOW":RECV_WINDOW,
            "X-BAPI-SIGN-TYPE":"2"
        }
        if SUB_ACCOUNT_NAME:
            headers["X-BAPI-SUB-ACCOUNT-NAME"] = SUB_ACCOUNT_NAME
        resp = requests.request(method, url, headers=headers, data=body_str, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            raise ApiException(f"API Error {data['retCode']}: {data.get('retMsg')}")
        return data

    def get_wallet_balance(self, coin="USDT"):
        """Return the wallet balance for the specified coin."""
        try:
            data = self._send_request(
                "GET", "/v5/account/wallet-balance", "", "accountType=UNIFIED"
            )
            for entry in data.get("result", {}).get("list", []):
                for c in entry.get("coin", []):
                    if c.get("coin") == coin:
                        return float(c.get("walletBalance", 0))
        except Exception as exc:
            logger.error("Failed to retrieve wallet balance: %s", exc)
        return 0.0

    def place_order(self, symbol, side, qty, price=None, tif="GTC", is_exit=False):
        """Create an order and return Bybit's result structure."""
        if price is not None:
            price = round_to_tick(price, symbol)
        body = {"category": "option", "symbol": symbol, "side": side,
                "orderType": "Limit" if price is not None else "Market",
                "qty": str(qty), "timeInForce": tif,
                "orderLinkId": uuid.uuid4().hex}
        if price is not None:
            body["price"] = str(price)
        if is_exit:
            body["reduceOnly"] = True
        resp = self._send_request("POST", "/v5/order/create", body)
        order_type = 'Exit' if is_exit else 'Entry'
        logger.info("%s order placed: %s", order_type, resp.get('result', {}))
        return resp.get("result",{})

    def get_trade_history(self, symbol, order_id, limit=20):
        """Return execution records for a given order."""
        q = f"category=option&symbol={symbol}&limit={limit}"
        data = self._send_request("GET","/v5/execution/list","",q)
        trades = data.get("result",{}).get("list",[])
        return [t for t in trades if t.get("orderId")==order_id]

    def get_order_detail(self, symbol, order_id):
        """Return realtime order info for the given order_id."""
        q = f"category=option&symbol={symbol}&orderId={order_id}"
        data = self._send_request("GET", "/v5/order/realtime", "", q)
        return data.get("result", {}).get("list", [])

    def wait_for_order_fill(self, symbol, order_id, timeout=60, poll_interval=2):
        """Wait until an order is filled and return its trades.

        This polls :func:`get_trade_history` and :func:`get_order_detail` until
        executions are found or the timeout elapses. It is useful for limit
        orders that may not fill immediately.  The ``timeout`` and
        ``poll_interval`` values are in seconds.
        """
        start = time.time()
        while time.time() - start < timeout:
            trades = self.get_trade_history(symbol, order_id)
            if trades:
                return trades
            details = self.get_order_detail(symbol, order_id)
            status = details[0].get("orderStatus") if details else ""
            if status in {"Filled", "PartiallyFilled"}:
                trades = self.get_trade_history(symbol, order_id)
                if trades:
                    return trades
            time.sleep(poll_interval)
        return []

    def get_open_orders(self, symbol=None):
        """Return a list of open option orders."""
        q = "category=option"
        if symbol:
            q += f"&symbol={symbol}"
        data = self._send_request("GET", "/v5/order/realtime", "", q)
        orders = data.get("result", {}).get("list", [])
        return [o for o in orders if o.get("orderStatus") not in {"Filled", "Cancelled"}]

    def get_positions(self, symbol=None):
        """Return a list of current option positions."""
        q = "category=option"
        if symbol:
            q += f"&symbol={symbol}"
        data = self._send_request("GET", "/v5/position/list", "", q)
        return data.get("result", {}).get("list", [])

    def cancel_all_orders(self):
        """Cancel all open option orders."""
        body = {"category": "option"}
        try:
            self._send_request("POST", "/v5/order/cancel-all", body)
        except ApiException as exc:
            # Bybit returns error 110008 when there are no active orders. This
            # should not abort the cancel-all workflow, so we simply log and
            # continue when that specific error occurs.
            if "110008" in str(exc):
                logger.info("No open orders to cancel")
            else:
                raise

    def close_position(self, symbol, side, qty):
        """Close a position using a market order."""
        self.place_order(symbol, side, qty, None, "GTC", True)

    def amend_order(self, order_id, price=None, qty=None):
        """Amend price and/or quantity of an open order."""
        body = {"category": "option", "orderId": order_id}
        if price is not None:
            body["price"] = str(price)
        if qty is not None:
            body["qty"] = str(qty)
        self._send_request("POST", "/v5/order/amend", body)

    def list_trade_history(self, start_time, end_time=None, limit=50):
        """Return execution records within a time range.

        The Bybit API allows querying at most seven days of history per
        request. To fetch a longer span, this method slices the desired
        period into week-long chunks and stitches the results together. The
        caller can therefore request months of data (or even everything by
        passing ``start_time=0``) without worrying about the 7‑day rule.
        """

        if end_time is None:
            end_time = int(time.time() * 1000)

        max_range = 7 * 24 * 60 * 60 * 1000  # seven days in milliseconds
        trades = []
        current_end = end_time
        empty_runs = 0

        while current_end > start_time and empty_runs < 3:
            current_start = max(start_time, current_end - max_range)
            q = f"category=option&startTime={current_start}&endTime={current_end}"
            if limit:
                q += f"&limit={limit}"

            cursor = None
            chunk = []
            while True:
                query = q
                if cursor:
                    query += f"&cursor={cursor}"
                data = self._send_request("GET", "/v5/execution/list", "", query)
                chunk.extend(data.get("result", {}).get("list", []))
                cursor = data.get("result", {}).get("nextPageCursor")
                if not cursor:
                    break

            if chunk:
                trades.extend(chunk)
                empty_runs = 0
            else:
                empty_runs += 1

            current_end = current_start - 1

        return trades

    def list_delivery_history(self, start_time, end_time=None, limit=50):
        """Return delivery records within a time range.

        Bybit restricts delivery queries to seven-day windows. This method
        automatically slices the requested period into week-long chunks and
        combines the results so callers can fetch months of history by passing
        ``start_time=0``.
        """

        if end_time is None:
            end_time = int(time.time() * 1000)

        max_range = 7 * 24 * 60 * 60 * 1000  # seven days in milliseconds
        deliveries = []
        current_end = end_time
        empty_runs = 0

        while current_end > start_time and empty_runs < 3:
            current_start = max(start_time, current_end - max_range)
            q = f"category=option&startTime={current_start}&endTime={current_end}"
            if limit:
                q += f"&limit={limit}"

            cursor = None
            chunk = []
            while True:
                query = q
                if cursor:
                    query += f"&cursor={cursor}"
                data = self._send_request(
                    "GET", "/v5/asset/delivery-record", "", query
                )
                chunk.extend(data.get("result", {}).get("list", []))
                cursor = data.get("result", {}).get("nextPageCursor")
                if not cursor:
                    break

            if chunk:
                deliveries.extend(chunk)
                empty_runs = 0
            else:
                empty_runs += 1

            current_end = current_start - 1

        return deliveries

    def place_and_log(self, symbol, side, qty, entry_price, tif):
        """Place entry and exit orders and log the resulting trades."""
        # Place entry
        result = self.place_order(symbol, side, qty, entry_price, tif, False)
        oid = result.get("orderId")
        # Give Bybit some time to generate execution records
        trades = []
        for _ in range(5):
            time.sleep(2)
            trades = self.get_trade_history(symbol, oid)
            if trades:
                break
        if not trades:
            trades = self.wait_for_order_fill(symbol, oid)

        # Always fetch order details as fallback for avgPrice
        order_info = self.get_order_detail(symbol, oid)
        order = order_info[0] if order_info else {}
        # Log trades to file
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        trade_log = os.path.join(script_dir, f"option_trade_log_{ts}.log")
        with open(trade_log, 'w', encoding='utf-8') as f:
            for t in trades:
                f.write(json.dumps(t, indent=2) + "\n")
            if order:
                f.write(json.dumps({"order": order}, indent=2) + "\n")
        logger.info("Trade log saved to %s", trade_log)
        if not trades:
            logger.info("Order not filled; skipping exit order")
            return trades, trade_log

        # Determine entry price for exit calculation
        if not entry_price:
            entry = next((t for t in trades if t.get('side', '').lower() == side.lower()), None)
            if entry and entry.get('execPrice'):
                entry_price = float(entry.get('execPrice'))
            elif order and order.get('avgPrice'):
                entry_price = float(order.get('avgPrice'))
            elif order and order.get('price'):
                entry_price = float(order.get('price'))
            else:
                logger.warning("No entry trade to infer price; skipping exit order")
                return trades, trade_log
        # Calculate target: e.g. 3x entry_price and round to tick size
        target = round_to_tick(entry_price * 3, symbol)
        exit_side = "Sell" if side.lower() == "buy" else "Buy"
        self.place_order(symbol, exit_side, qty, target, tif, True)
        return trades, trade_log

def execute_trade_from_cfg(cfg):
    """Execute trade using a configuration dictionary ``cfg``."""
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    symbol, side, qty = cfg["symbol"], cfg["side"], cfg["quantity"]
    entry_price = cfg.get("limit_price")
    key, secret = get_api_credentials(cfg)
    if not key or not secret:
        raise RuntimeError(
            "API credentials not provided. Set BYBIT_API_KEY and BYBIT_API_SECRET "
            "environment variables or include api_key/api_secret in the config." 
        )
    trader = BybitOptionsTrader(key, secret, BASE_URL)
    balance = trader.get_wallet_balance()
    lines = [f"Timestamp: {ts}", f"Balance: {balance:.4f} USDT"]
    if balance < MIN_BALANCE_THRESHOLD:
        lines.append("⚠️ Insufficient balance => abort")
        print_and_write(lines)
        return
    order_desc = 'Market' if not entry_price else entry_price
    lines.append(f"Placing {side} {qty} {symbol} @ {order_desc}")
    _trades, trade_log = trader.place_and_log(symbol, side, qty, entry_price, "GTC")
    lines.append(f"Trade log: {trade_log}")
    tick = fetch_option_ticker(symbol)
    lines.append("\nTicker Data:")
    for k, v in sorted(tick.items()):
        lines.append(f"  {k}: {v}")
    greeks = {k: float(tick[k]) for k in ('delta','gamma','vega','theta') if k in tick}
    mult = 1 if side.lower() == 'buy' else -1
    headers = ['Greek', 'Per-Contract', 'Qty', 'Exposure']
    rows = []
    for name, per in greeks.items():
        exp = per * qty * mult
        rows.append([name.capitalize(), f"{per:.8f}", str(qty), f"{exp:.8f}"])
    lines.append("\nGreek Exposures:")
    table = tabulate(rows, headers=headers, tablefmt="plain")
    lines.extend(table.splitlines())
    print_and_write(lines)
    token, chat_id = get_telegram_credentials(cfg)
    send_telegram_document(trade_log, token, chat_id, caption=f"{side} {qty} {symbol}")


def execute_trade(order_file):
    """Execute trade specified by ``order_file`` and print greek exposures."""
    cfg = load_trade_config(order_file)
    execute_trade_from_cfg(cfg)


def show_open(trader):
    """Display open option orders and positions."""
    orders = trader.get_open_orders()
    positions = trader.get_positions()
    print("\nOpen Orders:")
    if not orders:
        print("  None")
    for o in orders:
        print(json.dumps(o, indent=2))
    print("\nOpen Positions:")
    if not positions:
        print("  None")
    for p in positions:
        print(json.dumps(p, indent=2))


def cancel_all(trader):
    """Cancel all open orders and close all positions."""
    trader.cancel_all_orders()
    for pos in trader.get_positions():
        qty = abs(float(pos.get("size", 0)))
        if qty:
            side = "Sell" if pos.get("side", "Buy").lower() == "buy" else "Buy"
            trader.close_position(pos.get("symbol"), side, qty)
    print("All orders cancelled and positions closed.")


def edit_open_order(trader):
    """Prompt for an order id and new values then amend the order."""
    oid = input("Enter order ID to amend: ").strip()
    price = input("New price (blank to keep): ").strip()
    qty = input("New qty (blank to keep): ").strip()
    price_val = float(price) if price else None
    qty_val = float(qty) if qty else None
    trader.amend_order(oid, price_val, qty_val)
    print("Order amended.")


def _write_trade_history_csv(trader, trades, filename):
    """Write ``trades`` to ``filename`` adding fees, PnL and balance."""
    final_balance = trader.get_wallet_balance("USDT")
    path = os.path.join(script_dir, filename)
    base_fields = sorted(trades[0].keys())
    extra = ["netFee", "netPnl", "localTime", "balance"]

    with open(path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=base_fields + extra)
        writer.writeheader()

        # Sort trades chronologically so balances can be calculated in order
        trades_sorted = sorted(trades, key=lambda x: int(x.get("execTime", 0)))

        processed = []
        for t in trades_sorted:
            row = dict(t)
            # net fees
            try:
                row["netFee"] = float(t.get("execFee", 0))
            except (TypeError, ValueError):
                row["netFee"] = 0.0
            # net pnl
            pnl = None
            for pf in ("closedPnl", "realisedPnl", "execPnl"):
                if pf in t and t[pf] not in (None, ""):
                    try:
                        pnl = float(t[pf])
                        break
                    except (TypeError, ValueError):
                        pass
            if pnl is None:
                # fallback: derive from exec value and fee
                try:
                    value = float(t.get("execValue", 0) or 0)
                    side = str(t.get("side", "")).lower()
                    sign = 1 if side == "sell" else -1
                    fee = float(t.get("execFee", 0) or 0)
                    pnl = sign * value - fee
                except Exception:
                    pnl = 0.0
            row["netPnl"] = pnl
            processed.append((row, pnl))

        starting_balance = final_balance - sum(p for _, p in processed)
        running_balance = starting_balance

        for row, pnl in processed:
            # time conversion
            ts = None
            for tf in ("execTime", "createdTime", "updatedTime", "tradeTime"):
                if tf in row and row[tf] not in (None, ""):
                    ts = row[tf]
                    break
            if ts is not None:
                try:
                    ts_int = int(ts)
                    dt = datetime.fromtimestamp(ts_int / 1000, timezone.utc)
                    dt = dt.astimezone(ZoneInfo("Australia/Brisbane"))
                    row["localTime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    row["localTime"] = ""
            else:
                row["localTime"] = ""

            running_balance += pnl
            row["balance"] = running_balance
            writer.writerow(row)
    print(f"Saved {len(trades)} trades to {path}")


def export_recent_trade_history(trader, days=7):
    """Save trades from the last ``days`` days to a CSV file with extra info."""
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    trades = trader.list_trade_history(start, end)
    if not trades:
        print("No recent trades found.")
        return
    _write_trade_history_csv(trader, trades, "recent_trades.csv")


def export_all_trade_history(trader):
    """Save all available trades up to now to a CSV file."""
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = 0
    try:
        trades = trader.list_trade_history(start, end)
    except ApiException as exc:
        print(f"Failed to retrieve trade history: {exc}")
        return
    if not trades:
        print("No trades found.")
        return
    _write_trade_history_csv(trader, trades, "all_trades.csv")


def export_recent_delivery_history(trader, days=7):
    """Save delivery records from the last ``days`` days to a CSV file."""
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    deliveries = trader.list_delivery_history(start, end)
    if not deliveries:
        print("No recent deliveries found.")
        return
    _write_trade_history_csv(trader, deliveries, "recent_deliveries.csv")


def export_all_delivery_history(trader):
    """Save all available delivery records up to now to a CSV file."""
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = 0
    try:
        deliveries = trader.list_delivery_history(start, end)
    except ApiException as exc:
        print(f"Failed to retrieve delivery history: {exc}")
        return
    if not deliveries:
        print("No deliveries found.")
        return
    _write_trade_history_csv(trader, deliveries, "all_deliveries.csv")


def set_profit_targets(trader, multiplier=2):
    """Place reduce-only limit orders for open long positions.

    A limit order is submitted for each open ``Buy`` position such that the
    potential profit is at least ``multiplier`` times the premium paid.
    Fees are ignored when computing the target price.
    """
    positions = trader.get_positions()
    if not positions:
        print("No open positions found.")
        return
    for pos in positions:
        qty = abs(float(pos.get("size", 0)))
        if qty <= 0:
            continue
        side = str(pos.get("side", "")).lower()
        if side != "buy":
            # Skipping shorts because profit is collected upfront
            continue
        symbol = pos.get("symbol")
        avg_price = float(pos.get("avgPrice", 0))
        if not symbol or not avg_price:
            continue
        target = avg_price * (multiplier + 1)
        try:
            trader.place_order(symbol, "Sell", qty, target, "GTC", True)
            print(f"Placed reduce-only Sell {qty} {symbol} @ {target}")
        except ApiException as exc:
            print(
                f"Warning: failed to place reduce-only Sell {qty} {symbol} @ {target}: {exc}"
            )
            continue


def interactive_menu(cfg_path):
    """Show an interactive menu for common actions."""
    cfg = load_trade_config(cfg_path)
    key, secret = get_api_credentials(cfg)
    trader = BybitOptionsTrader(key, secret, BASE_URL)
    while True:
        print("\nSelect an option:")
        print("1. Place configured trade")
        print("2. Show open option orders/positions")
        print("3. Cancel all open orders and positions")
        print("4. Edit an open order")
        print("5. Export trade history (last 7 days) to CSV")
        print("6. Export all trade history to CSV")
        print("7. Place reduce-only exits for open positions")
        print("0. Exit")
        choice = input("Choice: ").strip()
        if choice == "1":
            execute_trade(cfg_path)
        elif choice == "2":
            show_open(trader)
        elif choice == "3":
            cancel_all(trader)
        elif choice == "4":
            edit_open_order(trader)
        elif choice == "5":
            export_recent_trade_history(trader)
        elif choice == "6":
            export_all_trade_history(trader)
        elif choice == "7":
            set_profit_targets(trader)
        elif choice == "0":
            break
        else:
            print("Invalid choice.")

def main():
    """Entry point for CLI execution."""
    parser = argparse.ArgumentParser(description="Bybit options helper")
    parser.add_argument(
        "order_file",
        nargs="?",
        default="",
        help="Path to JSON config (used only with --no-menu)",
    )
    parser.add_argument(
        "--no-menu",
        action="store_true",
        help="Execute trade immediately without showing the menu",
    )
    args = parser.parse_args()
    if args.no_menu:
        if not args.order_file:
            raise SystemExit("order_file required with --no-menu")
        execute_trade(args.order_file)
    else:
        import web_menu
        web_menu.start()

if __name__=='__main__':
    main()
