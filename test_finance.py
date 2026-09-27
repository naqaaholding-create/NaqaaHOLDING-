import os
os.environ["DATABASE_PATH"] = "test_naqa.db"
os.environ["NAQAA_ADMIN_KEY"] = "test-admin"
os.environ["REAL_MONEY_ENABLED"] = "0"

from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def register(role, email, name):
    return client.post("/api/v1/auth/register", json={
        "role": role, "name": name, "email": email, "password": "TestPass123!",
        "account_type": "individual", "identity_type": "passport"
    })

def login(email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"})
    assert r.status_code == 200
    return r.json()["token"]

def test_wallet_controls_and_double_entry():
    buyer = register("buyer", "buyer@test.local", "Buyer").json()
    seller = register("seller", "seller@test.local", "Seller").json()
    bt = login("buyer@test.local")
    st = login("seller@test.local")

    # Deposit request is pending and must not increase balance.
    r = client.post("/api/v1/wallet/deposit-request?token="+bt,
                    headers={"Idempotency-Key": "dep-1"},
                    json={"account_id": buyer["id"], "amount": 1000, "currency": "USD"})
    assert r.status_code == 200
    assert client.get(f"/api/v1/wallet/{buyer['id']}?token={bt}").json()["balance"] == 0

    # Same idempotency key cannot create another request.
    dup = client.post("/api/v1/wallet/deposit-request?token="+bt,
                      headers={"Idempotency-Key": "dep-1"},
                      json={"account_id": buyer["id"], "amount": 9999, "currency": "USD"})
    assert dup.status_code == 200 and dup.json()["duplicate"] is True

    # Authorized approval credits exactly 1000.
    rid = r.json()["request_id"]
    ap = client.post(f"/api/v1/admin/wallet-requests/{rid}/approve",
                     headers={"X-Admin-Key": "test-admin"})
    assert ap.status_code == 200
    w = client.get(f"/api/v1/wallet/{buyer['id']}?token={bt}").json()
    assert w["balance"] == 1000 and w["available"] == 1000

    # Cannot withdraw more than available.
    over = client.post("/api/v1/wallet/withdraw-request?token="+bt,
                       headers={"Idempotency-Key": "wd-over"},
                       json={"account_id": buyer["id"], "amount": 10000, "currency": "USD"})
    assert over.status_code == 400

    # Reserve 400, then only 600 remains available.
    wd = client.post("/api/v1/wallet/withdraw-request?token="+bt,
                     headers={"Idempotency-Key": "wd-1"},
                     json={"account_id": buyer["id"], "amount": 400, "currency": "USD"})
    assert wd.status_code == 200
    w = client.get(f"/api/v1/wallet/{buyer['id']}?token={bt}").json()
    assert w["available"] == 600 and w["held"] == 400

    # Payment cannot spend the held 400.
    pay = client.post("/api/v1/wallet/pay?token="+bt,
                      headers={"Idempotency-Key": "pay-too-much"},
                      json={"from_account_id": buyer["id"], "to_account_id": seller["id"], "amount": 700, "currency": "USD"})
    assert pay.status_code == 400

    # Pay the remaining 600 to seller.
    pay = client.post("/api/v1/wallet/pay?token="+bt,
                      headers={"Idempotency-Key": "pay-1"},
                      json={"from_account_id": buyer["id"], "to_account_id": seller["id"], "amount": 600, "currency": "USD"})
    assert pay.status_code == 200

    # Repeating the same payment key does not double-charge.
    dup_pay = client.post("/api/v1/wallet/pay?token="+bt,
                          headers={"Idempotency-Key": "pay-1"},
                          json={"from_account_id": buyer["id"], "to_account_id": seller["id"], "amount": 600, "currency": "USD"})
    assert dup_pay.status_code == 200 and dup_pay.json()["duplicate"] is True

    bw = client.get(f"/api/v1/wallet/{buyer['id']}?token={bt}").json()
    sw = client.get(f"/api/v1/wallet/{seller['id']}?token={st}").json()
    assert bw["balance"] == 400 and bw["available"] == 0
    assert sw["balance"] == 600

    rec = client.get("/api/v1/finance/reconciliation").json()
    assert rec["ok"] is True
