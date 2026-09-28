import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "security.db"))
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "security-admin")
    monkeypatch.setenv("REAL_MONEY_ENABLED", "0")
    import app
    importlib.reload(app)
    client = TestClient(app.app)
    return app, client


def register(client, email, role):
    r = client.post("/api/v1/auth/register", json={
        "role": role,
        "name": role.title(),
        "email": email,
        "password": "StrongPass123",
        "account_type": "individual",
        "identity_type": "passport",
        "identity_number": "SEC123456",
    })
    assert r.status_code == 200, r.text
    token = client.post("/api/v1/auth/login", json={
        "email": email, "password": "StrongPass123"
    }).json()["token"]
    return r.json()["id"], token


def approve_kyc(app, user_id):
    c = app.db()
    cid = __import__("uuid").uuid4().hex
    c.execute(
        "INSERT INTO kyc_cases(id,user_id,document_type,status,risk_level,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (cid, user_id, "passport", "approved", "standard", app.now()),
    )
    c.commit()
    c.close()


def add_commission(app, buyer_bps=100, seller_bps=200):
    c = app.db()
    c.execute(
        """INSERT INTO commission_rules
        (id,transaction_type,currency,buyer_rate,seller_rate,buyer_fixed,seller_fixed,
         minimum_fee,maximum_fee,effective_from,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            __import__("uuid").uuid4().hex, "marketplace", "USD",
            buyer_bps / 10000, seller_bps / 10000, 0, 0, 0, None,
            app.now(), "active",
        ),
    )
    c.commit()
    c.close()


def fund(client, account_id, token, admin_key="security-admin"):
    dep = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "fund-" + account_id},
        json={"account_id": account_id, "amount": 250, "currency": "USD"},
    )
    assert dep.status_code == 200, dep.text
    approved = client.post(
        f"/api/v1/admin/wallet-requests/{dep.json()['request_id']}/approve",
        headers={"X-Admin-Key": admin_key},
    )
    assert approved.status_code == 200, approved.text


def make_order(ctx, buyer_id, buyer_token, seller_id, seller_token, amount=100):
    app, client = ctx
    listing = client.post(
        "/api/v1/listings",
        params={"token": seller_token},
        json={
            "seller_id": seller_id, "category": "supplies",
            "title": "Secure Item", "description": "security test",
            "amount": amount, "currency": "USD",
        },
    )
    assert listing.status_code == 200, listing.text
    lid = listing.json()["id"]
    assert client.post(
        f"/api/v1/listings/{lid}/publish", params={"token": seller_token}
    ).status_code == 200
    offer = client.post(
        "/api/v1/offers",
        params={"token": buyer_token},
        json={"listing_id": lid, "buyer_id": buyer_id, "amount": amount, "currency": "USD"},
    )
    assert offer.status_code == 200, offer.text
    accepted = client.post(
        f"/api/v1/offers/{offer.json()['id']}/accept",
        params={"token": seller_token},
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["order_id"]


def test_identity_impersonation_is_blocked(ctx):
    app, client = ctx
    seller, seller_token = register(client, "seller@security.test", "seller")
    buyer, buyer_token = register(client, "buyer@security.test", "buyer")
    other_seller, other_seller_token = register(client, "other-seller@security.test", "seller")

    forged_listing = client.post(
        "/api/v1/listings",
        params={"token": seller_token},
        json={"seller_id": other_seller, "category": "supplies", "title": "Forged", "amount": 10, "currency": "USD"},
    )
    assert forged_listing.status_code == 403

    listing = client.post(
        "/api/v1/listings",
        params={"token": seller_token},
        json={"seller_id": seller, "category": "supplies", "title": "Real", "amount": 10, "currency": "USD"},
    )
    assert listing.status_code == 200
    lid = listing.json()["id"]

    assert client.post(
        f"/api/v1/listings/{lid}/publish", params={"token": other_seller_token}
    ).status_code == 403

    forged_offer = client.post(
        "/api/v1/offers",
        params={"token": buyer_token},
        json={"listing_id": lid, "buyer_id": "forged-buyer", "amount": 10, "currency": "USD"},
    )
    assert forged_offer.status_code == 403


def test_insufficient_balance_and_kyc_gates_remain_closed(ctx):
    app, client = ctx
    seller, seller_token = register(client, "seller2@security.test", "seller")
    buyer, buyer_token = register(client, "buyer2@security.test", "buyer")
    approve_kyc(app, seller)
    approve_kyc(app, buyer)
    add_commission(app)

    order = make_order(ctx, buyer, buyer_token, seller, seller_token, amount=100)

    insufficient = client.post(
        f"/api/v1/orders/{order}/pay",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "pay-insufficient"},
    )
    assert insufficient.status_code == 400
    assert "insufficient available buyer balance" in insufficient.json()["detail"]

    fund(client, buyer, buyer_token)
    paid = client.post(
        f"/api/v1/orders/{order}/pay",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "pay-funded"},
    )
    assert paid.status_code == 200, paid.text


def test_payment_cannot_be_paid_by_another_buyer_and_idempotency_cannot_cross_orders(ctx):
    app, client = ctx
    seller, seller_token = register(client, "seller3@security.test", "seller")
    buyer, buyer_token = register(client, "buyer3@security.test", "buyer")
    other_buyer, other_buyer_token = register(client, "buyer4@security.test", "buyer")
    for uid in (seller, buyer, other_buyer):
        approve_kyc(app, uid)
    add_commission(app)
    fund(client, buyer, buyer_token)
    fund(client, other_buyer, other_buyer_token)

    order1 = make_order(ctx, buyer, buyer_token, seller, seller_token, amount=50)
    order2 = make_order(ctx, other_buyer, other_buyer_token, seller, seller_token, amount=50)

    denied = client.post(
        f"/api/v1/orders/{order1}/pay",
        params={"token": other_buyer_token},
        headers={"Idempotency-Key": "cross-order-key"},
    )
    assert denied.status_code == 403

    first = client.post(
        f"/api/v1/orders/{order1}/pay",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "shared-key"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/orders/{order2}/pay",
        params={"token": other_buyer_token},
        headers={"Idempotency-Key": "shared-key"},
    )
    assert second.status_code == 409
    assert "different order" in second.json()["detail"]

    rec = client.get("/api/v1/finance/reconciliation")
    assert rec.status_code == 200
    assert rec.json()["ok"] is True


def test_counterparty_identity_is_not_exposed(ctx):
    app, client = ctx
    seller, seller_token = register(client, "seller5@security.test", "seller")
    buyer, buyer_token = register(client, "buyer5@security.test", "buyer")
    approve_kyc(app, seller)
    approve_kyc(app, buyer)
    add_commission(app)
    fund(client, buyer, buyer_token)
    order = make_order(ctx, buyer, buyer_token, seller, seller_token, amount=25)

    buyer_view = client.get(
        f"/api/v1/orders/{order}", params={"token": buyer_token}
    )
    seller_view = client.get(
        f"/api/v1/orders/{order}", params={"token": seller_token}
    )
    assert buyer_view.status_code == 200
    assert seller_view.status_code == 200
    assert buyer_view.json()["counterparty"] == "SELLER-PRIVATE"
    assert seller_view.json()["counterparty"] == "BUYER-PRIVATE"
    assert "seller_id" not in buyer_view.json()
    assert "buyer_id" not in seller_view.json()


def test_concurrent_same_order_allows_only_one_settlement(ctx):
    import concurrent.futures

    app, client = ctx
    seller, seller_token = register(client, "seller-concurrency@security.test", "seller")
    buyer, buyer_token = register(client, "buyer-concurrency@security.test", "buyer")
    approve_kyc(app, seller)
    approve_kyc(app, buyer)
    add_commission(app, buyer_bps=100, seller_bps=200)
    fund(client, buyer, buyer_token)
    order = make_order(ctx, buyer, buyer_token, seller, seller_token, amount=100)

    def pay(i):
        local = TestClient(app.app)
        return local.post(
            f"/api/v1/orders/{order}/pay",
            params={"token": buyer_token},
            headers={"Idempotency-Key": f"concurrent-pay-{i}"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(pay, range(8)))

    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1
    assert all(code in (200, 409) for code in codes)

    c = app.db()
    order_row = c.execute(
        "SELECT status,buyer_total_cents,seller_net_cents,payment_reference FROM orders WHERE id=?",
        (order,),
    ).fetchone()
    journals = c.execute(
        "SELECT COUNT(*) n FROM journal_entries WHERE entry_type='marketplace_settlement'"
    ).fetchone()["n"]
    buyer_wallet = c.execute(
        "SELECT balance_cents FROM wallets WHERE account_id=?", (buyer,)
    ).fetchone()["balance_cents"]
    seller_wallet = c.execute(
        "SELECT balance_cents FROM wallets WHERE account_id=?", (seller,)
    ).fetchone()["balance_cents"]
    c.close()

    assert order_row["status"] == "paid"
    assert journals == 1
    assert order_row["buyer_total_cents"] == 10100
    assert order_row["seller_net_cents"] == 9800
    assert buyer_wallet == 14900
    assert seller_wallet == 9800

    rec = client.get("/api/v1/finance/reconciliation")
    assert rec.status_code == 200
    assert rec.json()["ok"] is True


def test_concurrent_payments_cannot_overspend_available_balance(ctx):
    import concurrent.futures

    app, client = ctx
    seller, seller_token = register(client, "seller-overspend@security.test", "seller")
    buyer, buyer_token = register(client, "buyer-overspend@security.test", "buyer")
    approve_kyc(app, seller)
    approve_kyc(app, buyer)
    add_commission(app)
    fund(client, buyer, buyer_token)

    order1 = make_order(ctx, buyer, buyer_token, seller, seller_token, amount=120)
    order2 = make_order(ctx, buyer, buyer_token, seller, seller_token, amount=120)

    def pay(order_id, i):
        local = TestClient(app.app)
        return local.post(
            f"/api/v1/orders/{order_id}/pay",
            params={"token": buyer_token},
            headers={"Idempotency-Key": f"overspend-{i}"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda args: pay(*args),
            [(order1, 1), (order2, 2)],
        ))

    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1
    assert codes.count(400) == 1

    c = app.db()
    paid = c.execute("SELECT COUNT(*) n FROM orders WHERE status='paid'").fetchone()["n"]
    buyer_wallet = c.execute(
        "SELECT balance_cents FROM wallets WHERE account_id=?", (buyer,)
    ).fetchone()["balance_cents"]
    c.close()

    assert paid == 1
    assert buyer_wallet == 12800
    assert client.get("/api/v1/finance/reconciliation").json()["ok"] is True
