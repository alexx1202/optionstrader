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
