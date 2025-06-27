import csv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import optionstrader

class DummyTrader:
    def __init__(self, trades):
        self.trades = trades
    def list_trade_history(self, start, end):
        return self.trades
    def get_wallet_balance(self, coin="USDT"):
        return 100.0

def test_export_recent_trade_history(tmp_path, monkeypatch):
    ts = 1715000000000  # example timestamp in ms
    trades = [{"execTime": ts, "execFee": "0.1", "closedPnl": "0.2"}]
    monkeypatch.setattr(optionstrader, "script_dir", str(tmp_path))
    trader = DummyTrader(trades)
    optionstrader.export_recent_trade_history(trader, days=1)
    path = tmp_path / "recent_trades.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert abs(float(row["netFee"]) - 0.1) < 1e-9
    assert abs(float(row["netPnl"]) - 0.2) < 1e-9
    expected_time = datetime.fromtimestamp(ts/1000, timezone.utc)
    expected_time = expected_time.astimezone(ZoneInfo("Australia/Brisbane"))
    assert row["localTime"] == expected_time.strftime("%Y-%m-%d %H:%M:%S")
    assert abs(float(row["balance"]) - 100.0) < 1e-9
