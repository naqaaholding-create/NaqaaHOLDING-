import os, sqlite3, tempfile
from crypto_wallet import ensure_crypto_schema, ensure_crypto_wallet, to_units, reserve_crypto, release_reserve, settle_reserved_withdrawal

def test_withdrawal_reservation_lifecycle():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        c=sqlite3.connect(path); c.row_factory=sqlite3.Row; ensure_crypto_schema(c)
        w=ensure_crypto_wallet(c,"a","USDT","TRC20",lambda:"now")
        c.execute("UPDATE crypto_wallets SET balance_units=? WHERE id=?",(to_units("10","USDT"),w["id"]))
        reserve_crypto(c,w["id"],to_units("3","USDT"),lambda:"now")
        release_reserve(c,w["id"],to_units("3","USDT"),lambda:"now")
        r=c.execute("SELECT * FROM crypto_wallets WHERE id=?",(w["id"],)).fetchone()
        assert r["balance_units"]==10000000 and r["held_units"]==0
        reserve_crypto(c,w["id"],to_units("2","USDT"),lambda:"now")
        settle_reserved_withdrawal(c,w["id"],to_units("2","USDT"),lambda:"now")
        r=c.execute("SELECT * FROM crypto_wallets WHERE id=?",(w["id"],)).fetchone()
        assert r["balance_units"]==8000000 and r["held_units"]==0
        c.close()
    finally: os.unlink(path)
