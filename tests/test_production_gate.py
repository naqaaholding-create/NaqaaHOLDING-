import importlib

import pytest


def test_production_readiness_is_blocked_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("REAL_MONEY_ENABLED", "0")
    monkeypatch.setenv("PRODUCTION_APPROVED", "0")
    monkeypatch.setenv("FINANCE_PRODUCTION_APPROVED", "0")
    monkeypatch.setenv("KYC_KYB_PRODUCTION_APPROVED", "0")
    monkeypatch.setenv("DATABASE_PERSISTENT", "0")
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("NAQAA_ADMIN_KEY", raising=False)

    mod = importlib.reload(importlib.import_module("app"))
    blockers = set(mod.production_money_blockers())

    assert {
        "PRODUCTION_APPROVED",
        "FINANCE_PRODUCTION_APPROVED",
        "KYC_KYB_PRODUCTION_APPROVED",
        "DATABASE_PERSISTENT",
        "DATABASE_PATH",
        "NAQAA_ADMIN_KEY",
        "VERIFIED_PROVIDER_SETTLEMENT",
    } <= blockers
    assert mod.REAL_MONEY_ENABLED is False


def test_production_readiness_stays_blocked_until_provider_verifier_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("REAL_MONEY_ENABLED", "0")
    monkeypatch.setenv("PRODUCTION_APPROVED", "1")
    monkeypatch.setenv("FINANCE_PRODUCTION_APPROVED", "1")
    monkeypatch.setenv("KYC_KYB_PRODUCTION_APPROVED", "1")
    monkeypatch.setenv("DATABASE_PERSISTENT", "1")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "production-test.db"))
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "test-only-admin-key")

    mod = importlib.reload(importlib.import_module("app"))

    assert mod.production_money_blockers() == ["VERIFIED_PROVIDER_SETTLEMENT"]
    assert mod.PROVIDER_SETTLEMENT_VERIFIER_IMPLEMENTED is False
    assert mod.REAL_MONEY_ENABLED is False


def test_live_finance_is_blocked_when_provider_verifier_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("REAL_MONEY_ENABLED", "0")
    monkeypatch.setenv("PRODUCTION_APPROVED", "1")
    monkeypatch.setenv("FINANCE_PRODUCTION_APPROVED", "1")
    monkeypatch.setenv("KYC_KYB_PRODUCTION_APPROVED", "1")
    monkeypatch.setenv("DATABASE_PERSISTENT", "1")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "production-test.db"))
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "test-only-admin-key")

    mod = importlib.reload(importlib.import_module("app"))
    mod.REAL_MONEY_ENABLED = True

    with pytest.raises(mod.HTTPException) as exc:
        mod.require_live_finance("crypto")

    assert exc.value.status_code == 503
    assert "VERIFIED_PROVIDER_SETTLEMENT" in mod.production_money_blockers()
