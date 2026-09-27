import os, sqlite3, hashlib, uuid, secrets, base64
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from finance_ledger import ensure_schema, ensure_wallet_ledger, post_entry, set_wallet_balance, wallet_snapshot, to_cents, amount

APP_VERSION="6.3.0"
DB=os.getenv("DATABASE_PATH","naqaa_market.db")
REAL_MONEY_ENABLED=os.getenv("REAL_MONEY_ENABLED","0")=="1"
ADMIN_API_KEY=os.getenv("NAQAA_ADMIN_KEY","")
app=FastAPI(title="NAQAA Market API",version=APP_VERSION)

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,role TEXT NOT NULL,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,password_hash TEXT,
        account_type TEXT DEFAULT 'individual',dob TEXT,nationality TEXT,phone TEXT,identity_type TEXT,identity_last4 TEXT,identity_country TEXT,company_name TEXT,company_registration TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,account_id TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallets(id TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,currency TEXT NOT NULL DEFAULT 'USD',balance REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_transactions(id TEXT PRIMARY KEY,wallet_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,reference TEXT,description TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_requests(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,reference TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS listings(id TEXT PRIMARY KEY,seller_id TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY,listing_id TEXT NOT NULL,buyer_id TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY,offer_id TEXT NOT NULL,contract_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    """)
    for col,typ in [("account_type","TEXT"),("dob","TEXT"),("nationality","TEXT"),("phone","TEXT"),("identity_type","TEXT"),("identity_last4","TEXT"),("identity_country","TEXT"),("company_name","TEXT"),("company_registration","TEXT")]:
        try: c.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    ensure_schema(c)
    c.commit(); c.close()
init_db()

def hp(p):
    s=secrets.token_bytes(16); d=hashlib.pbkdf2_hmac("sha256",p.encode(),s,200000)
    return "pbkdf2$200000$"+base64.b64encode(s).decode()+"$"+base64.b64encode(d).decode()
def vp(p,h):
    try:
        _,n,s,d=h.split("$"); a=hashlib.pbkdf2_hmac("sha256",p.encode(),base64.b64decode(s),int(n))
        return secrets.compare_digest(a,base64.b64decode(d))
    except Exception: return False

class Register(BaseModel):
    role:str=Field(pattern="^(seller|buyer)$")
    name:str
    email:str
    password:str=Field(min_length=8)
    account_type:str=Field(default="individual",pattern="^(individual|company)$")
    dob:str=""
    nationality:str=""
    phone:str=""
    identity_type:str=Field(default="passport",pattern="^(passport|national_id)$")
    identity_number:str=""
    identity_country:str=""
    company_name:str=""
    company_registration:str=""
class Login(BaseModel): email:str; password:str
class Listing(BaseModel):
    seller_id:str; category:str; title:str; description:str=""; amount:float=Field(gt=0); currency:str="USD"
class Offer(BaseModel): listing_id:str; buyer_id:str; amount:float=Field(gt=0); currency:str="USD"
class WalletRequest(BaseModel): account_id:str; amount:float=Field(gt=0); currency:str="USD"
class WalletPay(BaseModel): from_account_id:str; to_account_id:str; amount:float=Field(gt=0); currency:str="USD"; description:str="Marketplace wallet payment"

def account_public(a):
    return {
        "id":a["id"],"name":a["name"],"email":a["email"],"role":a["role"],"status":a["status"],
        "account_type":a["account_type"],"dob":a["dob"],"nationality":a["nationality"],"phone":a["phone"],
        "identity_type":a["identity_type"],"identity_last4":a["identity_last4"],"identity_country":a["identity_country"],
        "company_name":a["company_name"],"company_registration":a["company_registration"]
    }

def account_for(c,token):
    r=c.execute("SELECT * FROM accounts a JOIN sessions s ON s.account_id=a.id WHERE s.token=?",(token,)).fetchone()
    if not r: raise HTTPException(401,"invalid session")
    return r

@app.get("/")
def root(): return FileResponse("static/index.html")
@app.get("/robots.txt")
def robots(request:Request):
    base=str(request.base_url).rstrip("/")
    return PlainTextResponse(f"User-agent: *\\nAllow: /\\nSitemap: {base}/sitemap.xml\\n")
@app.get("/sitemap.xml")
def sitemap(request:Request):
    base=str(request.base_url).rstrip("/")
    xml=f"<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>{base}/</loc></url></urlset>"
    return PlainTextResponse(xml,media_type="application/xml")
@app.get("/health")
def health(): return {"status":"ok","version":APP_VERSION,"wallet_only":True,"real_money":REAL_MONEY_ENABLED,"provider_mode":"LIVE" if REAL_MONEY_ENABLED else "SANDBOX"}
@app.get("/api/v1/status")
def status(): return {"product":"NAQAA Market","version":APP_VERSION,"wallet_only":True,"card_payments":False,"stripe_live":False,"money":"disabled" if not REAL_MONEY_ENABLED else "enabled","kyc_kyb_required":True,"ledger":"double_entry","precision":"integer_cents","idempotency":True,"reconciliation":"/api/v1/finance/reconciliation"}

@app.post("/api/v1/auth/register")
def register(x:Register):
    email=x.email.strip().lower(); c=db()
    if c.execute("SELECT id FROM accounts WHERE lower(email)=?",(email,)).fetchone(): c.close(); raise HTTPException(409,"email already registered")
    i=str(uuid.uuid4()); last4=x.identity_number.replace(" ","")[-4:] if x.identity_number else ""; c.execute("""INSERT INTO accounts(id,role,name,email,status,created_at,password_hash,account_type,dob,nationality,phone,identity_type,identity_last4,identity_country,company_name,company_registration) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(i,x.role,x.name.strip(),email,"pending",now(),hp(x.password),x.account_type,x.dob,x.nationality.strip(),x.phone.strip(),x.identity_type,last4,x.identity_country.strip(),x.company_name.strip(),x.company_registration.strip()))
    w=str(uuid.uuid4()); c.execute("INSERT INTO wallets VALUES(?,?,?,?,?)",(w,i,"USD",0,now())); c.commit(); c.close()
    return {"id":i,"role":x.role,"status":"pending","message":"Account created. Verification/KYC-KYB may be required."}

@app.post("/api/v1/auth/login")
def login(x:Login):
    c=db(); a=c.execute("SELECT * FROM accounts WHERE lower(email)=?",(x.email.strip().lower(),)).fetchone()
    if not a or not a["password_hash"] or not vp(x.password,a["password_hash"]): c.close(); raise HTTPException(401,"invalid email or password")
    t=secrets.token_urlsafe(32); c.execute("INSERT INTO sessions VALUES(?,?,?)",(t,a["id"],now())); c.commit(); c.close()
    return {"token":t,"user":account_public(a)}

@app.get("/api/v1/auth/me")
def me(token:str):
    c=db(); a=account_for(c,token); c.close()
    return account_public(a)

@app.get("/api/v1/wallet/{account_id}")
def wallet(account_id:str,token:str):
    c=db(); a=account_for(c,token)
    if a["id"]!=account_id: c.close(); raise HTTPException(403,"wallet access denied")
    try: snap=wallet_snapshot(c,account_id)
    except ValueError as e: c.close(); raise HTTPException(404,str(e))
    tx=c.execute("SELECT type,amount,currency,reference,description,status,created_at FROM wallet_transactions WHERE wallet_id=? ORDER BY created_at DESC LIMIT 100",(snap["wallet_id"],)).fetchall()
    c.close(); return {**snap,"transactions":[dict(x) for x in tx]}

def require_admin(request:Request):
    if not ADMIN_API_KEY: raise HTTPException(503,"financial admin controls are not configured")
    if not secrets.compare_digest(request.headers.get("X-Admin-Key",""),ADMIN_API_KEY): raise HTTPException(403,"admin authorization required")

def idem(request:Request):
    key=request.headers.get("Idempotency-Key","").strip()
    if not key: raise HTTPException(400,"Idempotency-Key header is required for financial operations")
    if len(key)>255: raise HTTPException(400,"Idempotency-Key is too long")
    return key

@app.post("/api/v1/wallet/deposit-request")
def deposit(x:WalletRequest,token:str,request:Request):
    key=idem(request); c=db(); a=account_for(c,token)
    if a["id"]!=x.account_id: c.close(); raise HTTPException(403,"wallet access denied")
    try: cents=to_cents(x.amount)
    except ValueError as e: c.close(); raise HTTPException(400,str(e))
    if x.currency!="USD": c.close(); raise HTTPException(400,"only USD is enabled")
    c.execute("BEGIN IMMEDIATE")
    old=c.execute("SELECT * FROM wallet_requests WHERE idempotency_key=?",(key,)).fetchone()
    if old:
        c.commit(); c.close(); return {"request_id":old["id"],"reference":old["reference"],"status":old["status"],"duplicate":True}
    rid=str(uuid.uuid4()); ref="DEP-"+uuid.uuid4().hex[:10].upper()
    c.execute("INSERT INTO wallet_requests(id,account_id,type,amount,currency,status,reference,created_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",(rid,a["id"],"deposit",float(amount(cents)),"USD","pending",ref,now(),key))
    c.commit(); c.close()
    return {"request_id":rid,"reference":ref,"status":"pending","amount":float(amount(cents)),"message":"Deposit is pending. Balance changes only after authorized approval/provider settlement."}

@app.post("/api/v1/wallet/withdraw-request")
def withdraw(x:WalletRequest,token:str,request:Request):
    key=idem(request); c=db(); a=account_for(c,token)
    if a["id"]!=x.account_id: c.close(); raise HTTPException(403,"wallet access denied")
    try: cents=to_cents(x.amount)
    except ValueError as e: c.close(); raise HTTPException(400,str(e))
    if x.currency!="USD": c.close(); raise HTTPException(400,"only USD is enabled")
    c.execute("BEGIN IMMEDIATE")
    old=c.execute("SELECT * FROM wallet_requests WHERE idempotency_key=?",(key,)).fetchone()
    if old:
        c.commit(); c.close(); return {"request_id":old["id"],"reference":old["reference"],"status":old["status"],"duplicate":True}
    w=c.execute("SELECT * FROM wallets WHERE account_id=?",(a["id"],)).fetchone()
    if not w: c.rollback(); c.close(); raise HTTPException(404,"wallet not found")
    available=int(w["balance_cents"] or 0)-int(w["held_cents"] or 0)
    if cents>available: c.rollback(); c.close(); raise HTTPException(400,f"insufficient available balance: {float(amount(available)):.2f} USD")
    rid=str(uuid.uuid4()); ref="WDR-"+uuid.uuid4().hex[:10].upper()
    c.execute("UPDATE wallets SET held_cents=held_cents+?,updated_at=? WHERE id=?",(cents,now(),w["id"]))
    c.execute("INSERT INTO wallet_requests(id,account_id,type,amount,currency,status,reference,created_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",(rid,a["id"],"withdraw",float(amount(cents)),"USD","pending",ref,now(),key))
    c.commit(); c.close()
    return {"request_id":rid,"reference":ref,"status":"pending","amount":float(amount(cents)),"message":"Withdrawal is pending and the amount is reserved from available balance."}

@app.post("/api/v1/wallet/pay")
def pay(x:WalletPay,token:str,request:Request):
    key=idem(request); c=db(); a=account_for(c,token)
    if a["id"]!=x.from_account_id: c.close(); raise HTTPException(403,"payment source denied")
    if x.from_account_id==x.to_account_id: c.close(); raise HTTPException(400,"source and destination must differ")
    if x.currency!="USD": c.close(); raise HTTPException(400,"only USD is enabled")
    try: cents=to_cents(x.amount)
    except ValueError as e: c.close(); raise HTTPException(400,str(e))
    c.execute("BEGIN IMMEDIATE")
    existing=c.execute("SELECT * FROM journal_entries WHERE idempotency_key=?",(key,)).fetchone()
    if existing:
        c.commit(); c.close(); return {"reference":existing["reference"],"status":"completed","duplicate":True,"wallet_only":True}
    s=c.execute("SELECT * FROM accounts WHERE id=?",(x.from_account_id,)).fetchone(); r=c.execute("SELECT * FROM accounts WHERE id=?",(x.to_account_id,)).fetchone()
    if not s or not r: c.rollback(); c.close(); raise HTTPException(404,"account not found")
    if r["role"]!="seller": c.rollback(); c.close(); raise HTTPException(400,"destination must be a seller wallet")
    fw=c.execute("SELECT * FROM wallets WHERE account_id=?",(x.from_account_id,)).fetchone(); tw=c.execute("SELECT * FROM wallets WHERE account_id=?",(x.to_account_id,)).fetchone()
    available=int(fw["balance_cents"] or 0)-int(fw["held_cents"] or 0)
    if cents>available: c.rollback(); c.close(); raise HTTPException(400,"insufficient available balance")
    fa=ensure_wallet_ledger(c,x.from_account_id,"USD",fw["id"]); ta=ensure_wallet_ledger(c,x.to_account_id,"USD",tw["id"])
    ref="PAY-"+uuid.uuid4().hex[:10].upper()
    post_entry(c,ref,"wallet_transfer",[{"account_id":fa,"side":"debit","amount_cents":cents},{"account_id":ta,"side":"credit","amount_cents":cents}],x.description or "Marketplace wallet payment",key)
    set_wallet_balance(c,fw["id"],int(fw["balance_cents"])-cents,int(fw["held_cents"] or 0))
    set_wallet_balance(c,tw["id"],int(tw["balance_cents"])+cents,int(tw["held_cents"] or 0))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),fw["id"],"payment",float(amount(cents)),"USD",ref,x.description,"completed",now()))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),tw["id"],"receipt",float(amount(cents)),"USD",ref,x.description,"completed",now()))
    c.commit(); c.close()
    return {"reference":ref,"status":"completed","amount":float(amount(cents)),"currency":"USD","from_account_id":x.from_account_id,"to_account_id":x.to_account_id,"wallet_only":True}

