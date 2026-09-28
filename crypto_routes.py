"""NAQAA internal crypto wallet API.

This layer supports balances, deposit intents, withdrawals and internal
transfers. External blockchain settlement stays provider-gated.
"""
import os
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from crypto_wallet import (
    SUPPORTED_ASSETS, normalize_asset, normalize_network, to_units,
    from_units, ensure_crypto_schema, ensure_crypto_wallet,
    credit_crypto, reserve_crypto, release_reserve, settle_reserved_withdrawal,
)

router = APIRouter(prefix="/api/v1/crypto", tags=["crypto-wallet"])


def _app():
    return __import__("app")


def _now():
    return _app().now()


def _db():
    return _app().db()


def _idem(request: Request):
    return _app().idem(request)


class CryptoDeposit(BaseModel):
    asset: str = "USDT"
    network: str = "TRC20"


class CryptoWithdraw(BaseModel):
    asset: str = "USDT"
    network: str = "TRC20"
    amount: str
    destination: str = Field(min_length=10, max_length=255)


class CryptoTransfer(BaseModel):
    to_account_id: str
    asset: str = "USDT"
    network: str = "TRC20"
    amount: str
    description: str = "Internal crypto transfer"


def _wallet(c, account_id, asset, network):
    return ensure_crypto_wallet(c, account_id, asset, network, _now)


def _require_verified(account):
    if account["status"] != "approved":
        raise HTTPException(403, "account verification/KYC approval is required")


def ensure_crypto_routes_schema(c):
    ensure_crypto_schema(c)


@router.get("/assets")
def assets():
    return {
        "assets": [
            {"asset": k, "decimals": v["decimals"], "networks": list(v["networks"])}
            for k, v in SUPPORTED_ASSETS.items()
        ],
        "external_settlement": "disabled" if os.getenv("CRYPTO_LIVE_ENABLED", "0") != "1" else "provider-gated",
    }


@router.get("/wallets")
def wallets(token: str):
    app = _app()
    c = _db()
    actor = app.account_for(c, token)
    ensure_crypto_schema(c)
    rows = c.execute(
        "SELECT * FROM crypto_wallets WHERE account_id=? ORDER BY asset,network",
        (actor["id"],),
    ).fetchall()
    data = [
        {
            "id": r["id"], "asset": r["asset"], "network": r["network"],
            "balance": from_units(r["balance_units"], r["asset"]),
            "held": from_units(r["held_units"], r["asset"]),
            "available": from_units(int(r["balance_units"]) - int(r["held_units"]), r["asset"]),
        }
        for r in rows
    ]
    c.close()
    return {"wallets": data}


@router.get("/wallet")
def wallet(asset: str = "USDT", network: str = "TRC20", token: str = ""):
    app = _app()
    c = _db()
    actor = app.account_for(c, token)
    try:
        asset = normalize_asset(asset)
        network = normalize_network(asset, network)
    except ValueError as exc:
        c.close()
        raise HTTPException(400, str(exc))
    row = _wallet(c, actor["id"], asset, network)
    txs = c.execute(
        """SELECT type,amount_units,status,reference,provider_transaction_id,
                  tx_hash,destination,fee_units,description,created_at,completed_at
           FROM crypto_transactions WHERE wallet_id=? ORDER BY created_at DESC LIMIT 100""",
        (row["id"],),
    ).fetchall()
    c.commit()
    c.close()
    return {
        "wallet_id": row["id"], "asset": asset, "network": network,
        "balance": from_units(row["balance_units"], asset),
        "held": from_units(row["held_units"], asset),
        "available": from_units(int(row["balance_units"]) - int(row["held_units"]), asset),
        "transactions": [
            {**dict(t),
             "amount": from_units(t["amount_units"], asset),
             "fee": from_units(t["fee_units"], asset)}
            for t in txs
        ],
    }


