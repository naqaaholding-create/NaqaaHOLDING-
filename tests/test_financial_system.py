import os
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(__file__).parent / "test_naaqa.sqlite3")
os.environ["NAQAA_ADMIN_KEY"] = "test-admin"
os.environ["REAL_MONEY_ENABLED"] = "0"

from fastapi.testclient import TestClient
from app import app, db

client = TestClient(app)

def reg(role, email, account_type="individual"):
    return client.post("/api/v1/auth/register", json={
        "role": role, "name": role.title(), "email": email, "password": "Password123!",
        "account_type": account_type, "identity_type": "passport", "identity_number": "AA123456"
    })

def login(email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]["id"]

def admin_headers():
    return {"X-Admin-Key": "test-admin"}

def test_pending_deposit_does_not_change_balance_and_approval_does():
    reg("buyer", "buyer-deposit@example.com")
    token, aid = login("buyer-deposit@example.com")
    r = client.post("/api/v1/wallet/deposit-request?token="+token,
                    json={"account_id": aid, "amount": 1000, "currency": "USD"},
                    headers={"Idempotency-Key": "dep-1"})
    assert r.status_code == 200
    assert client.get(f"/api/v1/wallet/{aid}?token={token}").json()["available"] == 0.0
    rid = r.json()["request_id"]
    r = client.post(f"/api/v1/admin/wallet-requests/{rid}/approve", headers=admin_headers())
    assert r.status_code == 200
    w = client.get(f"/api/v1/wallet/{aid}?token={token}").json()
    assert w["balance_cents"] == 100000

def test_withdrawal_cannot_exceed_available_and_rejection_releases_hold():
    reg("buyer", "buyer-withdraw@example.com")
    token, aid = login("buyer-withdraw@example.com")
    r = client.post("/api/v1/wallet/deposit-request?token="+token,
                    json={"account_id": aid, "amount": 1000, "currency": "USD"},
                    headers={"Idempotency-Key": "dep-2"})
    rid = r.json()["request_id"]
    assert client.post(f"/api/v1/admin/wallet-requests/{rid}/approve", headers=admin_headers()).status_code == 200
    too_much = client.post("/api/v1/wallet/withdraw-request?token="+token,
                            json={"account_id": aid, "amount": 10000, "currency": "USD"},
                            headers={"Idempotency-Key": "wd-too-much"})
    assert too_much.status_code == 400
    r = client.post("/api/v1/wallet/withdraw-request?token="+token,
                    json={"account_id": aid, "amount": 400, "currency": "USD"},
                    headers={"Idempotency-Key": "wd-1"})
    assert r.status_code == 200
    w = client.get(f"/api/v1/wallet/{aid}?token={token}").json()
    assert w["available_cents"] == 60000 and w["held_cents"] == 40000
    assert client.post(f"/api/v1/admin/wallet-requests/{r.json()['request_id']}/reject", headers=admin_headers()).status_code == 200
    w = client.get(f"/api/v1/wallet/{aid}?token={token}").json()
    assert w["available_cents"] == 100000 and w["held_cents"] == 0

def test_payment_is_balanced_and_idempotent():
    reg("buyer", "buyer-pay@example.com")
    reg("seller", "seller-pay@example.com")
    bt, bid = login("buyer-pay@example.com")
    st, sid = login("seller-pay@example.com")
    r = client.post("/api/v1/wallet/deposit-request?token="+bt,
                    json={"account_id": bid, "amount": 500, "currency": "USD"},
                    headers={"Idempotency-Key": "dep-pay"})
    assert client.post(f"/api/v1/admin/wallet-requests/{r.json()['request_id']}/approve", headers=admin_headers()).status_code == 200
    headers={"Idempotency-Key":"pay-1"}
    r = client.post("/api/v1/wallet/pay?token="+bt,
                    json={"from_account_id": bid, "to_account_id": sid, "amount": 125, "currency":"USD"},
                    headers=headers)
    assert r.status_code == 200, r.text
    first=r.json()["reference"]
    r2 = client.post("/api/v1/wallet/pay?token="+bt,
                     json={"from_account_id": bid, "to_account_id": sid, "amount": 125, "currency":"USD"},
                     headers=headers)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    rec=client.get("/api/v1/finance/reconciliation").json()
    assert rec["ok"] is True, rec

