import os, tempfile, sqlite3, unittest
os.environ["DATABASE_PATH"]=os.path.join(tempfile.gettempdir(),"naqaa_finance_test.db")
from finance_ledger import ensure_schema, post_entry, set_wallet_balance, wallet_snapshot, to_cents

class FinanceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(":memory:")
        self.c.row_factory=sqlite3.Row
        self.c.executescript("""
        CREATE TABLE wallets(
          id TEXT PRIMARY KEY, account_id TEXT UNIQUE NOT NULL, currency TEXT NOT NULL DEFAULT 'USD',
          balance REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        CREATE TABLE wallet_requests(
          id TEXT PRIMARY KEY, account_id TEXT NOT NULL, type TEXT NOT NULL, amount REAL NOT NULL,
          currency TEXT NOT NULL, status TEXT NOT NULL, reference TEXT, created_at TEXT NOT NULL
        );
        """)
        ensure_schema(self.c)
        self.c.execute("INSERT INTO wallets(id,account_id,currency,balance,updated_at,balance_cents,held_cents) VALUES('w1','u1','USD',0,'now',0,0)")
        self.c.commit()

    def tearDown(self):
        self.c.close()

    def test_cents_are_exact(self):
        self.assertEqual(to_cents("1000.00"),100000)
        self.assertEqual(to_cents("0.01"),1)

    def test_wallet_cannot_go_negative(self):
        with self.assertRaises(ValueError):
            set_wallet_balance(self.c,"w1",-1)

    def test_held_cannot_exceed_balance(self):
        with self.assertRaises(ValueError):
            set_wallet_balance(self.c,"w1",10000,10001)

    def test_balanced_journal_is_required(self):
        with self.assertRaises(ValueError):
            post_entry(self.c,"T-1","test",[
                {"account_id":"SYSTEM:CASH","side":"debit","amount_cents":1000}
            ])

    def test_idempotent_journal_does_not_duplicate(self):
        self.c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES('A','system','SYSTEM','USD','now')")
        self.c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES('B','system','SYSTEM','USD','now')")
        a=post_entry(self.c,"T-2","test",[
            {"account_id":"A","side":"debit","amount_cents":1000},
            {"account_id":"B","side":"credit","amount_cents":1000}
        ],idempotency_key="same-key")
        b=post_entry(self.c,"T-3","test",[
            {"account_id":"A","side":"debit","amount_cents":1000},
            {"account_id":"B","side":"credit","amount_cents":1000}
        ],idempotency_key="same-key")
        self.assertFalse(a[2])
        self.assertTrue(b[2])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0],1)

    def test_wallet_snapshot_uses_available_balance(self):
        set_wallet_balance(self.c,"w1",100000,25000)
        s=wallet_snapshot(self.c,"u1")
        self.assertEqual(s["balance_cents"],100000)
        self.assertEqual(s["held_cents"],25000)
        self.assertEqual(s["available_cents"],75000)

if __name__=="__main__":
    unittest.main()
