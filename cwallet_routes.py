"""NAQAA Market Cwallet integration layer.

Provider-specific API details remain configurable because the exact merchant
contract must be verified from the Cwallet account/docs before live money.
"""
import os
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, HTTPException, Request

from cwallet_provider import CwalletConfigurationError, CwalletProvider
from finance_ledger import ensure_wallet_ledger, post_entry, set_wallet_balance, amount, to_cents

router = APIRouter(prefix="/api/v1/cwallet", tags=["cwallet"])


def _now(app):
    return app.now()


def ensure_cwallet_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS payment_intents(
      id TEXT PRIMARY KEY, order_id TEXT NOT NULL, buyer_id TEXT NOT NULL,
      provider TEXT NOT NULL, provider_payment_id TEXT, reference TEXT UNIQUE NOT NULL,
      amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, asset TEXT, network TEXT,
      status TEXT NOT NULL, checkout_url TEXT, created_at TEXT NOT NULL, paid_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_payment_intents_order ON payment_intents(order_id);
    CREATE INDEX IF NOT EXISTS idx_payment_intents_provider_payment ON payment_intents(provider_payment_id);

    CREATE TABLE IF NOT EXISTS provider_transactions(
      id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_transaction_id TEXT NOT NULL,
      payment_intent_id TEXT, order_id TEXT, type TEXT NOT NULL, asset TEXT, network TEXT,
      amount TEXT, currency TEXT, status TEXT NOT NULL, raw_reference TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(provider,provider_transaction_id)
    );

    CREATE TABLE IF NOT EXISTS provider_webhooks(
      id TEXT PRIMARY KEY, provider TEXT NOT NULL, event_id TEXT NOT NULL,
      event_type TEXT, provider_transaction_id TEXT, signature_verified INTEGER NOT NULL,
      payload_hash TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
      received_at TEXT NOT NULL, processed_at TEXT,
      UNIQUE(provider,event_id)
    );

    CREATE TABLE IF NOT EXISTS payouts(
      id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, wallet_id TEXT NOT NULL,
      provider TEXT NOT NULL, provider_payout_id TEXT, amount_cents INTEGER NOT NULL,
      asset TEXT NOT NULL, network TEXT NOT NULL, destination TEXT NOT NULL,
      status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
    );
    """)


def _extract(payload, *keys):
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _provider_status(payload):
    return str(_extract(payload, "status", "payment_status", "state") or "").lower()


def _amount_cents(value):
    try:
        return to_cents(value)
    except (ValueError, InvalidOperation, TypeError):
        raise HTTPException(400, "provider event contains an invalid amount")


def _settle_payment(app, c, intent, payload, actor="cwallet-webhook"):
    if intent["status"] == "paid":
        return {"duplicate": True, "order_id": intent["order_id"], "status": "paid"}

    order = c.execute("SELECT * FROM orders WHERE id=?", (intent["order_id"],)).fetchone()
    if not order:
        raise HTTPException(404, "order not found for payment intent")
    if order["status"] != "awaiting_payment":
        raise HTTPException(409, "order is no longer awaiting provider payment")

    event_currency = str(_extract(payload, "currency", "fiat_currency") or intent["currency"]).upper()
    if event_currency != intent["currency"]:
        raise HTTPException(409, "provider currency does not match payment intent")

    event_amount = _extract(payload, "amount", "fiat_amount", "amount_usd")
    if event_amount is None:
        raise HTTPException(409, "provider event must contain a settlement amount")
    if _amount_cents(event_amount) != int(intent["amount_cents"]):
        raise HTTPException(409, "provider amount does not match payment intent")

    buyer_kyc = c.execute("SELECT status FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (intent["buyer_id"],)).fetchone()
    seller_kyc = c.execute("SELECT status FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (order["seller_id"],)).fetchone()
    if not buyer_kyc or buyer_kyc["status"] != "approved":
        raise HTTPException(403, "approved buyer KYC is required")
    if not seller_kyc or seller_kyc["status"] != "approved":
        raise HTTPException(403, "approved seller verification is required")

    gross = int(order["gross_cents"])
    buyer_fee = int(order["buyer_fee_cents"] or 0)
    seller_fee = int(order["seller_fee_cents"] or 0)
    buyer_total = int(order["buyer_total_cents"] or gross + buyer_fee)
    seller_net = int(order["seller_net_cents"] or gross - seller_fee)
    if buyer_total != int(intent["amount_cents"]) or seller_net <= 0:
        raise HTTPException(409, "payment intent does not match stored order settlement")

    provider_tx = _extract(payload, "payment_id", "transaction_id", "provider_transaction_id", "id")
    if not provider_tx:
        raise HTTPException(409, "provider transaction id is required")

    buyer = c.execute("SELECT * FROM wallets WHERE account_id=?", (order["buyer_id"],)).fetchone()
    seller = c.execute("SELECT * FROM wallets WHERE account_id=?", (order["seller_id"],)).fetchone()
    if not buyer or not seller:
        raise HTTPException(404, "buyer or seller wallet not found")

    # Provider settlement is the source event; do not debit the buyer's internal
    # wallet again. Credit the seller net and company commission against provider
    # settlement clearing.
    provider_ledger = "SYSTEM:CWALLET_CLEARING"
    c.execute(
        "INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES(?,?,?,?,?)",
        (provider_ledger, "provider", "CWALLET", "USD", app.now()),
    )
    seller_ledger = ensure_wallet_ledger(c, order["seller_id"], "USD", seller["id"])
    ref = "CW-" + uuid.uuid4().hex[:10].upper()
    lines = [
        {"account_id": provider_ledger, "side": "debit", "amount_cents": buyer_total},
        {"account_id": seller_ledger, "side": "credit", "amount_cents": seller_net},
    ]
    if buyer_fee + seller_fee:
        lines.append({"account_id": "SYSTEM:COMMISSION_REVENUE", "side": "credit", "amount_cents": buyer_fee + seller_fee})
    post_entry(c, ref, "cwallet_marketplace_settlement", lines,
               "Cwallet provider settlement", "cwallet-settle:" + intent["id"])

    c.execute("UPDATE orders SET commission_cents=?,payment_reference=?,paid_at=?,status='paid' WHERE id=? AND status='awaiting_payment'",
              (buyer_fee + seller_fee, ref, app.now(), order["id"]))
    if c.execute("SELECT changes()").fetchone()[0] != 1:
        raise HTTPException(409, "order settlement race detected")

    set_wallet_balance(c, seller["id"], int(seller["balance_cents"]) + seller_net, int(seller["held_cents"] or 0) + seller_net)
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",
              (str(uuid.uuid4()), seller["id"], "cwallet_marketplace_settlement",
               float(amount(seller_net)), "USD", ref, "Cwallet provider settlement; seller net", "completed", app.now()))
    c.execute("INSERT INTO commission_entries(id,order_id,side,amount_cents,currency,rate_bps,fixed_cents,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (str(uuid.uuid4()), order["id"], "buyer", buyer_fee, "USD", int(order["buyer_rate_bps"] or 0), int(order["buyer_fixed_cents"] or 0), "posted", app.now())) if buyer_fee else None
    c.execute("INSERT INTO commission_entries(id,order_id,side,amount_cents,currency,rate_bps,fixed_cents,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (str(uuid.uuid4()), order["id"], "seller", seller_fee, "USD", int(order["seller_rate_bps"] or 0), int(order["seller_fixed_cents"] or 0), "posted", app.now())) if seller_fee else None
    c.execute("INSERT INTO escrow_transactions(id,order_id,amount_cents,currency,status,held_at,released_at) VALUES(?,?,?,?,?,?,?)",
              (str(uuid.uuid4()), order["id"], seller_net, "USD", "held", app.now(), None))
    c.execute("UPDATE payment_intents SET status='paid',provider_payment_id=?,paid_at=? WHERE id=?",
              (str(provider_tx), app.now(), intent["id"]))
    return {"order_id": order["id"], "status": "paid", "reference": ref, "seller_net": float(amount(seller_net))}


@router.on_event("startup")
async def _startup():
    # Startup hook intentionally only verifies schema; provider remains disabled
    # unless explicitly configured.
    app = __import__("app")
    c = app.db()
    ensure_cwallet_schema(c)
    c.commit()
    c.close()


@router.post("/orders/{order_id}/payment-intent")
def create_payment_intent(order_id: str, token: str, request: Request):
    app = __import__("app")
    c = app.db()
    buyer = app.account_for(c, token)
    if buyer["role"] != "buyer":
        c.close()
        raise HTTPException(403, "only a buyer can create a payment intent")
    key = app.idem(request)
    c.execute("BEGIN IMMEDIATE")
    order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        c.rollback(); c.close(); raise HTTPException(404, "order not found")
    if order["buyer_id"] != buyer["id"]:
        c.rollback(); c.close(); raise HTTPException(403, "order access denied")
    if order["status"] != "awaiting_payment":
        c.rollback(); c.close(); raise HTTPException(409, "order is not awaiting payment")

    old = c.execute("SELECT * FROM payment_intents WHERE order_id=? ORDER BY created_at DESC LIMIT 1", (order_id,)).fetchone()
    if old:
        c.commit(); c.close()
        return {"payment_intent_id": old["id"], "reference": old["reference"], "status": old["status"], "checkout_url": old["checkout_url"], "duplicate": True}

    intent_id = str(uuid.uuid4())
    reference = "NQ-CW-" + uuid.uuid4().hex[:12].upper()
    amount_cents = int(order["buyer_total_cents"])
    c.execute("""INSERT INTO payment_intents
        (id,order_id,buyer_id,provider,provider_payment_id,reference,amount_cents,currency,asset,network,status,checkout_url,created_at,paid_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (intent_id, order_id, buyer["id"], "cwallet", None, reference, amount_cents, "USD",
         os.getenv("CWALLET_DEFAULT_ASSET", "USDT"), os.getenv("CWALLET_DEFAULT_NETWORK", "TRC20"),
         "created", None, app.now(), None))

    provider = CwalletProvider()
    response = None
    if provider.enabled:
        try:
            response = provider.create_payment(
                reference=reference,
                amount=f"{amount_cents/100:.2f}",
                currency="USD",
                asset=os.getenv("CWALLET_DEFAULT_ASSET", "USDT"),
                network=os.getenv("CWALLET_DEFAULT_NETWORK", "TRC20"),
                callback_url=str(request.base_url).rstrip("/") + "/api/v1/cwallet/webhook",
                metadata={"payment_intent_id": intent_id, "order_id": order_id},
            )
        except CwalletConfigurationError as exc:
            c.rollback(); c.close(); raise HTTPException(503, str(exc))
        provider_payment_id = _extract(response, "payment_id", "transaction_id", "id")
        checkout_url = _extract(response, "payment_url", "checkout_url", "url")
        c.execute("UPDATE payment_intents SET provider_payment_id=?,checkout_url=?,status='provider_pending' WHERE id=?",
                  (None if provider_payment_id is None else str(provider_payment_id),
                   None if checkout_url is None else str(checkout_url), intent_id))
        status = "provider_pending"
    else:
        status = "provider_disabled"

    c.commit(); c.close()
    return {"payment_intent_id": intent_id, "reference": reference, "status": status,
            "amount": amount(amount_cents), "currency": "USD",
            "asset": os.getenv("CWALLET_DEFAULT_ASSET", "USDT"),
            "network": os.getenv("CWALLET_DEFAULT_NETWORK", "TRC20"),
            "checkout_url": None if not response else _extract(response, "payment_url", "checkout_url", "url"),
            "provider": "cwallet", "sandbox": not provider.enabled}


@router.post("/webhook")
async def cwallet_webhook(request: Request):
    app = __import__("app")
    raw = await request.body()
    provider = CwalletProvider()
    signature_header = os.getenv("CWALLET_WEBHOOK_SIGNATURE_HEADER", "X-Cwallet-Signature")
    signature = request.headers.get(signature_header, "")
    if provider.enabled and not provider.verify_webhook(raw, signature):
        raise HTTPException(401, "invalid Cwallet webhook signature")
    try:
        payload = provider.parse_webhook(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    event_id = _extract(payload, "event_id", "eventId", "id")
    if not event_id:
        raise HTTPException(400, "provider event id is required")
    provider_tx = _extract(payload, "payment_id", "transaction_id", "provider_transaction_id")
    event_type = str(_extract(payload, "event_type", "type", "event") or "payment")
    payload_hash = __import__("hashlib").sha256(raw).hexdigest()

    c = app.db()
    ensure_cwallet_schema(c)
    try:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute("SELECT * FROM provider_webhooks WHERE provider=? AND event_id=?",
                         ("cwallet", str(event_id))).fetchone()
        if old:
            c.commit(); c.close()
            return {"accepted": True, "duplicate": True, "processed": bool(old["processed"])}

        c.execute("""INSERT INTO provider_webhooks
          (id,provider,event_id,event_type,provider_transaction_id,signature_verified,payload_hash,processed,received_at,processed_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (str(uuid.uuid4()), "cwallet", str(event_id), event_type,
           None if provider_tx is None else str(provider_tx), 1 if (not provider.enabled or provider.verify_webhook(raw, signature)) else 0,
           payload_hash, 0, app.now(), None))

        status = _provider_status(payload)
        intent_id = _extract(payload, "payment_intent_id", "metadata.payment_intent_id")
        intent = None
        if intent_id:
            intent = c.execute("SELECT * FROM payment_intents WHERE id=?", (str(intent_id),)).fetchone()
        if not intent:
            reference = _extract(payload, "reference", "merchant_reference", "order_reference")
            if reference:
                intent = c.execute("SELECT * FROM payment_intents WHERE reference=?", (str(reference),)).fetchone()
        if not intent:
            raise HTTPException(404, "payment intent not found")
        if status in ("paid", "completed", "success", "succeeded"):
            result = _settle_payment(app, c, intent, payload)
            processed = 1
        elif status in ("failed", "expired", "cancelled", "canceled"):
            c.execute("UPDATE payment_intents SET status=? WHERE id=? AND status NOT IN ('paid','settled')", (status, intent["id"]))
            result = {"payment_intent_id": intent["id"], "status": status}
            processed = 1
        else:
            c.execute("UPDATE payment_intents SET status='provider_pending' WHERE id=? AND status!='paid'", (intent["id"],))
            result = {"payment_intent_id": intent["id"], "status": "provider_pending"}
            processed = 1

        c.execute("UPDATE provider_webhooks SET processed=?,processed_at=? WHERE provider=? AND event_id=?",
                  (processed, app.now(), "cwallet", str(event_id)))
        c.execute("INSERT OR IGNORE INTO provider_transactions
          (id,provider,provider_transaction_id,payment_intent_id,order_id,type,asset,network,amount,currency,status,raw_reference,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (str(uuid.uuid4()), "cwallet", str(provider_tx or event_id), intent["id"], intent["order_id"],
           "payment", str(_extract(payload, "asset", "token") or intent["asset"]),
           str(_extract(payload, "network", "chain") or intent["network"]),
           str(_extract(payload, "amount", "fiat_amount", "amount_usd") or ""), str(_extract(payload, "currency", "fiat_currency") or "USD"),
           status or "received", str(event_id), app.now(), app.now()))
        c.commit(); c.close()
        return {"accepted": True, "duplicate": False, "processed": True, **result}
    except HTTPException:
        c.rollback(); c.close()
        raise
    except Exception:
        c.rollback(); c.close()
        raise


@router.get("/payment-intents/{payment_intent_id}")
def get_payment_intent(payment_intent_id: str, token: str):
    app = __import__("app")
    c = app.db()
    actor = app.account_for(c, token)
    row = c.execute("SELECT * FROM payment_intents WHERE id=?", (payment_intent_id,)).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "payment intent not found")
    order = c.execute("SELECT buyer_id,seller_id FROM orders WHERE id=?", (row["order_id"],)).fetchone()
    if not order or actor["id"] not in (order["buyer_id"], order["seller_id"]):
        c.close(); raise HTTPException(403, "payment intent access denied")
    data = dict(row)
    c.close()
    return data
