"""NAQAA internal crypto wallet primitives.

The internal wallet is the ledger of record for user crypto balances.
Blockchain custody/deposit-address generation and payout execution are provider
responsibilities; no private keys are stored here.
"""
import os
from decimal import Decimal, InvalidOperation


SUPPORTED_ASSETS = {
    "USDT": {"decimals": 6, "networks": ("TRC20", "ERC20", "BEP20", "SOL")},
    "USDC": {"decimals": 6, "networks": ("ERC20", "BEP20", "SOL")},
    "BTC": {"decimals": 8, "networks": ("BITCOIN",)},
    "ETH": {"decimals": 18, "networks": ("ERC20",)},
}


def normalize_asset(asset: str) -> str:
    value = (asset or "").strip().upper()
    if value not in SUPPORTED_ASSETS:
        raise ValueError("unsupported crypto asset")
    return value


def normalize_network(asset: str, network: str) -> str:
    asset = normalize_asset(asset)
    value = (network or "").strip().upper()
    if value not in SUPPORTED_ASSETS[asset]["networks"]:
        raise ValueError("unsupported network for asset")
    return value


def to_units(value, asset: str) -> int:
    asset = normalize_asset(asset)
    decimals = SUPPORTED_ASSETS[asset]["decimals"]
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid crypto amount")
    if d <= 0:
        raise ValueError("crypto amount must be greater than zero")
    scaled = d * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"amount supports at most {decimals} decimal places")
    return int(scaled)


def from_units(units: int, asset: str) -> str:
    asset = normalize_asset(asset)
    decimals = SUPPORTED_ASSETS[asset]["decimals"]
    d = Decimal(int(units)) / (Decimal(10) ** decimals)
    return format(d, "f")


def ensure_crypto_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS crypto_wallets(
      id TEXT PRIMARY KEY,
      account_id TEXT NOT NULL,
      asset TEXT NOT NULL,
      network TEXT NOT NULL,
      balance_units INTEGER NOT NULL DEFAULT 0,
      held_units INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id,asset,network)
    );
    CREATE TABLE IF NOT EXISTS crypto_addresses(
      id TEXT PRIMARY KEY,
      wallet_id TEXT NOT NULL,
      provider TEXT NOT NULL,
      address TEXT NOT NULL,
      memo_tag TEXT,
      status TEXT NOT NULL DEFAULT 'active',
      created_at TEXT NOT NULL,
      UNIQUE(provider,address,memo_tag)
    );
    CREATE TABLE IF NOT EXISTS crypto_transactions(
      id TEXT PRIMARY KEY,
      wallet_id TEXT NOT NULL,
      type TEXT NOT NULL,
      asset TEXT NOT NULL,
      network TEXT NOT NULL,
      amount_units INTEGER NOT NULL,
      status TEXT NOT NULL,
      reference TEXT UNIQUE NOT NULL,
      provider_transaction_id TEXT,
      tx_hash TEXT,
      destination TEXT,
      fee_units INTEGER NOT NULL DEFAULT 0,
      description TEXT,
      created_at TEXT NOT NULL,
      completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_crypto_tx_wallet ON crypto_transactions(wallet_id,created_at);
    CREATE INDEX IF NOT EXISTS idx_crypto_tx_provider ON crypto_transactions(provider_transaction_id);
    CREATE TABLE IF NOT EXISTS crypto_wallet_ledger(
      id TEXT PRIMARY KEY,
      wallet_id TEXT NOT NULL,
      transaction_id TEXT NOT NULL,
      delta_units INTEGER NOT NULL,
      balance_after_units INTEGER NOT NULL,
      created_at TEXT NOT NULL
    );
    """)


def ensure_crypto_wallet(c, account_id: str, asset: str, network: str, now_fn):
    asset = normalize_asset(asset)
    network = normalize_network(asset, network)
    row = c.execute(
        "SELECT * FROM crypto_wallets WHERE account_id=? AND asset=? AND network=?",
        (account_id, asset, network),
    ).fetchone()
    if row:
        return row
    import uuid
    wid = str(uuid.uuid4())
    c.execute(
        """INSERT INTO crypto_wallets
           (id,account_id,asset,network,balance_units,held_units,updated_at)
           VALUES(?,?,?,?,0,0,?)""",
        (wid, account_id, asset, network, now_fn()),
    )
    return c.execute("SELECT * FROM crypto_wallets WHERE id=?", (wid,)).fetchone()


def credit_crypto(c, wallet_id: str, amount_units: int, tx_id: str, now_fn):
    if amount_units <= 0:
        raise ValueError("credit must be positive")
    row = c.execute("SELECT * FROM crypto_wallets WHERE id=?", (wallet_id,)).fetchone()
    if not row:
        raise ValueError("crypto wallet not found")
    new_balance = int(row["balance_units"]) + amount_units
    c.execute(
        "UPDATE crypto_wallets SET balance_units=?,updated_at=? WHERE id=?",
        (new_balance, now_fn(), wallet_id),
    )
    c.execute(
        """INSERT INTO crypto_wallet_ledger
           (id,wallet_id,transaction_id,delta_units,balance_after_units,created_at)
           VALUES(?,?,?,?,?,?)""",
        (__import__("uuid").uuid4().hex, wallet_id, tx_id, amount_units, new_balance, now_fn()),
    )


def reserve_crypto(c, wallet_id: str, amount_units: int, now_fn):
    if amount_units <= 0:
        raise ValueError("reserve must be positive")
    row = c.execute("SELECT * FROM crypto_wallets WHERE id=?", (wallet_id,)).fetchone()
    if not row:
        raise ValueError("crypto wallet not found")
    available = int(row["balance_units"]) - int(row["held_units"])
    if amount_units > available:
        raise ValueError("insufficient available crypto balance")
    c.execute(
        "UPDATE crypto_wallets SET held_units=held_units+?,updated_at=? WHERE id=?",
        (amount_units, now_fn(), wallet_id),
    )
