import hashlib, hmac, os, json, uuid
from fastapi import APIRouter, HTTPException, Request
from tap_provider import TapConfigurationError, TapProvider

router=APIRouter(prefix="/api/v1/tap",tags=["tap"])

def ensure_tap_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS tap_payment_intents(
      id TEXT PRIMARY KEY, order_id TEXT NOT NULL, buyer_id TEXT NOT NULL,
      reference TEXT UNIQUE NOT NULL, charge_id TEXT, amount_cents INTEGER NOT NULL,
      currency TEXT NOT NULL, status TEXT NOT NULL, checkout_url TEXT,
      created_at TEXT NOT NULL, paid_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tap_intents_order ON tap_payment_intents(order_id);
    """)

def _decimals(currency):
    return 3 if currency in {"BHD","KWD","OMR","JOD"} else 2

def _fmt(value,currency):
    return f"{float(value):.{_decimals(currency)}f}"

def _verify(payload, header, secret):
    ref=payload.get("reference") or {}
    tx=payload.get("transaction") or {}
    currency=str(payload.get("currency") or "").upper()
    raw=(f"x_id{payload.get('id','')}x_amount{_fmt(payload.get('amount',0),currency)}"
         f"x_currency{currency}x_gateway_reference{ref.get('gateway','')}"
         f"x_payment_reference{ref.get('payment') or ref.get('transaction','')}"
         f"x_status{payload.get('status','')}x_created{tx.get('created','')}")
    expected=hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest()
    return bool(header) and hmac.compare_digest(expected,header)

@router.on_event("startup")
async def startup():
    app=__import__("app")
    c=app.db(); ensure_tap_schema(c); c.commit(); c.close()

@router.post("/orders/{order_id}/payment-intent")
def create_intent(order_id:str, token:str, request:Request):
    app=__import__("app")
    c=app.db(); buyer=app.account_for(c,token)
    if buyer["role"]!="buyer":
        c.close(); raise HTTPException(403,"buyer required")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o:
        c.close(); raise HTTPException(404,"order not found")
    if o["buyer_id"]!=buyer["id"] or o["status"]!="awaiting_payment":
        c.close(); raise HTTPException(409,"order is not payable")
    if o["settlement_mode"]!="naqa_protected":
        c.close(); raise HTTPException(409,"protected settlement required")
    if not os.getenv("TAP_ENABLED","0")=="1":
        c.close(); raise HTTPException(503,"Tap is not enabled")
    ensure_tap_schema(c)
    old=c.execute("SELECT * FROM tap_payment_intents WHERE order_id=? ORDER BY created_at DESC LIMIT 1",(order_id,)).fetchone()
    if old:
        c.close(); return {"payment_intent_id":old["id"],"charge_id":old["charge_id"],"status":old["status"],"checkout_url":old["checkout_url"],"duplicate":True}
    amount_cents=int(o["buyer_total_cents"])
    currency=os.getenv("TAP_CURRENCY","USD").upper()
    p=TapProvider()
    reference="NQ-TAP-"+uuid.uuid4().hex[:12].upper()
    customer={"first_name":str(buyer["name"]).split()[0],"last_name":" ".join(str(buyer["name"]).split()[1:]) or "Customer","email":buyer["email"]}
    try:
        response=p.post_charge(
            amount=_fmt(amount_cents/100,currency),reference=reference,order_id=order_id,customer=customer,
            post_url=str(request.base_url).rstrip("/")+"/api/v1/tap/webhook",
            redirect_url=str(request.base_url).rstrip("/")+"/api/v1/tap/redirect",
            destination_id=os.getenv("TAP_DEFAULT_DESTINATION_ID") or None,
            destination_amount=int(o["seller_net_cents"])/100)
    except TapConfigurationError as exc:
        c.close(); raise HTTPException(503,str(exc))
    except RuntimeError as exc:
        c.close(); raise HTTPException(502,str(exc))
    charge_id=response.get("id"); checkout=((response.get("transaction") or {}).get("url"))
    if not charge_id or not checkout:
        c.close(); raise HTTPException(502,"Tap did not return checkout data")
    iid=str(uuid.uuid4())
    c.execute("INSERT INTO tap_payment_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (iid,order_id,buyer["id"],reference,str(charge_id),amount_cents,currency,str(response.get("status") or "INITIATED"),checkout,app.now(),None))
    c.commit(); c.close()
    return {"payment_intent_id":iid,"charge_id":charge_id,"status":response.get("status"),"checkout_url":checkout,"currency":currency}

@router.post("/webhook")
async def webhook(request:Request):
    app=__import__("app")
    raw=await request.body()
    try: payload=json.loads(raw.decode())
    except Exception: raise HTTPException(400,"invalid webhook")
    p=TapProvider()
    if not p.enabled or not _verify(payload,request.headers.get("hashstring",""),p.secret_key):
        raise HTTPException(401,"invalid webhook signature")
    c=app.db(); ensure_tap_schema(c)
    intent=c.execute("SELECT * FROM tap_payment_intents WHERE charge_id=?",(str(payload.get("id") or ""),)).fetchone()
    if not intent:
        c.close(); raise HTTPException(404,"payment intent not found")
    status=str(payload.get("status") or "").upper()
    if status=="CAPTURED":
        currency=str(payload.get("currency") or "").upper()
        amount_cents=int(round(float(payload.get("amount",0))*100))
        if currency!=intent["currency"] or amount_cents!=int(intent["amount_cents"]):
            c.close(); raise HTTPException(409,"webhook amount mismatch")
        c.execute("UPDATE tap_payment_intents SET status='CAPTURED',paid_at=? WHERE id=?",(app.now(),intent["id"]))
        c.execute("UPDATE orders SET payment_reference=?,paid_at=?,status='provider_paid' WHERE id=? AND status='awaiting_payment'",(intent["charge_id"],app.now(),intent["order_id"]))
    else:
        c.execute("UPDATE tap_payment_intents SET status=? WHERE id=?",(status or "UNKNOWN",intent["id"]))
    c.commit(); c.close()
    return {"accepted":True,"status":status}

@router.get("/redirect")
def redirect(tap_id:str|None=None):
    return {"status":"redirect_received","tap_id":tap_id}
