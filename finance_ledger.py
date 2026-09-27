from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import sqlite3, uuid
from datetime import datetime, timezone

CENT = Decimal("0.01")

def now():
    return datetime.now(timezone.utc).isoformat()

def to_cents(value):
    try:
        d = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("invalid monetary amount")
    if d <= 0:
        raise ValueError("amount must be greater than zero")
    return int(d * 100)

def amount(cents):
    return Decimal(cents) / Decimal(100)

def ensure_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS ledger_accounts(
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT, currency TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS journal_entries(
      id TEXT PRIMARY KEY, reference TEXT NOT NULL UNIQUE, entry_type TEXT NOT NULL,
      description TEXT, idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS journal_lines(
      id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, ledger_account_id TEXT NOT NULL,
      side TEXT NOT NULL CHECK(side IN ('debit','credit')), amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_journal_lines_entry ON journal_lines(entry_id);
    CREATE INDEX IF NOT EXISTS idx_journal_lines_account ON journal_lines(ledger_account_id);
    """)
    for col, typ in [("balance_cents","INTEGER NOT NULL DEFAULT 0"),("held_cents","INTEGER NOT NULL DEFAULT 0")]:
        try: c.execute(f"ALTER TABLE wallets ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    for col, typ in [("idempotency_key","TEXT"),("approved_at","TEXT"),("rejection_reason","TEXT")]:
        try: c.execute(f"ALTER TABLE wallet_requests ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    rows = c.execute("SELECT id,balance,balance_cents,held_cents FROM wallets").fetchall()
    for r in rows:
        if r["balance_cents"] == 0 and float(r["balance"] or 0) != 0:
            cents = int(Decimal(str(r["balance"])) * 100)
            c.execute("UPDATE wallets SET balance_cents=?,held_cents=COALESCE(held_cents,0) WHERE id=?", (cents, r["id"]))
        c.execute("UPDATE wallets SET balance=ROUND(balance_cents/100.0,2),held_cents=COALESCE(held_cents,0) WHERE id=?", (r["id"],))
    c.executescript("""
    CREATE TABLE IF NOT EXISTS kyc_cases(
      id TEXT PRIMARY KEY,user_id TEXT NOT NULL,document_type TEXT,status TEXT NOT NULL,
      risk_level TEXT NOT NULL DEFAULT 'standard',reviewed_by TEXT,reviewed_at TEXT,rejection_reason TEXT,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kyb_cases(
      id TEXT PRIMARY KEY,user_id TEXT NOT NULL,company_name TEXT,registration_number TEXT,status TEXT NOT NULL,
      risk_level TEXT NOT NULL DEFAULT 'standard',reviewed_by TEXT,reviewed_at TEXT,rejection_reason TEXT,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS commission_rules(
      id TEXT PRIMARY KEY,transaction_type TEXT NOT NULL,currency TEXT NOT NULL DEFAULT 'USD',
      buyer_rate REAL NOT NULL DEFAULT 0,seller_rate REAL NOT NULL DEFAULT 0,
      buyer_fixed REAL NOT NULL DEFAULT 0,seller_fixed REAL NOT NULL DEFAULT 0,
      minimum_fee REAL NOT NULL DEFAULT 0,maximum_fee REAL,effective_from TEXT NOT NULL,
      effective_to TEXT,status TEXT NOT NULL DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS orders(
      id TEXT PRIMARY KEY,listing_id TEXT,buyer_id TEXT NOT NULL,seller_id TEXT NOT NULL,
      gross_cents INTEGER NOT NULL,commission_cents INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS disputes(
      id TEXT PRIMARY KEY,transaction_reference TEXT NOT NULL,opened_by TEXT NOT NULL,
      amount_cents INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,
      resolution TEXT,created_at TEXT NOT NULL,resolved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit_logs(
      id TEXT PRIMARY KEY,actor_id TEXT,action TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT,
      old_value TEXT,new_value TEXT,ip TEXT,device TEXT,created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_kyc_user ON kyc_cases(user_id);
    CREATE INDEX IF NOT EXISTS idx_kyb_user ON kyb_cases(user_id);
    CREATE INDEX IF NOT EXISTS idx_commission_active ON commission_rules(transaction_type,status,effective_from);
    CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity,entity_id);
    CREATE TABLE IF NOT EXISTS commission_entries(
      id TEXT PRIMARY KEY,order_id TEXT NOT NULL,side TEXT NOT NULL CHECK(side IN ('buyer','seller')),
      amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),currency TEXT NOT NULL,rate_bps INTEGER NOT NULL DEFAULT 0,
      fixed_cents INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS escrow_transactions(
      id TEXT PRIMARY KEY,order_id TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
      currency TEXT NOT NULL,status TEXT NOT NULL,held_at TEXT NOT NULL,released_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_commission_order ON commission_entries(order_id);
    CREATE INDEX IF NOT EXISTS idx_escrow_order ON escrow_transactions(order_id);
    """)
    c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES(?,?,?,?,?)",
              ("SYSTEM:CASH","system","SYSTEM","USD",now()))
    for r in c.execute("SELECT id,account_id,currency FROM wallets").fetchall():
        ensure_wallet_ledger(c, r["account_id"], r["currency"], r["id"])
    c.commit()

def ensure_wallet_ledger(c, account_id, currency="USD", wallet_id=None):
    wid = wallet_id or c.execute("SELECT id FROM wallets WHERE account_id=?", (account_id,)).fetchone()["id"]
    lid = "WALLET:" + wid
    c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES(?,?,?,?,?)",
              (lid,"wallet",account_id,currency,now()))
    return lid

def post_entry(c, reference, entry_type, lines, description="", idempotency_key=None):
    if idempotency_key:
        old = c.execute("SELECT id,reference FROM journal_entries WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if old:
            return old["id"], old["reference"], True
    debit = sum(int(x["amount_cents"]) for x in lines if x["side"]=="debit")
    credit = sum(int(x["amount_cents"]) for x in lines if x["side"]=="credit")
    if debit <= 0 or debit != credit:
        raise ValueError("unbalanced journal entry")
    eid = str(uuid.uuid4())
    c.execute("INSERT INTO journal_entries(id,reference,entry_type,description,idempotency_key,created_at) VALUES(?,?,?,?,?,?)",
              (eid,reference,entry_type,description,idempotency_key,now()))
    for x in lines:
        if int(x["amount_cents"]) <= 0 or x["side"] not in ("debit","credit"):
            raise ValueError("invalid journal line")
        c.execute("INSERT INTO journal_lines(id,entry_id,ledger_account_id,side,amount_cents,created_at) VALUES(?,?,?,?,?,?)",
                  (str(uuid.uuid4()),eid,x["account_id"],x["side"],int(x["amount_cents"]),now()))
    return eid, reference, False

def set_wallet_balance(c, wallet_id, balance_cents, held_cents=None):
    if balance_cents < 0:
        raise ValueError("negative balance is forbidden")
    w = c.execute("SELECT held_cents FROM wallets WHERE id=?", (wallet_id,)).fetchone()
    if not w: raise ValueError("wallet not found")
    held = int(w["held_cents"] or 0) if held_cents is None else int(held_cents)
    if held < 0 or held > balance_cents:
        raise ValueError("invalid held balance")
    c.execute("UPDATE wallets SET balance_cents=?,held_cents=?,balance=ROUND(?/100.0,2),updated_at=? WHERE id=?",
              (balance_cents,held,balance_cents,now(),wallet_id))

def wallet_snapshot(c, account_id):
    w = c.execute("SELECT * FROM wallets WHERE account_id=?", (account_id,)).fetchone()
    if not w: raise ValueError("wallet not found")
    balance = int(w["balance_cents"] or 0)
    held = int(w["held_cents"] or 0)
    pending_deposit = c.execute("SELECT COALESCE(SUM(CAST(ROUND(amount*100) AS INTEGER)),0) n FROM wallet_requests WHERE account_id=? AND type='deposit' AND status='pending'", (account_id,)).fetchone()["n"]
    pending_withdraw = c.execute("SELECT COALESCE(SUM(CAST(ROUND(amount*100) AS INTEGER)),0) n FROM wallet_requests WHERE account_id=? AND type='withdraw' AND status='pending'", (account_id,)).fetchone()["n"]
    return {
        "wallet_id":w["id"], "account_id":account_id, "currency":w["currency"],
        "balance_cents":balance, "held_cents":held, "available_cents":balance-held,
        "pending_deposit_cents":int(pending_deposit or 0), "pending_withdrawal_cents":int(pending_withdraw or 0),
        "balance":float(amount(balance)), "held":float(amount(held)), "available":float(amount(balance-held)),
        "pending_deposit":float(amount(int(pending_deposit or 0))), "pending_withdrawal":float(amount(int(pending_withdraw or 0))),
        "wallet_only":True
    }
