import optionstrader

def test_get_telegram_credentials_env_override(monkeypatch):
    cfg = {'telegram_token': 'T', 'telegram_chat_id': 'C'}
    monkeypatch.setenv('TELEGRAM_TOKEN', 'ET')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'EC')
    token, chat_id = optionstrader.get_telegram_credentials(cfg)
    assert token == 'ET' and chat_id == 'EC'


def test_send_telegram_document_calls_post(monkeypatch, tmp_path):
    called = {}

    def fake_post(url, data=None, files=None, timeout=10):
        called['url'] = url
        called['data'] = data
        called['sent'] = files['document'].read()
        class Resp:
            pass
        return Resp()

    monkeypatch.setattr(optionstrader.requests, 'post', fake_post)
    f = tmp_path / 'f.log'
    f.write_text('hi')
    optionstrader.send_telegram_document(str(f), 'tok', 'chat')
    assert called['url'].endswith('/sendDocument')
    assert called['data']['chat_id'] == 'chat'
    assert called['sent'] == b'hi'


def test_send_telegram_document_no_creds(monkeypatch, tmp_path):
    posted = False
    def fake_post(*a, **k):
        nonlocal posted
        posted = True
    monkeypatch.setattr(optionstrader.requests, 'post', fake_post)
    f = tmp_path / 'f.log'
    f.write_text('x')
    optionstrader.send_telegram_document(str(f), '', '')
    assert posted is False
