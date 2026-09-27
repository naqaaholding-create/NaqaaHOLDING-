import sqlite3
from decimal import Decimal
from finance_ledger import ensure_schema, post_entry, set_wallet_balance, wallet_snapshot
from finance_rules import fee_amount, split_settlement

def test_double_entry_and_available_balance():
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
    c.executescript("""
    CREATE TABLE wallets(id TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,currency TEXT NOT NULL DEFAULT 'USD',balance REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
    CREATE TABLE wallet_requests(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,reference TEXT,created_at TEXT NOT NULL);
    """)
    ensure_schema(c)
    c.execute("INSERT INTO wallets(id,account_id,currency,balance,updated_at,balance_cents,held_cents) VALUES('w1','u1','USD',1000,'now',100000,0)")
    ensure_schema(c)
    snap=wallet_snapshot(c,"u1")
    assert snap["available_cents"]==100000
    set_wallet_balance(c,"w1",100000,25000)
    snap=wallet_snapshot(c,"u1")
    assert snap["available_cents"]==75000
    try: set_wallet_balance(c,"w1",100000,-1); assert False
    except ValueError: pass
    c.close()

def test_ledger_must_balance():
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
    c.executescript("""
    CREATE TABLE wallets(id TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,currency TEXT NOT NULL DEFAULT 'USD',balance REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
    CREATE TABLE wallet_requests(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,reference TEXT,created_at TEXT NOT NULL);
    """)
    ensure_schema(c)
    post_entry(c,"T-1","test",[{"account_id":"SYSTEM:CASH","side":"debit","amount_cents":1000},{"account_id":"WALLET:w1","side":"credit","amount_cents":1000}])
    d=c.execute("SELECT SUM(amount_cents) FROM journal_lines WHERE side='debit'").fetchone()[0]
    cr=c.execute("SELECT SUM(amount_cents) FROM journal_lines WHERE side='credit'").fetchone()[0]
    assert d==cr==1000
    c.close()

def test_commission_math():
    s=split_settlement("1000","2","1")
    assert s["buyer_fee"]==Decimal("20.00")
    assert s["seller_fee"]==Decimal("10.00")
    assert s["seller_net"]==Decimal("990.00")
    assert fee_amount("1000","2.5")==Decimal("25.00")


def test_api_wallet_safety_and_payment():
    import os, uuid
    from fastapi.testclient import TestClient
    from app import app
    client=TestClient(app)

    buyer_email=f"buyer-{uuid.uuid4().hex}@example.com"
    seller_email=f"seller-{uuid.uuid4().hex}@example.com"
    assert client.post("/api/v1/auth/register",json={"role":"buyer","name":"Buyer","email":buyer_email,"password":"StrongPass123!"}).status_code==200
    assert client.post("/api/v1/auth/register",json={"role":"seller","name":"Seller","email":seller_email,"password":"StrongPass123!"}).status_code==200

    def tok(email):
        r=client.post("/api/v1/auth/login",json={"email":email,"password":"StrongPass123!"})
        assert r.status_code==200
        return r.json()["token"]

    bt,st=tok(buyer_email),tok(seller_email)
    bid=client.get("/api/v1/auth/me",params={"token":bt}).json()["id"]
    sid=client.get("/api/v1/auth/me",params={"token":st}).json()["id"]

    dep=client.post("/api/v1/wallet/deposit-request",params={"token":bt},
        headers={"Idempotency-Key":"api-dep-1"},
        json={"account_id":bid,"amount":1000,"currency":"USD"})
    assert dep.status_code==200
    rid=dep.json()["request_id"]
    assert client.get(f"/api/v1/wallet/{bid}",params={"token":bt}).json()["balance_cents"]==0

    assert client.post(f"/api/v1/admin/wallet-requests/{rid}/approve",
        headers={"X-Admin-Key":"test-admin"}).status_code==200
    assert client.get(f"/api/v1/wallet/{bid}",params={"token":bt}).json()["balance_cents"]==100000

    too_much=client.post("/api/v1/wallet/withdraw-request",params={"token":bt},
        headers={"Idempotency-Key":"api-wd-too-much"},
        json={"account_id":bid,"amount":1001,"currency":"USD"})
    assert too_much.status_code==400

    pay=client.post("/api/v1/wallet/pay",params={"token":bt},
        headers={"Idempotency-Key":"api-pay-1"},
        json={"from_account_id":bid,"to_account_id":sid,"amount":250,"currency":"USD"})
    assert pay.status_code==200, pay.text
    assert client.get(f"/api/v1/wallet/{bid}",params={"token":bt}).json()["available_cents"]==75000
    assert client.get(f"/api/v1/wallet/{sid}",params={"token":st}).json()["balance_cents"]==25000

    duplicate=client.post("/api/v1/wallet/pay",params={"token":bt},
        headers={"Idempotency-Key":"api-pay-1"},
        json={"from_account_id":bid,"to_account_id":sid,"amount":250,"currency":"USD"})
    assert duplicate.status_code==200
    assert duplicate.json()["duplicate"] is True

    rec=client.get("/api/v1/finance/reconciliation")
    assert rec.status_code==200
    assert rec.json()["ok"] is True
