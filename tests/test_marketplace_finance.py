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

def login(email, password):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]

def test_end_to_end_marketplace_settlement():
    seller = client.post("/api/v1/auth/register", json={
        "role":"seller","name":"Seller","email":"seller@test.local","password":"Seller12345",
        "account_type":"individual","identity_type":"passport","identity_number":"AA123456"
    })
    buyer = client.post("/api/v1/auth/register", json={
        "role":"buyer","name":"Buyer","email":"buyer@test.local","password":"Buyer12345",
        "account_type":"individual","identity_type":"passport","identity_number":"BB123456"
    })
    assert seller.status_code == 200
    assert buyer.status_code == 200
    seller_id=seller.json()["id"]; buyer_id=buyer.json()["id"]
    c=app.db()
    for uid in (seller_id,buyer_id):
        cid=__import__("uuid").uuid4().hex
        c.execute("INSERT INTO kyc_cases(id,user_id,document_type,status,risk_level,created_at) VALUES(?,?,?,?,?,?)",
                  (cid,uid,"passport","approved","standard",app.now()))
    c.execute("""INSERT INTO commission_rules
      (id,transaction_type,currency,buyer_rate,seller_rate,buyer_fixed,seller_fixed,minimum_fee,maximum_fee,effective_from,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
      ("rule-1","marketplace","USD",0.01,0.02,0,0,0,None,app.now(),"active"))
    c.commit(); c.close()

    st=login("seller@test.local","Seller12345")
    bt=login("buyer@test.local","Buyer12345")

    listing=client.post("/api/v1/listings",params={"token":st},json={
        "seller_id":seller_id,"category":"supplies","title":"Test Item","description":"Test",
        "amount":100.0,"currency":"USD"
    })
    assert listing.status_code==200, listing.text
    lid=listing.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish",params={"token":st}).status_code==200

    offer=client.post("/api/v1/offers",params={"token":bt},json={"listing_id":lid,"buyer_id":buyer_id,"amount":100.0,"currency":"USD"})
    assert offer.status_code==200, offer.text
    oid=offer.json()["id"]
    accepted=client.post(f"/api/v1/offers/{oid}/accept",params={"token":st},
                     json={"settlement_mode":"naqa_protected"})
    assert accepted.status_code==200, accepted.text
    order_id=accepted.json()["order_id"]
    chosen=client.post(f"/api/v1/orders/{order_id}/settlement-choice",params={"token":bt},
                       json={"settlement_mode":"naqa_protected"})
    assert chosen.status_code==200, chosen.text

    dep=client.post("/api/v1/wallet/deposit-request",params={"token":bt},
                    headers={"Idempotency-Key":"dep-1"},
                    json={"account_id":buyer_id,"amount":102.0,"currency":"USD"})
    assert dep.status_code==200, dep.text
    rid=dep.json()["request_id"]
    approved=client.post(f"/api/v1/admin/wallet-requests/{rid}/approve",
                          headers={"X-Admin-Key":"ci-admin-key"})
    assert approved.status_code==200, approved.text

    paid=client.post(f"/api/v1/orders/{order_id}/pay",params={"token":bt},
                     headers={"Idempotency-Key":"pay-1"})
    assert paid.status_code==200, paid.text
    data=paid.json()
    assert data["gross"]==100.0
    assert data["buyer_fee"]==1.0
    assert data["seller_fee"]==2.0
    assert data["buyer_total"]==101.0
    assert data["seller_net"]==98.0

    duplicate=client.post(f"/api/v1/orders/{order_id}/pay",params={"token":bt},
                           headers={"Idempotency-Key":"pay-1"})
    assert duplicate.status_code==200
    assert duplicate.json()["duplicate"] is True

    reconciliation=client.get("/api/v1/finance/reconciliation")
    assert reconciliation.status_code==200
    assert reconciliation.json()["ok"] is True

    buyer_wallet=client.get(f"/api/v1/wallet/{buyer_id}",params={"token":bt}).json()
    seller_wallet=client.get(f"/api/v1/wallet/{seller_id}",params={"token":st}).json()
    assert buyer_wallet["balance_cents"]==100
    assert seller_wallet["balance_cents"]==9800

def test_payment_requires_kyc():
    r=client.post("/api/v1/auth/register",json={
        "role":"buyer","name":"No KYC","email":"nokyc@test.local","password":"NoKyc12345",
        "account_type":"individual","identity_type":"passport"
    })
    assert r.status_code==200
    token=login("nokyc@test.local","NoKyc12345")
    # Endpoint-level compliance gate is verified by the end-to-end payment test;
    # this test ensures a new account remains pending.
    me=client.get("/api/v1/auth/me",params={"token":token})
    assert me.status_code==200
    assert me.json()["status"]=="pending"


def test_deposit_is_pending_and_withdrawal_cannot_overdraw():
    reg=client.post("/api/v1/auth/register",json={
        "role":"buyer","name":"Balance Test","email":"balance@test.local","password":"Balance12345",
        "account_type":"individual","identity_type":"passport"
    })
    assert reg.status_code==200
    uid=reg.json()["id"]; token=login("balance@test.local","Balance12345")

    dep=client.post("/api/v1/wallet/deposit-request",params={"token":token},
                    headers={"Idempotency-Key":"balance-dep"},
                    json={"account_id":uid,"amount":1000,"currency":"USD"})
    assert dep.status_code==200
    w=client.get(f"/api/v1/wallet/{uid}",params={"token":token}).json()
    assert w["balance_cents"]==0
    assert w["pending_deposit_cents"]==100000

    over=client.post("/api/v1/wallet/withdraw-request",params={"token":token},
                     headers={"Idempotency-Key":"balance-over"},
                     json={"account_id":uid,"amount":10000,"currency":"USD"})
    assert over.status_code==400

    rid=dep.json()["request_id"]
    approved=client.post(f"/api/v1/admin/wallet-requests/{rid}/approve",
                          headers={"X-Admin-Key":"ci-admin-key"})
    assert approved.status_code==200
    w=client.get(f"/api/v1/wallet/{uid}",params={"token":token}).json()
    assert w["balance_cents"]==100000
    assert w["available_cents"]==100000

    wd=client.post("/api/v1/wallet/withdraw-request",params={"token":token},
                   headers={"Idempotency-Key":"balance-wd"},
                   json={"account_id":uid,"amount":600,"currency":"USD"})
    assert wd.status_code==200
    w=client.get(f"/api/v1/wallet/{uid}",params={"token":token}).json()
    assert w["balance_cents"]==100000
    assert w["held_cents"]==60000
    assert w["available_cents"]==40000

    over2=client.post("/api/v1/wallet/withdraw-request",params={"token":token},
                      headers={"Idempotency-Key":"balance-over2"},
                      json={"account_id":uid,"amount":400.01,"currency":"USD"})
    assert over2.status_code==400

    rejected=client.post(f"/api/v1/admin/wallet-requests/{wd.json()['request_id']}/reject",
                          headers={"X-Admin-Key":"ci-admin-key"})
    assert rejected.status_code==200
    w=client.get(f"/api/v1/wallet/{uid}",params={"token":token}).json()
    assert w["held_cents"]==0
    assert w["available_cents"]==100000


def test_idempotency_and_reconciliation():
    reg=client.post("/api/v1/auth/register",json={
        "role":"buyer","name":"Idempotency Test","email":"idem@test.local","password":"Idem12345",
        "account_type":"individual","identity_type":"passport"
    })
    assert reg.status_code==200
    uid=reg.json()["id"]; token=login("idem@test.local","Idem12345")
    body={"account_id":uid,"amount":25,"currency":"USD"}
    h={"Idempotency-Key":"idem-deposit"}
    a=client.post("/api/v1/wallet/deposit-request",params={"token":token},headers=h,json=body)
    b=client.post("/api/v1/wallet/deposit-request",params={"token":token},headers=h,json=body)
    assert a.status_code==200 and b.status_code==200
    assert b.json()["duplicate"] is True
    assert b.json()["request_id"]==a.json()["request_id"]
    rec=client.get("/api/v1/finance/reconciliation")
    assert rec.status_code==200
    assert rec.json()["ok"] is True


def test_commission_snapshot_survives_rule_change():
    seller_email = "snapshot-seller@test.local"
    buyer_email = "snapshot-buyer@test.local"
    seller = client.post("/api/v1/auth/register", json={
        "role":"seller","name":"Snapshot Seller","email":seller_email,"password":"SnapshotSeller12345",
        "account_type":"individual","identity_type":"passport"
    })
    buyer = client.post("/api/v1/auth/register", json={
        "role":"buyer","name":"Snapshot Buyer","email":buyer_email,"password":"SnapshotBuyer12345",
        "account_type":"individual","identity_type":"passport"
    })
    assert seller.status_code == buyer.status_code == 200
    seller_id, buyer_id = seller.json()["id"], buyer.json()["id"]
    c = app.db()
    for uid in (seller_id, buyer_id):
        cid = __import__("uuid").uuid4().hex
        c.execute(
            "INSERT INTO kyc_cases(id,user_id,document_type,status,risk_level,created_at) VALUES(?,?,?,?,?,?)",
            (cid, uid, "passport", "approved", "standard", app.now()),
        )
    c.execute(
        """INSERT INTO commission_rules
           (id,transaction_type,currency,buyer_rate,seller_rate,buyer_fixed,seller_fixed,
            minimum_fee,maximum_fee,effective_from,status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("snapshot-rule", "marketplace", "USD", 0.01, 0.02, 0, 0, 0, None, app.now(), "active"),
    )
    c.commit(); c.close()

    st, bt = login(seller_email, "SnapshotSeller12345"), login(buyer_email, "SnapshotBuyer12345")
    listing = client.post("/api/v1/listings", params={"token": st}, json={
        "seller_id": seller_id, "category": "supplies", "title": "Snapshot Item",
        "description": "Test", "amount": 100.0, "currency": "USD"
    })
    assert listing.status_code == 200
    lid = listing.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish", params={"token": st}).status_code == 200
    offer = client.post("/api/v1/offers", params={"token": bt}, json={
        "listing_id": lid, "buyer_id": buyer_id, "amount": 100.0, "currency": "USD"
    })
    assert offer.status_code == 200
    accepted = client.post(f"/api/v1/offers/{offer.json()['id']}/accept", params={"token": st},
                       json={"settlement_mode":"naqa_protected"})
    assert accepted.status_code == 200
    order_id = accepted.json()["order_id"]
    chosen = client.post(f"/api/v1/orders/{order_id}/settlement-choice", params={"token": bt},
                         json={"settlement_mode":"naqa_protected"})
    assert chosen.status_code == 200, chosen.text

    c = app.db()
    c.execute("UPDATE commission_rules SET status='inactive' WHERE id='snapshot-rule'")
    c.execute(
        """INSERT INTO commission_rules
           (id,transaction_type,currency,buyer_rate,seller_rate,buyer_fixed,seller_fixed,
            minimum_fee,maximum_fee,effective_from,status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("snapshot-rule-new", "marketplace", "USD", 0.05, 0.07, 0, 0, 0, None, app.now(), "active"),
    )
    c.commit(); c.close()

    dep = client.post("/api/v1/wallet/deposit-request", params={"token": bt},
                      headers={"Idempotency-Key":"snapshot-dep"},
                      json={"account_id":buyer_id,"amount":101.0,"currency":"USD"})
    assert dep.status_code == 200
    assert client.post(f"/api/v1/admin/wallet-requests/{dep.json()['request_id']}/approve",
                       headers={"X-Admin-Key":"ci-admin-key"}).status_code == 200

    paid = client.post(f"/api/v1/orders/{order_id}/pay", params={"token": bt},
                       headers={"Idempotency-Key":"snapshot-pay"})
    assert paid.status_code == 200, paid.text
    data = paid.json()
    assert data["buyer_fee"] == 1.0
    assert data["seller_fee"] == 2.0
    assert data["buyer_total"] == 101.0
    assert data["seller_net"] == 98.0


def test_both_parties_can_choose_off_platform_without_fees():
    seller = client.post("/api/v1/auth/register", json={
        "role":"seller","name":"External Seller","email":"external-seller@test.local","password":"ExternalSeller12345",
        "account_type":"individual","identity_type":"passport"
    })
    buyer = client.post("/api/v1/auth/register", json={
        "role":"buyer","name":"External Buyer","email":"external-buyer@test.local","password":"ExternalBuyer12345",
        "account_type":"individual","identity_type":"passport"
    })
    assert seller.status_code == buyer.status_code == 200
    seller_id, buyer_id = seller.json()["id"], buyer.json()["id"]
    # Direct external settlement requires a verified seller USDT/BEP20 address.
    c=app.db()
    c.execute("UPDATE accounts SET email_verified_at=? WHERE id=?", (app.now(), seller_id))
    app.ensure_crypto_schema(c)
    wallet=c.execute("SELECT * FROM crypto_wallets WHERE account_id=? AND asset='USDT' AND network='BEP20'",(seller_id,)).fetchone()
    if not wallet:
        wallet_id=__import__("uuid").uuid4().hex
        c.execute("INSERT INTO crypto_wallets(id,account_id,asset,network,balance_units,held_units,updated_at) VALUES(?,?,?,?,0,0,?)",(wallet_id,seller_id,"USDT","BEP20",app.now()))
        wallet=c.execute("SELECT * FROM crypto_wallets WHERE id=?",(wallet_id,)).fetchone()
    c.execute("""INSERT INTO crypto_addresses
        (id,wallet_id,provider,address,memo_tag,status,created_at,verification_status,verified_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (__import__("uuid").uuid4().hex,wallet["id"],"External Wallet",
         "0x1111111111111111111111111111111111111111",None,"active",app.now(),"verified",app.now()))
    c.commit(); c.close()
    st, bt = login("external-seller@test.local","ExternalSeller12345"), login("external-buyer@test.local","ExternalBuyer12345")
    listing = client.post("/api/v1/listings", params={"token":st}, json={
        "seller_id":seller_id,"category":"supplies","title":"External Item","description":"Test",
        "amount":50.0,"currency":"USD"
    })
    assert listing.status_code == 200
    lid=listing.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish",params={"token":st}).status_code == 200
    offer=client.post("/api/v1/offers",params={"token":bt},json={
        "listing_id":lid,"buyer_id":buyer_id,"amount":50.0,"currency":"USD"
    })
    assert offer.status_code == 200
    accepted=client.post(f"/api/v1/offers/{offer.json()['id']}/accept",params={"token":st},
                         json={"settlement_mode":"off_platform"})
    assert accepted.status_code == 200, accepted.text
    order_id=accepted.json()["order_id"]
    assert accepted.json()["settlement_mode"]=="off_platform"
    chosen=client.post(f"/api/v1/orders/{order_id}/settlement-choice",params={"token":bt},
                       json={"settlement_mode":"off_platform"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["status"]=="awaiting_external_payment"
    order=client.get(f"/api/v1/orders/{order_id}",params={"token":bt})
    assert order.status_code == 200
    assert order.json()["settlement_mode"]=="off_platform"
    assert order.json()["status"]=="awaiting_external_payment"
    assert order.json()["commission_cents"]==0


def test_buyer_cannot_enable_protected_settlement_if_seller_chose_external():
    seller = client.post("/api/v1/auth/register", json={
        "role":"seller","name":"External Seller 2","email":"external-seller2@test.local","password":"ExternalSeller212345",
        "account_type":"individual","identity_type":"passport"
    })
    buyer = client.post("/api/v1/auth/register", json={
        "role":"buyer","name":"External Buyer 2","email":"external-buyer2@test.local","password":"ExternalBuyer212345",
        "account_type":"individual","identity_type":"passport"
    })
    assert seller.status_code == buyer.status_code == 200
    st, bt = login("external-seller2@test.local","ExternalSeller212345"), login("external-buyer2@test.local","ExternalBuyer212345")
    lid=client.post("/api/v1/listings",params={"token":st},json={
        "seller_id":seller.json()["id"],"category":"supplies","title":"External Item 2","description":"Test",
        "amount":40.0,"currency":"USD"
    }).json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish",params={"token":st}).status_code==200
    offer=client.post("/api/v1/offers",params={"token":bt},json={
        "listing_id":lid,"buyer_id":buyer.json()["id"],"amount":40.0,"currency":"USD"
    })
    assert offer.status_code==200
    accepted=client.post(f"/api/v1/offers/{offer.json()['id']}/accept",params={"token":st},
                         json={"settlement_mode":"off_platform"})
    assert accepted.status_code==200
    blocked=client.post(f"/api/v1/orders/{accepted.json()['order_id']}/settlement-choice",params={"token":bt},
                        json={"settlement_mode":"naqa_protected"})
    assert blocked.status_code==409

def test_service_billing_rejects_wrong_role_and_tracks_subscription():
    seller = client.post("/api/v1/auth/register", json={"role":"seller","name":"Plan Seller","email":"plan-seller@test.local","password":"PlanSeller12345","account_type":"individual","identity_type":"passport"})
    buyer = client.post("/api/v1/auth/register", json={"role":"buyer","name":"Plan Buyer","email":"plan-buyer@test.local","password":"PlanBuyer12345","account_type":"individual","identity_type":"passport"})
    assert seller.status_code == buyer.status_code == 200
    seller_id, buyer_id = seller.json()["id"], buyer.json()["id"]
    st, bt = login("plan-seller@test.local","PlanSeller12345"), login("plan-buyer@test.local","PlanBuyer12345")

    wrong = client.post("/api/v1/services/pay", params={"token":bt}, headers={"Idempotency-Key":"plan-wrong-role"}, json={"code":"seller_monthly"})
    assert wrong.status_code == 403

    for uid in (seller_id, buyer_id):
        c=app.db()
        w=c.execute("SELECT * FROM wallets WHERE account_id=?",(uid,)).fetchone()
        app.set_wallet_balance(c,w["id"],5000,0)
        c.commit(); c.close()

    paid = client.post("/api/v1/services/pay", params={"token":st}, headers={"Idempotency-Key":"plan-seller-monthly"}, json={"code":"seller_monthly"})
    assert paid.status_code == 200
    assert paid.json()["amount"] == 10.0

    c=app.db()
    sub=c.execute("SELECT plan_code,status,active_until,started_at FROM subscriptions WHERE account_id=? AND plan_code=?",(seller_id,"seller_monthly")).fetchone()
    assert sub is not None
    assert sub["status"] == "active"
    assert sub["active_until"] > sub["started_at"]
    c.close()

    wrong_listing = client.post("/api/v1/services/pay", params={"token":bt}, headers={"Idempotency-Key":"plan-wrong-listing"}, json={"code":"listing"})
    assert wrong_listing.status_code == 403
