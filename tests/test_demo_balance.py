import json
from pathlib import Path

import web_menu


def test_trade_page_uses_demo_balance():
    cfg_path = Path("trade_config.json")
    original = cfg_path.read_text(encoding="utf-8")
    try:
        cfg_path.write_text(json.dumps({"demo_balance": 123.45}), encoding="utf-8")
        client = web_menu.app.test_client()
        resp = client.get("/trade")
        assert "Current Balance: 123.45 USDT" in resp.get_data(as_text=True)
    finally:
        cfg_path.write_text(original, encoding="utf-8")
