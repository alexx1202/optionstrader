import textwrap
from journal_trades import _parse_trade_logs


def test_parse_trade_logs_from_text(tmp_path, monkeypatch):
    sample = textwrap.dedent(
        """\
        Open Orders:
          None

        Open Positions:
        {
          "symbol": "ETH-18AUG25-4450-P-USDT",
          "avgPrice": "37.9",
          "delta": "-0.038841247",
          "theta": "-2.161295116",
          "gamma": "0.000305838",
          "vega": "0.094796374"
        }
        Back
        """
    )
    (tmp_path / "positions.txt").write_text(sample)
    monkeypatch.chdir(tmp_path)
    logs = _parse_trade_logs()
    assert "ETH-18AUG25-4450-P-USDT" in logs
    data = logs["ETH-18AUG25-4450-P-USDT"]
    assert data["delta"] == -0.038841247
    assert data["theta"] == -2.161295116
