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
    return {
        "wallet_id":w["id"], "account_id":account_id, "currency":w["currency"],
        "balance_cents":balance, "held_cents":held, "available_cents":balance-held,
        "balance":float(amount(balance)), "held":float(amount(held)), "available":float(amount(balance-held)),
        "wallet_only":True
    }
