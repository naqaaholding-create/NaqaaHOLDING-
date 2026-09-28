from cwallet_provider import CwalletConfigurationError, CwalletProvider


def test_cwallet_disabled_by_default():
    provider = CwalletProvider()
    assert provider.enabled is False


def test_cwallet_webhook_signature(monkeypatch):
    monkeypatch.setenv("CWALLET_WEBHOOK_SECRET", "test-secret")
    provider = CwalletProvider()
    body = b'{"event_id":"evt-1","status":"paid"}'
    import hashlib, hmac
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    assert provider.verify_webhook(body, signature) is True
    assert provider.verify_webhook(body, "bad") is False


def test_cwallet_disabled_does_not_send():
    provider = CwalletProvider()
    try:
        provider.create_payment(
            reference="NQ-TEST",
            amount="1.00",
            currency="USD",
            asset="USDT",
            network="TRC20",
            callback_url="https://example.invalid/webhook",
        )
    except CwalletConfigurationError:
        return
    raise AssertionError("disabled Cwallet provider must not send requests")
