import os
import importlib


def test_production_readiness_is_blocked_by_default():
    os.environ["REAL_MONEY_ENABLED"] = "0"
    os.environ["PRODUCTION_APPROVED"] = "0"
    os.environ["FINANCE_PRODUCTION_APPROVED"] = "0"
    os.environ["KYC_KYB_PRODUCTION_APPROVED"] = "0"
    os.environ["DATABASE_PERSISTENT"] = "0"
    os.environ.pop("DATABASE_PATH", None)
    os.environ["CWALLET_ENABLED"] = "0"
    os.environ["CWALLET_PROTOCOL_VERIFIED"] = "0"
    os.environ["CWALLET_LIVE_CONTRACT_VERIFIED"] = "0"
    os.environ["CWALLET_ENV"] = "sandbox"
    for key in (
        "NAQAA_ADMIN_KEY", "CWALLET_API_BASE_URL", "CWALLET_API_KEY",
        "CWALLET_API_SECRET", "CWALLET_PAYMENT_PATH",
        "CWALLET_PAYOUT_PATH", "CWALLET_WEBHOOK_SECRET"
    ):
        os.environ.pop(key, None)
    mod = importlib.import_module("app")
    blockers = mod.production_money_blockers()
    assert "PRODUCTION_APPROVED" in blockers
    assert "FINANCE_PRODUCTION_APPROVED" in blockers
    assert "DATABASE_PERSISTENT" in blockers
    assert "CWALLET_ENABLED" in blockers
    assert "CWALLET_PROTOCOL_VERIFIED" in blockers
    assert "CWALLET_LIVE_CONTRACT_VERIFIED" in blockers
    assert mod.REAL_MONEY_ENABLED is False


def test_production_gate_requires_all_controls():
    os.environ["REAL_MONEY_ENABLED"] = "0"
    os.environ["PRODUCTION_APPROVED"] = "1"
    os.environ["FINANCE_PRODUCTION_APPROVED"] = "1"
    os.environ["KYC_KYB_PRODUCTION_APPROVED"] = "1"
    os.environ["DATABASE_PERSISTENT"] = "1"
    os.environ["DATABASE_PATH"] = "/var/data/naqaa_market.db"
    os.environ["NAQAA_ADMIN_KEY"] = "test-admin"
    os.environ["CWALLET_ENABLED"] = "1"
    os.environ["CWALLET_PROTOCOL_VERIFIED"] = "1"
    os.environ["CWALLET_LIVE_CONTRACT_VERIFIED"] = "1"
    os.environ["CWALLET_ENV"] = "production"
    os.environ["CWALLET_API_BASE_URL"] = "https://provider.invalid"
    os.environ["CWALLET_API_KEY"] = "test"
    os.environ["CWALLET_API_SECRET"] = "test"
    os.environ["CWALLET_PAYMENT_PATH"] = "/payment"
    os.environ["CWALLET_PAYOUT_PATH"] = "/payout"
    os.environ["CWALLET_WEBHOOK_SECRET"] = "test"
    mod = importlib.reload(importlib.import_module("app"))
    assert mod.production_money_blockers() == []
    os.environ["REAL_MONEY_ENABLED"] = "0"
