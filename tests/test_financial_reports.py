import os
import tempfile

DB = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = DB
os.environ["NAQAA_ADMIN_KEY"] = "ci-admin-key"
os.environ["REAL_MONEY_ENABLED"] = "0"

from fastapi.testclient import TestClient
import app

app.ADMIN_API_KEY = "ci-admin-key"
client = TestClient(app.app)

def test_financial_reports_are_admin_only_and_ledger_derived():
    denied = client.get("/api/v1/admin/reports/company-revenue")
    assert denied.status_code == 401

    c = app.db()
    app.post_entry(
        c, "REP-1", "marketplace_settlement",
        [
            {"account_id": "SYSTEM:CASH", "side": "debit", "amount_cents": 300},
            {"account_id": "SYSTEM:COMMISSION_REVENUE", "side": "credit", "amount_cents": 300},
        ],
        "report fixture", "report-fixture-1",
    )
    c.execute(
        "INSERT INTO commission_entries(id,order_id,side,amount_cents,currency,rate_bps,fixed_cents,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("ce-1", "order-report-1", "buyer", 100, "USD", 100, 0, "posted", app.now()),
    )
    c.execute(
        "INSERT INTO escrow_transactions(id,order_id,amount_cents,currency,status,held_at,released_at) VALUES(?,?,?,?,?,?,?)",
        ("es-1", "order-report-1", 9800, "USD", "held", app.now(), None),
    )
    c.commit()
    c.close()

    headers = {"X-Admin-Key": "ci-admin-key"}

    revenue = client.get("/api/v1/admin/reports/company-revenue", headers=headers)
    assert revenue.status_code == 200, revenue.text
    assert revenue.json()["commission_revenue"] == 3.0
    assert revenue.json()["source"] == "commission_revenue_ledger"

    commissions = client.get("/api/v1/admin/reports/commissions", headers=headers)
    assert commissions.status_code == 200, commissions.text
    assert commissions.json()["total_commission"] == 1.0

    escrow = client.get("/api/v1/admin/reports/escrow", headers=headers)
    assert escrow.status_code == 200, escrow.text
    assert escrow.json()["current"]["held"] == 98.0

    journal = client.get("/api/v1/admin/reports/journal", headers=headers)
    assert journal.status_code == 200, journal.text
    assert any(x["entry"]["reference"] == "REP-1" for x in journal.json()["entries"])

    daily = client.get("/api/v1/admin/reports/daily-reconciliation", headers=headers)
    assert daily.status_code == 200, daily.text
    assert daily.json()["ok"] is True

    wallet = client.get("/api/v1/admin/reports/wallet-movements", headers=headers)
    assert wallet.status_code == 200, wallet.text
    assert wallet.json()["source"] == "double_entry_ledger"

    reconciliation = client.get("/api/v1/finance/reconciliation")
    assert reconciliation.status_code == 200, reconciliation.text
    assert reconciliation.json()["ok"] is True
