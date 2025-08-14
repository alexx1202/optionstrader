import web_menu
import optionstrader


def test_trade_page_uses_demo_balance(monkeypatch):
    monkeypatch.delenv('BYBIT_API_KEY', raising=False)
    monkeypatch.delenv('BYBIT_API_SECRET', raising=False)
    optionstrader.DEMO_BALANCE = 123.45
    client = web_menu.app.test_client()
    resp = client.get("/trade")
    assert "Current Balance: 123.45 USDT" in resp.get_data(as_text=True)