def test_marketplace_payment_escrow_release_and_refund():
    reg("buyer", "buyer-market@example.com")
    reg("seller", "seller-market@example.com")
    bt, bid = login("buyer-market@example.com")
    st, sid = login("seller-market@example.com")

    for email, token, aid, key in [
        ("buyer-market@example.com", bt, bid, "dep-bm"),
    ]:
        r=client.post("/api/v1/wallet/deposit-request?token="+token,
                      json={"account_id":aid,"amount":1000,"currency":"USD"},
                      headers={"Idempotency-Key":key})
        assert client.post(f"/api/v1/admin/wallet-requests/{r.json()['request_id']}/approve", headers=admin_headers()).status_code==200

    for token, payload in [
        (bt, {"document_type":"passport","identity_country":"US"}),
        (st, {"document_type":"passport","identity_country":"US"}),
    ]:
        assert client.post("/api/v1/compliance/kyc?token="+token, json=payload).status_code==200

    rows=db()
    cases=rows.execute("SELECT id FROM kyc_cases ORDER BY created_at").fetchall()
    rows.close()
    for c in cases:
        assert client.post(f"/api/v1/admin/compliance/kyc/{c['id']}/approve", headers=admin_headers()).status_code==200

    r=client.post("/api/v1/listings?token="+st, json={
        "seller_id":sid,"category":"equipment","title":"Test Item","description":"test","amount":200,"currency":"USD"})
    assert r.status_code==200
    lid=r.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish?token={st}").status_code==200
    r=client.post("/api/v1/offers?token="+bt, json={"listing_id":lid,"buyer_id":bid,"amount":200,"currency":"USD"})
    assert r.status_code==200
    r=client.post(f"/api/v1/offers/{r.json()['id']}/accept?token={st}")
    assert r.status_code==200
    oid=r.json()["order_id"]
    r=client.post(f"/api/v1/orders/{oid}/pay?token={bt}", headers={"Idempotency-Key":"order-pay-1"})
    assert r.status_code==200, r.text
    assert r.json()["seller_net"]==200.0
    r=client.post(f"/api/v1/admin/orders/{oid}/escrow/release", headers=admin_headers())
    assert r.status_code==200, r.text
    rec=client.get("/api/v1/finance/reconciliation").json()
    assert rec["ok"] is True, rec


def test_manager_can_publish_without_finance_permissions():
    reg("seller", "manager-seller@example.com")
    st, sid = login("manager-seller@example.com")
    reg("buyer", "listing-manager@example.com")
    mt, mid = login("listing-manager@example.com")
    assert client.post(f"/api/v1/admin/accounts/{mid}/role", json={"role":"manager"}, headers=admin_headers()).status_code == 200
    me=client.get(f"/api/v1/auth/me?token={mt}").json()
    assert me["role"]=="manager"
    assert me["permissions"]["publish_listings"] is True
    assert me["permissions"]["financial_admin"] is False
    r=client.post("/api/v1/listings?token="+st, json={
        "seller_id":sid,"category":"services","title":"Manager publication test",
        "description":"Complete listing for manager review","amount":250,"currency":"USD"})
    assert r.status_code==200
    lid=r.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish?token={mt}").status_code==403
    r=client.post(f"/api/v1/manager/listings/{lid}/publish?token={mt}")
    assert r.status_code==200, r.text
    assert r.json()["status"]=="published"

def test_external_wallet_link_bsc_rejects_non_evm_and_accepts_public_address():
    reg("buyer-bsc", "wallet-bsc@example.com")
    token, aid = login("wallet-bsc@example.com")
    c=db()
    c.execute("UPDATE accounts SET email_verified_at=? WHERE id=?", ("2026-10-03T00:00:00+00:00", aid))
    c.commit(); c.close()
    bad={"asset":"USDT","network":"BEP20","address":"TNotAnEvmAddress123","label":"External Wallet"}
    assert client.post("/api/v1/crypto/external-address?token="+token, json=bad).status_code==400
    good={"asset":"USDT","network":"BEP20","address":"0x2222222222222222222222222222222222222222","label":"External Wallet"}
    r=client.post("/api/v1/crypto/external-address?token="+token, json=good)
    assert r.status_code==200, r.text
    assert r.json()["network"]=="BEP20"
    assert r.json()["address"]==good["address"]
    r2=client.post("/api/v1/crypto/external-address?token="+token, json=good)
    assert r2.status_code==200 and r2.json()["duplicate"] is True

