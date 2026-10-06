import app


def test_production_preflight_blocks_live_money_without_verified_controls(monkeypatch):
    monkeypatch.setattr(app, "REAL_MONEY_ENABLED", True)
    monkeypatch.setattr(app, "FINANCE_PRODUCTION_APPROVED", False)
    monkeypatch.delenv("NAQAA_ADMIN_KEY", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    result = app.production_preflight()

    assert result["ready"] is False
    assert result["money_mode"] == "blocked"


def test_production_preflight_can_be_ready_only_after_all_gates_are_set(monkeypatch):
    monkeypatch.setattr(app, "REAL_MONEY_ENABLED", True)
    monkeypatch.setattr(app, "FINANCE_PRODUCTION_APPROVED", True)
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("DATABASE_PATH", "/var/data/naqaa_market.db")

    result = app.production_preflight()

    assert result["ready"] is True
    assert result["money_mode"] == "live"
    assert result["blockers"] == []
