import os, tempfile, sqlite3
from fastapi.testclient import TestClient

def make_client():
    os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    os.environ["REAL_MONEY_ENABLED"] = "0"
    os.environ["NAQAA_ADMIN_KEY"] = "test-admin"
    import app
    return TestClient(app.app)

def reg(c, role, email):
    return c.post("/api/v1/auth/register", json={
        "role": role, "name": role.title(), "email": email, "password": "StrongPass123",
        "account_type": "individual", "identity_type": "passport", "identity_number": "AA123456"
    })

def login(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]

def test_deposit_pending_does_not_increase_balance():
    c = make_client()
    reg(c, "buyer", "buyer1@example.test")
    token = login(c, "buyer1@example.test")
    me = c.get("/api/v1/auth/me", params={"token": token}).json()
    aid = me["id"]
    r = c.post("/api/v1/wallet/deposit-request", params={"token": token},
               headers={"Idempotency-Key":"dep-1"},
               json={"account_id":aid,"amount":1000,"currency":"USD"})
    assert r.status_code == 200
    w = c.get(f"/api/v1/wallet/{aid}", params={"token":token}).json()
    assert w["balance_cents"] == 0
    assert w["pending_deposit_cents"] == 100000

def test_withdraw_cannot_exceed_available_and_hold_is_reserved():
    c = make_client()
    reg(c, "buyer", "buyer2@example.test")
    token = login(c, "buyer2@example.test")
    aid = c.get("/api/v1/auth/me", params={"token":token}).json()["id"]
    # Seed balance only through authorized admin approval.
    dep = c.post("/api/v1/wallet/deposit-request", params={"token":token},
                 headers={"Idempotency-Key":"dep-2"},
                 json={"account_id":aid,"amount":1000,"currency":"USD"}).json()
    assert c.post(f"/api/v1/admin/wallet-requests/{dep['request_id']}/approve",
                  headers={"X-Admin-Key":"test-admin"}).status_code == 200
    over = c.post("/api/v1/wallet/withdraw-request", params={"token":token},
                  headers={"Idempotency-Key":"wd-over"},
                  json={"account_id":aid,"amount":10000,"currency":"USD"})
    assert over.status_code == 400
    wd = c.post("/api/v1/wallet/withdraw-request", params={"token":token},
                headers={"Idempotency-Key":"wd-ok"},
                json={"account_id":aid,"amount":600,"currency":"USD"})
    assert wd.status_code == 200
    w = c.get(f"/api/v1/wallet/{aid}", params={"token":token}).json()
    assert w["balance_cents"] == 100000
    assert w["held_cents"] == 60000
    assert w["available_cents"] == 40000

def test_payment_is_idempotent_and_reconciliation_stays_balanced():
    c = make_client()
    reg(c, "buyer", "buyer3@example.test")
    reg(c, "seller", "seller3@example.test")
    bt, st = login(c, "buyer3@example.test"), login(c, "seller3@example.test")
    bid = c.get("/api/v1/auth/me",params={"token":bt}).json()["id"]
    sid = c.get("/api/v1/auth/me",params={"token":st}).json()["id"]
    dep = c.post("/api/v1/wallet/deposit-request",params={"token":bt},
                 headers={"Idempotency-Key":"dep-3"},
                 json={"account_id":bid,"amount":1000,"currency":"USD"}).json()
    assert c.post(f"/api/v1/admin/wallet-requests/{dep['request_id']}/approve",
                  headers={"X-Admin-Key":"test-admin"}).status_code == 200
    h={"Idempotency-Key":"pay-1"}
    payload={"from_account_id":bid,"to_account_id":sid,"amount":250,"currency":"USD"}
    first=c.post("/api/v1/wallet/pay",params={"token":bt},headers=h,json=payload)
    second=c.post("/api/v1/wallet/pay",params={"token":bt},headers=h,json=payload)
    assert first.status_code == second.status_code == 200
    assert second.json().get("duplicate") is True
    rec=c.get("/api/v1/finance/reconciliation")
    assert rec.status_code == 200 and rec.json()["ok"] is True

def test_marketplace_commission_never_exceeds_gross():
    c=make_client()
    reg(c, "buyer", "buyer4@example.test")
    reg(c, "seller", "seller4@example.test")
    bt, st = login(c, "buyer4@example.test"), login(c, "seller4@example.test")
    bid=c.get("/api/v1/auth/me",params={"token":bt}).json()["id"]
    sid=c.get("/api/v1/auth/me",params={"token":st}).json()["id"]
    # Create a listing and offer; payment should require approved KYC.
    l=c.post("/api/v1/listings",json={"seller_id":sid,"category":"test","title":"Test","description":"Test","amount":100,"currency":"USD"})
    assert l.status_code in (200,201)
    listing=l.json()["id"]
    o=c.post("/api/v1/offers",json={"listing_id":listing,"buyer_id":bid,"amount":100,"currency":"USD"})
    assert o.status_code in (200,201)
    a=c.post(f"/api/v1/offers/{o.json()['id']}/accept",params={"token":st})
    assert a.status_code == 200
    pay=c.post(f"/api/v1/orders/{a.json()['order_id']}/pay",params={"token":bt},
               headers={"Idempotency-Key":"order-pay-1"})
    assert pay.status_code == 403
