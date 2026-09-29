import hashlib
import hmac
import json
import os
import sqlite3
import tempfile

from cwallet_provider import CwalletProvider
from cwallet_routes import ensure_cwallet_schema


def test_cwallet_schema_creates_provider_tables():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        ensure_cwallet_schema(c)
        names = {
            r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"payment_intents", "provider_transactions", "provider_webhooks", "payouts"} <= names
        c.close()
    finally:
        os.unlink(path)


def test_provider_disabled_by_default_and_no_network():
    provider = CwalletProvider()
    assert provider.enabled is False


def test_webhook_signature_is_deterministic_for_configured_secret(monkeypatch):
    secret = "integration-test-secret"
    monkeypatch.setenv("CWALLET_WEBHOOK_SECRET", secret)
    provider = CwalletProvider()
    body = json.dumps({"event_id": "evt-1", "status": "paid"}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert provider.verify_webhook(body, signature)
    assert not provider.verify_webhook(body, "invalid")


def test_cwallet_webhook_cannot_settle_in_sandbox(monkeypatch):
    import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "REAL_MONEY_ENABLED", False)
    monkeypatch.setattr(app, "FINANCE_PRODUCTION_APPROVED", False)
    monkeypatch.setattr(app, "CWALLET_LIVE_CONTRACT_VERIFIED", False)
    monkeypatch.setenv("CWALLET_ENABLED", "0")

    client = TestClient(app.app)
    response = client.post(
        "/api/v1/cwallet/webhook",
        content=b'{"event_id":"sandbox-event","status":"paid"}',
    )
    assert response.status_code == 503