@app.get("/api/v1/finance/reconciliation")
def reconciliation():
    c=db()
    bad=[]
    for e in c.execute("SELECT id,reference FROM journal_entries ORDER BY created_at").fetchall():
        d=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM journal_lines WHERE entry_id=? AND side='debit'",(e["id"],)).fetchone()["n"]
        cr=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM journal_lines WHERE entry_id=? AND side='credit'",(e["id"],)).fetchone()["n"]
        if d!=cr: bad.append({"reference":e["reference"],"debit_cents":d,"credit_cents":cr})
    negatives=c.execute("SELECT id,account_id,balance_cents,held_cents FROM wallets WHERE balance_cents<0 OR held_cents<0 OR held_cents>balance_cents").fetchall()
    c.close()
    return {"ok":not bad and not negatives,"unbalanced_entries":bad,"invalid_wallets":[dict(x) for x in negatives],"checked":"double_entry_and_wallet_invariants"}

@app.get("/api/v1/finance/journal/{reference}")
def journal(reference:str):
    c=db(); e=c.execute("SELECT * FROM journal_entries WHERE reference=?",(reference,)).fetchone()
    if not e: c.close(); raise HTTPException(404,"journal entry not found")
    lines=c.execute("SELECT ledger_account_id,side,amount_cents,created_at FROM journal_lines WHERE entry_id=?",(e["id"],)).fetchall(); c.close()
    return {"entry":dict(e),"lines":[{**dict(x),"amount":float(amount(x["amount_cents"]))} for x in lines]}

