import os
import sqlite3
import tempfile

from crypto_wallet import (
    SUPPORTED_ASSETS, ensure_crypto_schema, ensure_crypto_wallet,
    to_units, from_units, reserve_crypto, credit_crypto,
)


def test_crypto_units_precision():
    assert to_units("1.25", "USDT") == 1250000
    assert from_units(1250000, "USDT") == "1.25"
    assert to_units("0.00000001", "BTC") == 1


def test_crypto_wallet_credit_and_reserve():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        ensure_crypto_schema(c)
        w = ensure_crypto_wallet(c, "acct-1", "USDT", "TRC20", lambda: "now")
        tx = "tx-1"
        credit_crypto(c, w["id"], to_units("10", "USDT"), tx, lambda: "now")
        reserve_crypto(c, w["id"], to_units("3", "USDT"), lambda: "now")
        row = c.execute("SELECT * FROM crypto_wallets WHERE id=?", (w["id"],)).fetchone()
        assert row["balance_units"] == 10000000
        assert row["held_units"] == 3000000
        c.close()
    finally:
        os.unlink(path)


def test_supported_assets_have_networks():
    assert {"USDT", "USDC", "BTC", "ETH"} <= set(SUPPORTED_ASSETS)
    for value in SUPPORTED_ASSETS.values():
        assert value["networks"]


def test_reserve_rejects_insufficient_available_balance():
    import pytest
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import pytest
    try:
        c = sqlite3.connect(path); c.row_factory = sqlite3.Row
        ensure_crypto_schema(c)
        w = ensure_crypto_wallet(c, "acct-2", "USDT", "TRC20", lambda: "now")
        credit_crypto(c, w["id"], to_units("5", "USDT"), "tx-2", lambda: "now")
        with pytest.raises(ValueError):
            reserve_crypto(c, w["id"], to_units("6", "USDT"), lambda: "now")
    finally:
        try: c.close()
        except Exception: pass
        os.unlink(path)