@router.post("/deposit-intent")
def deposit_intent(x: CryptoDeposit, token: str, request: Request):
    app = _app()
    c = _db()
    actor = app.account_for(c, token)
    _require_verified(actor)
    key = _idem(request)
    try:
        asset = normalize_asset(x.asset)
        network = normalize_network(asset, x.network)
    except ValueError as exc:
        c.close()
        raise HTTPException(400, str(exc))

    ensure_crypto_schema(c)
    c.execute("BEGIN IMMEDIATE")
    existing = c.execute(
        "SELECT * FROM crypto_transactions WHERE wallet_id IN "
        "(SELECT id FROM crypto_wallets WHERE account_id=?) AND type='deposit' "
        "AND description=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
        (actor["id"], "deposit-intent:" + key),
    ).fetchone()
    if existing:
        c.commit(); c.close()
        return {"reference": existing["reference"], "status": existing["status"],
                "asset": asset, "network": network, "amount": None, "duplicate": True}

    w = _wallet(c, actor["id"], asset, network)
    ref = "CDP-" + uuid.uuid4().hex[:12].upper()
    c.execute(
        """INSERT INTO crypto_transactions
           (id,wallet_id,type,asset,network,amount_units,status,reference,
            provider_transaction_id,tx_hash,destination,fee_units,description,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), w["id"], "deposit", asset, network, 0, "pending", ref,
         None, None, None, 0, "deposit-intent:" + key, _now(), None),
    )
    c.commit(); c.close()

    live = os.getenv("CRYPTO_LIVE_ENABLED", "0") == "1"
    return {
        "reference": ref, "status": "pending", "asset": asset, "network": network,
        "deposit_address": None,
        "message": "Deposit address is issued by the configured custody/payment provider; no blockchain credit is recorded until a verified provider event is received.",
        "provider_enabled": live,
    }


@router.post("/withdraw")
def withdraw(x: CryptoWithdraw, token: str, request: Request):
    app = _app()
    c = _db()
    actor = app.account_for(c, token)
    _require_verified(actor)
    key = _idem(request)
    try:
        asset = normalize_asset(x.asset)
        network = normalize_network(asset, x.network)
        units = to_units(x.amount, asset)
    except ValueError as exc:
        c.close()
        raise HTTPException(400, str(exc))

    ensure_crypto_schema(c)
    c.execute("BEGIN IMMEDIATE")
    dup = c.execute(
        "SELECT * FROM crypto_transactions WHERE wallet_id IN "
        "(SELECT id FROM crypto_wallets WHERE account_id=?) AND description=?",
        (actor["id"], "withdraw-request:" + key),
    ).fetchone()
    if dup:
        c.commit(); c.close()
        return {"reference": dup["reference"], "status": dup["status"], "duplicate": True}

    w = _wallet(c, actor["id"], asset, network)
    try:
        reserve_crypto(c, w["id"], units, _now)
    except ValueError as exc:
        c.rollback(); c.close()
        raise HTTPException(400, str(exc))

    ref = "CWD-" + uuid.uuid4().hex[:12].upper()
    c.execute(
        """INSERT INTO crypto_transactions
           (id,wallet_id,type,asset,network,amount_units,status,reference,
            provider_transaction_id,tx_hash,destination,fee_units,description,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), w["id"], "withdraw", asset, network, units, "pending", ref,
         None, None, x.destination.strip(), 0, "withdraw-request:" + key, _now(), None),
    )
    c.commit(); c.close()
    return {
        "reference": ref, "status": "pending", "asset": asset, "network": network,
        "amount": from_units(units, asset), "destination": x.destination.strip(),
        "message": "Withdrawal reserved and awaiting approved provider payout.",
    }


