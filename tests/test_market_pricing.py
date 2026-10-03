import os
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(__file__).parent / "test_pricing_runtime.db")
os.environ["REAL_MONEY_ENABLED"] = "0"

from fastapi.testclient import TestClient
from app import app

def test_pricing_catalog_matches_current_market_plan():
    with TestClient(app) as client:
        r = client.get("/api/v1/finance/pricing")
        assert r.status_code == 200
        data = r.json()
        assert data["currency"] == "USD"
        assert data["real_money_collection"] is False
        sources = {item["code"]: item for item in data["revenue_sources"]}
        assert set(sources) == {"subscription", "advertising", "protected_sales_commission"}
        subscription = {item["code"]: item["amount_cents"] for item in sources["subscription"]["items"]}
        advertising = {item["code"]: item["amount_cents"] for item in sources["advertising"]["items"]}
        assert subscription == {"seller_monthly": 1000, "buyer_monthly": 300}
        assert advertising == {"listing": 200, "contact_unlock": 100}
        assert sources["protected_sales_commission"]["items"] == []
