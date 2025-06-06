import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import optionstrader

def test_load_trade_config_fallback(tmp_path, monkeypatch):
    """load_trade_config should locate the file in the script directory when the working directory doesn't contain it."""
    monkeypatch.chdir(tmp_path)
    cfg = optionstrader.load_trade_config('trade_config.json')
    assert {'symbol', 'side', 'quantity', 'auto_trade', 'risk_usd'}.issubset(cfg)

def test_get_api_credentials_from_config(tmp_path, monkeypatch):
    path = tmp_path / 'cfg.json'
    path.write_text('{"symbol":"S","side":"Buy","quantity":1,"api_key":"K","api_secret":"S"}')
    monkeypatch.delenv('BYBIT_API_KEY', raising=False)
    monkeypatch.delenv('BYBIT_API_SECRET', raising=False)
    cfg = optionstrader.load_trade_config(str(path))
    key, secret = optionstrader.get_api_credentials(cfg)
    assert key == 'K' and secret == 'S'

def test_get_api_credentials_env_override(tmp_path, monkeypatch):
    path = tmp_path / 'cfg.json'
    path.write_text('{"symbol":"S","side":"Buy","quantity":1,"api_key":"K","api_secret":"S"}')
    monkeypatch.setenv('BYBIT_API_KEY', 'EK')
    monkeypatch.setenv('BYBIT_API_SECRET', 'ES')
    cfg = optionstrader.load_trade_config(str(path))
    key, secret = optionstrader.get_api_credentials(cfg)
    assert key == 'EK' and secret == 'ES'


def test_load_trade_config_defaults(tmp_path):
    path = tmp_path / 'cfg.json'
    path.write_text('{"symbol":"S","side":"Buy","quantity":1}')
    cfg = optionstrader.load_trade_config(str(path))
    assert cfg['risk_usd'] == 0
    assert cfg['auto_trade'] is False


def test_choose_symbol_by_risk_earliest(monkeypatch):
    instruments = [
        {'symbol': 'BTC-07JUN25-100000-P'},
        {'symbol': 'BTC-14JUN25-100000-P'},
        {'symbol': 'BTC-25JUL25-100000-P'},
    ]
    prices = {
        'BTC-07JUN25-100000-P': {'markPrice': '1'},
        'BTC-14JUN25-100000-P': {'markPrice': '0.5'},
        'BTC-25JUL25-100000-P': {'markPrice': '0.2'},
    }

    def fake_insts(base_coin, expiry=None, option_type=None, base_url=None):
        assert expiry is None
        assert option_type == 'P'
        return instruments

    def fake_tick(symbol, base_url=None):
        return prices[symbol]

    monkeypatch.setattr(optionstrader, 'fetch_option_instruments', fake_insts)
    monkeypatch.setattr(optionstrader, 'fetch_option_ticker', fake_tick)
    sym, price = optionstrader.choose_symbol_by_risk('BTC-07JUN25-105000-P-USDT', 1, 1)
    assert sym == 'BTC-07JUN25-100000-P'
    assert price == 1.0


def test_choose_symbol_by_risk_single_digit(monkeypatch):
    instruments = [
        {'symbol': 'BTC-7JUN25-100000-P'},
        {'symbol': 'BTC-14JUN25-100000-P'},
    ]
    prices = {
        'BTC-7JUN25-100000-P': {'markPrice': '1'},
        'BTC-14JUN25-100000-P': {'markPrice': '0.5'},
    }

    def fake_insts(base_coin, expiry=None, option_type=None, base_url=None):
        assert option_type == 'P'
        return [i for i in instruments if i['symbol'].split('-')[3] == 'P']

    def fake_tick(symbol, base_url=None):
        return prices[symbol]

    monkeypatch.setattr(optionstrader, 'fetch_option_instruments', fake_insts)
    monkeypatch.setattr(optionstrader, 'fetch_option_ticker', fake_tick)
    sym, price = optionstrader.choose_symbol_by_risk('BTC-7JUN25-105000-P-USDT', 1, 1)
    assert sym == 'BTC-7JUN25-100000-P'
    assert price == 1.0


def test_choose_symbol_by_risk_respects_option_type(monkeypatch):
    instruments = [
        {'symbol': 'BTC-07JUN25-100000-C'},
        {'symbol': 'BTC-07JUN25-100000-P'},
    ]
    prices = {
        'BTC-07JUN25-100000-C': {'markPrice': '0.8'},
        'BTC-07JUN25-100000-P': {'markPrice': '1.2'},
    }

    def fake_insts(base_coin, expiry=None, option_type=None, base_url=None):
        assert option_type == 'P'
        return [i for i in instruments if i['symbol'].split('-')[3] == 'P']

    def fake_tick(symbol, base_url=None):
        return prices[symbol]

    monkeypatch.setattr(optionstrader, 'fetch_option_instruments', fake_insts)
    monkeypatch.setattr(optionstrader, 'fetch_option_ticker', fake_tick)
    sym, _ = optionstrader.choose_symbol_by_risk('BTC-07JUN25-105000-P-USDT', 1, 1)
    assert sym.endswith('-P')


def test_compute_order_qty_floor():
    qty = optionstrader.compute_order_qty(0.1, 100)
    assert qty == optionstrader.MIN_ORDER_QTY


def test_compute_order_qty_round_to_increment():
    qty = optionstrader.compute_order_qty(0.32, 20)
    assert qty == 0.02
