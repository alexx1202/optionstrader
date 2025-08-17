import optionstrader

class DummyTrader(optionstrader.BybitOptionsTrader):
    def __init__(self):
        pass
    def get_positions(self, symbol=None):
        return [{"symbol": "BTC-TEST", "side": "Buy", "size": "1", "avgPrice": "0.5"}]

    def place_order(self, symbol, side, qty, price=None, tif="GTC", is_exit=False):
        self.called = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "is_exit": is_exit,
        }


def test_set_profit_targets_places_order():
    trader = DummyTrader()
    optionstrader.set_profit_targets(trader)
    assert trader.called["side"] == "Sell"
    assert trader.called["price"] == 1.5
    assert trader.called["is_exit"] is True

def test_set_profit_targets_skips_shorts(monkeypatch):
    class ShortTrader(DummyTrader):
        def get_positions(self, symbol=None):
            return [{"symbol": "BTC-TEST", "side": "Sell", "size": "1", "avgPrice": "0.5"}]
    trader = ShortTrader()
    called = False
    def fake_place_order(*a, **k):
        nonlocal called
        called = True
    trader.place_order = fake_place_order
    optionstrader.set_profit_targets(trader)
    assert called is False


def test_set_profit_targets_continues_on_error(capsys):
    class ErrorTrader(optionstrader.BybitOptionsTrader):
        def __init__(self):
            self.calls = 0

        def get_positions(self, symbol=None):
            return [
                {"symbol": "BTC-FAIL", "side": "Buy", "size": "1", "avgPrice": "0.5"},
                {"symbol": "BTC-OK", "side": "Buy", "size": "1", "avgPrice": "0.5"},
            ]

        def place_order(self, symbol, side, qty, price=None, tif="GTC", is_exit=False):
            self.calls += 1
            if self.calls == 1:
                raise optionstrader.ApiException("boom")
            self.called = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "is_exit": is_exit,
            }

    trader = ErrorTrader()
    optionstrader.set_profit_targets(trader)
    assert trader.calls == 2
    assert trader.called["symbol"] == "BTC-OK"
    captured = capsys.readouterr()
    assert "Warning" in captured.out