def test_external_wallet_link_requires_email_verification():
    reg("buyer", "wallet-link@example.com")
    token, aid = login("wallet-link@example.com")
    payload={"asset":"USDT","network":"BEP20","address":"0x1111111111111111111111111111111111111111","label":"External Wallet"}
    assert client.post("/api/v1/crypto/external-address?token="+token, json=payload).status_code==403
    c=db()
    c.execute("UPDATE accounts SET email_verified_at=? WHERE id=?",("2026-10-03T00:00:00+00:00",aid))
    c.commit(); c.close()
    r=client.post("/api/v1/crypto/external-address?token="+token, json=payload)
    assert r.status_code==200, r.text
    rows=client.get("/api/v1/crypto/external-addresses?token="+token)
    assert rows.status_code==200
    assert any(x["address"]==payload["address"] for x in rows.json()["addresses"])


def test_external_wallet_public_config_is_safe_and_bsc_only():
    response = client.get("/api/v1/config/external-wallet")
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "Reown"
    assert data["network"] == "BSC"
    assert data["chain_id"] == 56
    assert data["asset"] == "USDT"
    assert data["transfers_enabled"] is False
    assert "private_key" not in data
    assert "seed_phrase" not in data


def test_external_wallet_link_then_marketplace_buy_sell_stays_internal():
    reg("buyer", "buyer-external-e2e@example.com")
    reg("seller", "seller-external-e2e@example.com")
    bt, bid = login("buyer-external-e2e@example.com")
    st, sid = login("seller-external-e2e@example.com")

    c=db()
    c.execute("UPDATE accounts SET email_verified_at=? WHERE id IN (?,?)",
              ("2026-10-03T00:00:00+00:00", bid, sid))
    c.commit(); c.close()

    wallet_payload={"asset":"USDT","network":"BEP20",
                    "address":"0x3333333333333333333333333333333333333333",
                    "label":"External Wallet"}
    linked=client.post("/api/v1/crypto/external-address?token="+bt, json=wallet_payload)
    assert linked.status_code == 200, linked.text

    for token, payload in [
        (bt, {"document_type":"passport","identity_country":"US"}),
        (st, {"document_type":"passport","identity_country":"US"}),
    ]:
        assert client.post("/api/v1/compliance/kyc?token="+token, json=payload).status_code == 200

    rows=db()
    cases=rows.execute("SELECT id FROM kyc_cases ORDER BY created_at").fetchall()
    rows.close()
    for case in cases:
        client.post(f"/api/v1/admin/compliance/kyc/{case['id']}/approve", headers=admin_headers())

    r=client.post("/api/v1/listings?token="+st, json={
        "seller_id":sid,"category":"equipment","title":"External wallet E2E item",
        "description":"integration test","amount":150,"currency":"USD"})
    assert r.status_code == 200, r.text
    lid=r.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish?token={st}").status_code == 200

    r=client.post("/api/v1/offers?token="+bt,
                  json={"listing_id":lid,"buyer_id":bid,"amount":150,"currency":"USD"})
    assert r.status_code == 200, r.text
    r=client.post(f"/api/v1/offers/{r.json()['id']}/accept?token={st}")
    assert r.status_code == 200, r.text
    oid=r.json()["order_id"]

    # External Wallet is an address-link layer only; marketplace settlement remains internal.
    r=client.post(f"/api/v1/orders/{oid}/pay?token={bt}",
                  headers={"Idempotency-Key":"external-wallet-e2e-pay"})
    assert r.status_code == 200, r.text
    assert r.json()["seller_net"] == 150.0
    assert client.post(f"/api/v1/admin/orders/{oid}/escrow/release",
                       headers=admin_headers()).status_code == 200
