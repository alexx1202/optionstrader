import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import optionstrader

def test_load_trade_config_fallback(tmp_path, monkeypatch):
    """load_trade_config should locate the file in the script directory when the working directory doesn't contain it."""
    monkeypatch.chdir(tmp_path)
    cfg = optionstrader.load_trade_config('trade_config.json')
    assert {'symbol', 'side', 'quantity'}.issubset(cfg)