@app.post("/api/v1/admin/wallet-requests/{request_id}/approve")
def approve_wallet_request(request_id:str,request:Request):
    require_admin(request); c=db(); c.execute("BEGIN IMMEDIATE")
    q=c.execute("SELECT * FROM wallet_requests WHERE id=?",(request_id,)).fetchone()
    if not q: c.rollback(); c.close(); raise HTTPException(404,"wallet request not found")
    if q["status"]!="pending": c.rollback(); c.close(); return {"request_id":request_id,"status":q["status"],"duplicate":True}
    w=c.execute("SELECT * FROM wallets WHERE account_id=?",(q["account_id"],)).fetchone(); ledger=ensure_wallet_ledger(c,q["account_id"],q["currency"],w["id"])
    cents=to_cents(q["amount"]); ref=q["reference"]
    if q["type"]=="deposit":
        post_entry(c,ref,"deposit",[{"account_id":"SYSTEM:CASH","side":"debit","amount_cents":cents},{"account_id":ledger,"side":"credit","amount_cents":cents}],"Approved deposit",q["idempotency_key"]+"::approval")
        set_wallet_balance(c,w["id"],int(w["balance_cents"])+cents,int(w["held_cents"] or 0))
    elif q["type"]=="withdraw":
        held=int(w["held_cents"] or 0)
        if cents>held: c.rollback(); c.close(); raise HTTPException(409,"withdrawal hold is inconsistent")
        post_entry(c,ref,"withdrawal",[{"account_id":ledger,"side":"debit","amount_cents":cents},{"account_id":"SYSTEM:CASH","side":"credit","amount_cents":cents}],"Approved withdrawal",q["idempotency_key"]+"::approval")
        set_wallet_balance(c,w["id"],int(w["balance_cents"])-cents,held-cents)
    else:
        c.rollback(); c.close(); raise HTTPException(400,"unsupported wallet request type")
    c.execute("UPDATE wallet_requests SET status='approved',approved_at=? WHERE id=?",(now(),request_id)); c.commit(); c.close()
    return {"request_id":request_id,"status":"approved","reference":ref}

