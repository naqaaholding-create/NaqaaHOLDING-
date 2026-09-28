import app


def test_production_preflight_blocks_live_money_without_verified_controls(monkeypatch):
    monkeypatch.setattr(app, "REAL_MONEY_ENABLED", True)
    monkeypatch.setattr(app, "FINANCE_PRODUCTION_APPROVED", False)
    monkeypatch.setattr(app, "CWALLET_LIVE_CONTRACT_VERIFIED", False)
    monkeypatch.setenv("CWALLET_ENABLED", "0")
    monkeypatch.delenv("NAQAA_ADMIN_KEY", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    result = app.production_preflight()

    assert result["ready"] is False
    assert result["money_mode"] == "blocked"
    assert "Cwallet live API/webhook contract has not been verified" in result["blockers"]
    assert "CWALLET_ENABLED is not enabled" in result["blockers"]


def test_production_preflight_can_be_ready_only_after_all_gates_are_set(monkeypatch):
    monkeypatch.setattr(app, "REAL_MONEY_ENABLED", True)
    monkeypatch.setattr(app, "FINANCE_PRODUCTION_APPROVED", True)
    monkeypatch.setattr(app, "CWALLET_LIVE_CONTRACT_VERIFIED", True)
    monkeypatch.setenv("CWALLET_ENABLED", "1")
    monkeypatch.setenv("CWALLET_ENV", "production")
    monkeypatch.setenv("CWALLET_API_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("CWALLET_API_KEY", "test-key")
    monkeypatch.setenv("CWALLET_API_SECRET", "test-secret")
    monkeypatch.setenv("CWALLET_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("CWALLET_PAYMENT_PATH", "/payment")
    monkeypatch.setenv("CWALLET_PAYOUT_PATH", "/payout")
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("DATABASE_PATH", "/var/data/naqaa_market.db")

    result = app.production_preflight()

    assert result["ready"] is True
    assert result["money_mode"] == "live"
    assert result["blockers"] == []