@router.post("/transfer")
def transfer(x: CryptoTransfer, token: str, request: Request):
    app = _app()
    c = _db()
    actor = app.account_for(c, token)
    _require_verified(actor)
    key = _idem(request)
    if actor["id"] == x.to_account_id:
        c.close(); raise HTTPException(400, "source and destination must differ")
    try:
        asset = normalize_asset(x.asset)
        network = normalize_network(asset, x.network)
        units = to_units(x.amount, asset)
    except ValueError as exc:
        c.close(); raise HTTPException(400, str(exc))

    recipient = c.execute("SELECT * FROM accounts WHERE id=?", (x.to_account_id,)).fetchone()
    if not recipient:
        c.close(); raise HTTPException(404, "recipient not found")
    _require_verified(recipient)
    ensure_crypto_schema(c)
    c.execute("BEGIN IMMEDIATE")
    marker = "transfer:" + key
    old = c.execute(
        "SELECT reference,status FROM crypto_transactions WHERE description=? LIMIT 1",
        (marker,),
    ).fetchone()
    if old:
        c.commit(); c.close()
        return {"reference": old["reference"], "status": old["status"], "duplicate": True}

    src = _wallet(c, actor["id"], asset, network)
    dst = _wallet(c, recipient["id"], asset, network)
    try:
        reserve_crypto(c, src["id"], units, _now)
    except ValueError as exc:
        c.rollback(); c.close(); raise HTTPException(400, str(exc))

    ref = "CTX-" + uuid.uuid4().hex[:12].upper()
    # Internal transfer is atomic: consume the source reservation and credit recipient.
    tx1, tx2 = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute("UPDATE crypto_wallets SET balance_units=balance_units-?,held_units=held_units-?,updated_at=? WHERE id=?",
              (units, units, _now(), src["id"]))
    c.execute("UPDATE crypto_wallets SET balance_units=balance_units+?,updated_at=? WHERE id=?",
              (units, _now(), dst["id"]))

    c.execute(
        """INSERT INTO crypto_transactions
           (id,wallet_id,type,asset,network,amount_units,status,reference,provider_transaction_id,
            tx_hash,destination,fee_units,description,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tx1, src["id"], "transfer_out", asset, network, units, "completed", ref, None, None,
         recipient["id"], 0, marker, _now(), _now()),
    )
    c.execute(
        """INSERT INTO crypto_transactions
           (id,wallet_id,type,asset,network,amount_units,status,reference,provider_transaction_id,
            tx_hash,destination,fee_units,description,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tx2, dst["id"], "transfer_in", asset, network, units, "completed", ref + "-IN", None, None,
         actor["id"], 0, marker, _now(), _now()),
    )
    c.execute(
        """INSERT INTO crypto_wallet_ledger
           (id,wallet_id,transaction_id,delta_units,balance_after_units,created_at)
           SELECT ?,w.id,t.id,-t.amount_units,w.balance_units,?
           FROM crypto_transactions t JOIN crypto_wallets w ON w.id=t.wallet_id
           WHERE t.id=?
           UNION ALL
           SELECT ?,w.id,t.id,t.amount_units,w.balance_units,?
           FROM crypto_transactions t JOIN crypto_wallets w ON w.id=t.wallet_id
           WHERE t.id=?""",
        (str(uuid.uuid4()), _now(), tx1, str(uuid.uuid4()), _now(), tx2),
    )
    c.commit(); c.close()
    return {"reference": ref, "status": "completed", "asset": asset, "network": network,
            "amount": from_units(units, asset), "description": x.description}


@router.get("/transactions")
def transactions(token: str, asset: str = "", network: str = ""):
    app = _app()
    c = _db()
    actor = app.account_for(c, token)
    ensure_crypto_schema(c)
    query = """SELECT t.* FROM crypto_transactions t
               JOIN crypto_wallets w ON w.id=t.wallet_id
               WHERE w.account_id=?"""
    params = [actor["id"]]
    if asset:
        query += " AND t.asset=?"; params.append(asset.upper())
    if network:
        query += " AND t.network=?"; params.append(network.upper())
    query += " ORDER BY t.created_at DESC LIMIT 200"
    rows = c.execute(query, params).fetchall()
    c.close()
    return {"transactions": [dict(r) for r in rows]}


class ProviderDepositSettlement(BaseModel):
    reference: str
    provider_transaction_id: str = Field(min_length=1, max_length=255)
    amount: str
    tx_hash: str = Field(min_length=6, max_length=255)


class ProviderWithdrawalSettlement(BaseModel):
    reference: str
    provider_transaction_id: str = Field(min_length=1, max_length=255)
    tx_hash: str = Field(min_length=6, max_length=255)


