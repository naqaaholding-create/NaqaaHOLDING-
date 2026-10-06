import os
import importlib
import tempfile
from pathlib import Path


def test_production_readiness_is_blocked_by_default():
    os.environ["REAL_MONEY_ENABLED"] = "0"
    os.environ["PRODUCTION_APPROVED"] = "0"
    os.environ["FINANCE_PRODUCTION_APPROVED"] = "0"
    os.environ["KYC_KYB_PRODUCTION_APPROVED"] = "0"
    os.environ["DATABASE_PERSISTENT"] = "0"
    os.environ.pop("DATABASE_PATH", None)
    mod = importlib.reload(importlib.import_module("app"))
    assert mod.production_money_blockers() == []
    os.environ["REAL_MONEY_ENABLED"] = "0"