@app.post("/api/v1/admin/wallet-requests/{request_id}/reject")
def reject_wallet_request(request_id:str,request:Request):
    require_admin(request); c=db(); c.execute("BEGIN IMMEDIATE")
    q=c.execute("SELECT * FROM wallet_requests WHERE id=?",(request_id,)).fetchone()
    if not q: c.rollback(); c.close(); raise HTTPException(404,"wallet request not found")
    if q["status"]!="pending": c.rollback(); c.close(); return {"request_id":request_id,"status":q["status"],"duplicate":True}
    if q["type"]=="withdraw":
        w=c.execute("SELECT * FROM wallets WHERE account_id=?",(q["account_id"],)).fetchone(); cents=to_cents(q["amount"]); held=int(w["held_cents"] or 0)
        if cents>held: c.rollback(); c.close(); raise HTTPException(409,"withdrawal hold is inconsistent")
        set_wallet_balance(c,w["id"],int(w["balance_cents"]),held-cents)
    c.execute("UPDATE wallet_requests SET status='rejected',approved_at=?,rejection_reason=? WHERE id=?",(now(),"Rejected by authorized finance administrator",request_id)); c.commit(); c.close()
    return {"request_id":request_id,"status":"rejected"}

@app.post("/api/v1/listings")
def listing(x:Listing):
    c=db(); s=c.execute("SELECT id,role FROM accounts WHERE id=?",(x.seller_id,)).fetchone()
    if not s or s["role"]!="seller": c.close(); raise HTTPException(400,"seller account not found")
    i=str(uuid.uuid4()); c.execute("INSERT INTO listings VALUES(?,?,?,?,?,?,?,?,?)",(i,x.seller_id,x.category,x.title,x.description,x.amount,x.currency,"draft",now())); c.commit(); c.close(); return {"id":i,"status":"draft"}
