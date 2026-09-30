from tap_provider import TapProvider

def test_tap_disabled_by_default():
    assert TapProvider().enabled is False

def test_tap_configuration(monkeypatch):
    monkeypatch.setenv("TAP_ENABLED","1")
    monkeypatch.setenv("TAP_SECRET_KEY","test")
    monkeypatch.setenv("TAP_MERCHANT_ID","merchant")
    p=TapProvider()
    assert p.enabled is True
    assert p.secret_key=="test"
    assert p.merchant_id=="merchant"
