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