@router.post("/admin/provider/deposit-settle")
def provider_deposit_settle(x: ProviderDepositSettlement, request: Request):
    app = _app()
    app.require_admin(request)
    if os.getenv("CRYPTO_LIVE_ENABLED", "0") != "1":
        raise HTTPException(503, "crypto live settlement is disabled")
    c = _db()
    ensure_crypto_schema(c)
    c.execute("BEGIN IMMEDIATE")
    tx = c.execute("SELECT t.* FROM crypto_transactions t WHERE t.reference=?", (x.reference,)).fetchone()
    if not tx or tx["type"] != "deposit":
        c.rollback(); c.close(); raise HTTPException(404, "deposit reference not found")
    if tx["status"] == "completed":
        c.commit(); c.close(); return {"reference": x.reference, "status": "completed", "duplicate": True}
    try:
        units = to_units(x.amount, tx["asset"])
    except ValueError as exc:
        c.rollback(); c.close(); raise HTTPException(400, str(exc))
    duplicate_provider = c.execute(
        "SELECT reference FROM crypto_transactions WHERE provider_transaction_id=? AND status='completed'",
        (x.provider_transaction_id,),
    ).fetchone()
    if duplicate_provider:
        c.rollback(); c.close(); raise HTTPException(409, "provider transaction already settled")
    w = c.execute("SELECT * FROM crypto_wallets WHERE id=?", (tx["wallet_id"],)).fetchone()
    credit_crypto(c, w["id"], units, tx["id"], _now)
    c.execute("""UPDATE crypto_transactions
                 SET amount_units=?,status='completed',provider_transaction_id=?,tx_hash=?,completed_at=?
                 WHERE id=? AND status='pending'""",
              (units, x.provider_transaction_id, x.tx_hash, _now(), tx["id"]))
    c.commit(); c.close()
    return {"reference": x.reference, "status": "completed", "asset": tx["asset"],
            "network": tx["network"], "amount": from_units(units, tx["asset"]), "tx_hash": x.tx_hash}


@router.post("/admin/provider/withdraw-settle")
def provider_withdraw_settle(x: ProviderWithdrawalSettlement, request: Request):
    app = _app()
    app.require_admin(request)
    c = _db()
    ensure_crypto_schema(c)
    c.execute("BEGIN IMMEDIATE")
    tx = c.execute("SELECT * FROM crypto_transactions WHERE reference=?", (x.reference,)).fetchone()
    if not tx or tx["type"] != "withdraw":
        c.rollback(); c.close(); raise HTTPException(404, "withdrawal reference not found")
    if tx["status"] == "completed":
        c.commit(); c.close(); return {"reference": x.reference, "status": "completed", "duplicate": True}
    if tx["status"] != "pending":
        c.rollback(); c.close(); raise HTTPException(409, "withdrawal is not pending")
    duplicate_provider = c.execute(
        "SELECT reference FROM crypto_transactions WHERE provider_transaction_id=? AND status='completed'",
        (x.provider_transaction_id,),
    ).fetchone()
    if duplicate_provider:
        c.rollback(); c.close(); raise HTTPException(409, "provider transaction already settled")
    try:
        settle_reserved_withdrawal(c, tx["wallet_id"], int(tx["amount_units"]), _now)
    except ValueError as exc:
        c.rollback(); c.close(); raise HTTPException(409, str(exc))
    c.execute("""UPDATE crypto_transactions
                 SET status='completed',provider_transaction_id=?,tx_hash=?,completed_at=?
                 WHERE id=? AND status='pending'""",
              (x.provider_transaction_id, x.tx_hash, _now(), tx["id"]))
    c.execute(
        """INSERT INTO crypto_wallet_ledger
           (id,wallet_id,transaction_id,delta_units,balance_after_units,created_at)
           SELECT ?,w.id,t.id,-t.amount_units,w.balance_units,?
           FROM crypto_transactions t JOIN crypto_wallets w ON w.id=t.wallet_id
           WHERE t.id=?""",
        (str(uuid.uuid4()), _now(), tx["id"]),
    )
    c.commit(); c.close()
    return {"reference": x.reference, "status": "completed", "asset": tx["asset"],
            "network": tx["network"], "amount": from_units(tx["amount_units"], tx["asset"]),
            "tx_hash": x.tx_hash}


@router.post("/admin/provider/withdraw-reject")
def provider_withdraw_reject(reference: str, request: Request):
    app = _app()
    app.require_admin(request)
    c = _db()
    ensure_crypto_schema(c)
    c.execute("BEGIN IMMEDIATE")
    tx = c.execute("SELECT * FROM crypto_transactions WHERE reference=?", (reference,)).fetchone()
    if not tx or tx["type"] != "withdraw":
        c.rollback(); c.close(); raise HTTPException(404, "withdrawal reference not found")
    if tx["status"] != "pending":
        c.rollback(); c.close(); raise HTTPException(409, "withdrawal is not pending")
    release_reserve(c, tx["wallet_id"], int(tx["amount_units"]), _now())
    c.execute("UPDATE crypto_transactions SET status='failed',completed_at=? WHERE id=? AND status='pending'",
              (_now(), tx["id"]))
    c.commit(); c.close()
    return {"reference": reference, "status": "failed", "reserve_released": True}