@app.post("/api/v1/listings/{listing_id}/publish")
def publish(listing_id:str):
    c=db(); r=c.execute("SELECT id FROM listings WHERE id=?",(listing_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"listing not found")
    c.execute("UPDATE listings SET status='published' WHERE id=?",(listing_id,)); c.commit(); c.close(); return {"id":listing_id,"status":"published"}
@app.get("/api/v1/listings")
def listings():
    c=db(); rows=c.execute("SELECT id,category,title,description,amount,currency,status,created_at FROM listings WHERE status='published' ORDER BY created_at DESC").fetchall(); c.close(); return [dict(r) for r in rows]
@app.post("/api/v1/offers")
def offer(x:Offer):
    c=db(); l=c.execute("SELECT id,status FROM listings WHERE id=?",(x.listing_id,)).fetchone(); b=c.execute("SELECT id,role FROM accounts WHERE id=?",(x.buyer_id,)).fetchone()
    if not l or l["status"]!="published": c.close(); raise HTTPException(400,"listing unavailable")
    if not b or b["role"]!="buyer": c.close(); raise HTTPException(400,"buyer account not found")
    i=str(uuid.uuid4()); c.execute("INSERT INTO offers VALUES(?,?,?,?,?,?,?)",(i,x.listing_id,x.buyer_id,x.amount,x.currency,"pending",now())); c.commit(); c.close(); return {"id":i,"status":"pending"}
@app.post("/api/v1/offers/{offer_id}/accept")
def accept(offer_id:str):
    c=db(); r=c.execute("SELECT * FROM offers WHERE id=?",(offer_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"offer not found")
    c.execute("UPDATE offers SET status='accepted' WHERE id=?",(offer_id,)); c.commit(); c.close(); return {"id":offer_id,"status":"accepted","payment":"wallet_only"}
@app.post("/api/v1/contracts/{offer_id}")
def contract(offer_id:str):
    c=db(); r=c.execute("SELECT * FROM offers WHERE id=? AND status='accepted'",(offer_id,)).fetchone()
    if not r: c.close(); raise HTTPException(400,"accepted offer required")
    cid="NQ-EC-"+uuid.uuid4().hex[:10].upper(); h=hashlib.sha256(f"{cid}|{offer_id}|{r['amount']}|{r['currency']}".encode()).hexdigest()
    c.execute("INSERT INTO contracts VALUES(?,?,?,?,?)",(cid,offer_id,h,"awaiting_signatures",now())); c.commit(); c.close(); return {"contract_id":cid,"sha256":h,"status":"awaiting_signatures"}
@app.post("/api/v1/webhooks")
def webhook(payload:dict):
    if REAL_MONEY_ENABLED: raise HTTPException(501,"live provider webhook verification is not configured")
    eid=payload.get("event_id")
    if not eid: raise HTTPException(400,"event_id required")
    c=db()
    try: c.execute("INSERT INTO webhooks VALUES(?,?,?,?,?)",(str(uuid.uuid4()),eid,"processed",now())); c.commit(); first=True
    except sqlite3.IntegrityError: first=False
    c.close(); return {"accepted":True,"duplicate":not first,"mode":"sandbox"}
@app.get("/api/v1/production-readiness")
def readiness():
    return {"wallet_only":True,"real_money_enabled":REAL_MONEY_ENABLED,"requirements":["legal entity/provider approval","KYC/KYB","verified provider webhooks","persistent production database","production secrets","withdrawal approval controls"]}
