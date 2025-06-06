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
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode
import hmac
import hashlib

import requests
from tabulate import tabulate

# === Configuration ===
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api-demo.bybit.com"
RECV_WINDOW = "5000"
SUB_ACCOUNT_NAME = ""
MIN_BALANCE_THRESHOLD = 10.0

# === File setup ===
script_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(script_dir, '1.log')
output_file = os.path.join(script_dir, '1_output.txt')

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
logger.info("Starting 1.py; logs to %s, output to %s", log_file, output_file)

def ensure_tests_pass():
    """Run pytest and exit if any tests fail."""
    logger.info("Running test suite before execution")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        logger.error("Tests failed:\n%s", result.stdout + result.stderr)
        print(result.stdout + result.stderr)
        sys.exit(result.returncode)
    logger.info("All tests passed")

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
    for field in ("symbol", "side", "quantity"):
        if field not in cfg or cfg[field] in (None, ""):
            raise ValueError(f"Missing required field in config: {field}")
    return cfg

def get_api_credentials(cfg):
    """Return API credentials from environment variables or config."""
    key = os.getenv("BYBIT_API_KEY") or cfg.get("api_key", "")
    secret = os.getenv("BYBIT_API_SECRET") or cfg.get("api_secret", "")
    return key, secret

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
        body = {"category":"option","symbol":symbol,"side":side,
                "orderType":"Limit" if price else "Market",
                "qty":str(qty),"timeInForce":tif,
                "orderLinkId":uuid.uuid4().hex}
        if price:
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
        # Calculate target: e.g. 3x entry_price
        target = entry_price * 3
        exit_side = "Sell" if side.lower()=="buy" else "Buy"
        self.place_order(symbol, exit_side, qty, target, tif, True)
        return trades, trade_log

def main():
    """Execute a single options trade and output Greek exposures."""
    parser = argparse.ArgumentParser(description="Execute trade and fetch Greeks.")
    parser.add_argument("order_file", help="Path to JSON config.")
    args = parser.parse_args()
    try:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cfg = load_trade_config(args.order_file)
        symbol, side, qty = cfg["symbol"], cfg["side"], cfg["quantity"]
        entry_price = cfg.get("limit_price")
        key, secret = get_api_credentials(cfg)
        if not key or not secret:
            raise RuntimeError(
                "API credentials not provided. Set BYBIT_API_KEY and BYBIT_API_SECRET "
                "environment variables or include api_key/api_secret in the config file."
            )
        trader = BybitOptionsTrader(key, secret, BASE_URL)
        balance = trader.get_wallet_balance()
        # Prepare output
        lines = [f"Timestamp: {ts}", f"Balance: {balance:.4f} USDT"]
        if balance < MIN_BALANCE_THRESHOLD:
            lines.append("⚠️ Insufficient balance => abort")
            print_and_write(lines)
            sys.exit(1)
        order_desc = 'Market' if not entry_price else entry_price
        lines.append(f"Placing {side} {qty} {symbol} @ {order_desc}")
        _trades, trade_log = trader.place_and_log(symbol, side, qty, entry_price, "GTC")
        lines.append(f"Trade log: {trade_log}")
        # Fetch ticker and Greeks
        tick = fetch_option_ticker(symbol)
        lines.append("\nTicker Data:")
        for k,v in sorted(tick.items()):
            lines.append(f"  {k}: {v}")
        # Greeks table
        greeks = {k: float(tick[k]) for k in ('delta','gamma','vega','theta') if k in tick}
        mult = 1 if side.lower()=='buy' else -1
        headers = ['Greek', 'Per-Contract', 'Qty', 'Exposure']
        rows = []
        for name, per in greeks.items():
            exp = per * qty * mult
            rows.append([name.capitalize(), f"{per:.8f}", str(qty), f"{exp:.8f}"])
        lines.append("\nGreek Exposures:")
        table = tabulate(rows, headers=headers, tablefmt="plain")
        lines.extend(table.splitlines())
        print_and_write(lines)
    except Exception:
        tb=traceback.format_exc()
        logger.error("Fatal:\n%s",tb)
        print(tb)
        sys.exit(1)

if __name__=='__main__':
    ensure_tests_pass()
    main()
