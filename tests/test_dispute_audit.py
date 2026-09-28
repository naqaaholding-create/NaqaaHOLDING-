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


def _register(email, role):
    r = client.post("/api/v1/auth/register", json={
        "role": role,
        "name": role.title(),
        "email": email,
        "password": "Test12345",
        "account_type": "individual",
        "identity_type": "passport",
        "identity_number": "AA123456",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _login(email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Test12345"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _approve_kyc(user_id):
    c = app.db()
    c.execute(
        "INSERT INTO kyc_cases(id,user_id,document_type,status,risk_level,created_at) VALUES(?,?,?,?,?,?)",
        (__import__("uuid").uuid4().hex, user_id, "passport", "approved", "standard", app.now()),
    )
    c.commit()
    c.close()


def test_dispute_open_creates_audit_log():
    seller = _register("audit-seller@test.local", "seller")
    buyer = _register("audit-buyer@test.local", "buyer")
    _approve_kyc(seller)
    _approve_kyc(buyer)

    c = app.db()
    c.execute(
        """INSERT INTO commission_rules
        (id,transaction_type,currency,buyer_rate,seller_rate,buyer_fixed,seller_fixed,
         minimum_fee,maximum_fee,effective_from,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("audit-rule", "marketplace", "USD", 0.01, 0.02, 0, 0, 0, None, app.now(), "active"),
    )
    c.commit()
    c.close()

    seller_token = _login("audit-seller@test.local")
    buyer_token = _login("audit-buyer@test.local")

    listing = client.post(
        "/api/v1/listings",
        params={"token": seller_token},
        json={
            "seller_id": seller,
            "category": "supplies",
            "title": "Audit Test Item",
            "description": "Audit test",
            "amount": 100,
            "currency": "USD",
        },
    )
    assert listing.status_code == 200, listing.text
    lid = listing.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish", params={"token": seller_token}).status_code == 200

    offer = client.post(
        "/api/v1/offers",
        params={"token": buyer_token},
        json={"listing_id": lid, "buyer_id": buyer, "amount": 100, "currency": "USD"},
    )
    assert offer.status_code == 200, offer.text
    accepted = client.post(f"/api/v1/offers/{offer.json()['id']}/accept", params={"token": seller_token})
    assert accepted.status_code == 200, accepted.text
    order_id = accepted.json()["order_id"]

    deposit = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "audit-deposit"},
        json={"account_id": buyer, "amount": 101, "currency": "USD"},
    )
    assert deposit.status_code == 200, deposit.text
    rid = deposit.json()["request_id"]
    approved = client.post(
        f"/api/v1/admin/wallet-requests/{rid}/approve",
        headers={"X-Admin-Key": "ci-admin-key"},
    )
    assert approved.status_code == 200, approved.text

    paid = client.post(
        f"/api/v1/orders/{order_id}/pay",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "audit-pay"},
    )
    assert paid.status_code == 200, paid.text

    opened = client.post(
        f"/api/v1/orders/{order_id}/dispute",
        params={"token": buyer_token},
        json={"reason": "Audit trail test"},
    )
    assert opened.status_code == 200, opened.text
    dispute_id = opened.json()["dispute_id"]

    c = app.db()
    audit = c.execute(
        "SELECT action,entity,entity_id,new_value FROM audit_logs WHERE action='dispute_opened' AND entity_id=?",
        (dispute_id,),
    ).fetchone()
    c.close()

    assert audit is not None
    assert audit["entity"] == "dispute"
    assert audit["new_value"] == "open"
