import optionstrader


def test_wait_for_order_fill(monkeypatch):
    trader = optionstrader.BybitOptionsTrader('k', 's', 'u')
    calls = {'n': 0}

    def fake_history(symbol, order_id, limit=20):
        calls['n'] += 1
        if calls['n'] >= 2:
            return [{'orderId': order_id}]
        return []

    monkeypatch.setattr(trader, 'get_trade_history', fake_history)
    monkeypatch.setattr(trader, 'get_order_detail', lambda s, o: [])
    monkeypatch.setattr(optionstrader.time, 'sleep', lambda x: None)
    trades = trader.wait_for_order_fill('S', 'OID', timeout=0.1, poll_interval=0.01)
    assert trades and trades[0]['orderId'] == 'OID'
