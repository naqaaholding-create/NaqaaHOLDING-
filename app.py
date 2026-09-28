import os, sqlite3, hashlib, uuid, secrets, base64
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from finance_ledger import ensure_schema, ensure_wallet_ledger, post_entry, set_wallet_balance, wallet_snapshot, to_cents, amount

APP_VERSION="6.6.0"
DB=os.getenv("DATABASE_PATH","naqaa_market.db")
REAL_MONEY_ENABLED=os.getenv("REAL_MONEY_ENABLED","0")=="1"
ADMIN_API_KEY=os.getenv("NAQAA_ADMIN_KEY","")
app=FastAPI(title="NAQAA Market API",version=APP_VERSION)

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    # Allow concurrent API workers to wait briefly for SQLite writers instead of failing with SQLITE_BUSY.
    c=sqlite3.connect(DB, timeout=30.0)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c

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
    c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES(?,?,?,?,?)",("SYSTEM:COMMISSION_REVENUE","revenue","SYSTEM","USD",now()))
    c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES(?,?,?,?,?)",("SYSTEM:COMPANY_WALLET","system","COMPANY","USD",now()))
    for col,typ in [("buyer_fee_cents","INTEGER NOT NULL DEFAULT 0"),("seller_fee_cents","INTEGER NOT NULL DEFAULT 0"),("buyer_total_cents","INTEGER NOT NULL DEFAULT 0"),("seller_net_cents","INTEGER NOT NULL DEFAULT 0"),("payment_reference","TEXT"),("paid_at","TEXT"),("commission_rule_id","TEXT"),("buyer_rate_bps","INTEGER NOT NULL DEFAULT 0"),("seller_rate_bps","INTEGER NOT NULL DEFAULT 0"),("buyer_fixed_cents","INTEGER NOT NULL DEFAULT 0"),("seller_fixed_cents","INTEGER NOT NULL DEFAULT 0"),("minimum_fee_cents","INTEGER NOT NULL DEFAULT 0"),("maximum_fee_cents","INTEGER")]:
        try: c.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
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
    return PlainTextResponse(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
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
    w=str(uuid.uuid4()); c.execute("INSERT INTO wallets(id,account_id,currency,balance,updated_at,balance_cents,held_cents) VALUES(?,?,?,?,?,?,?)",(w,i,"USD",0,now(),0,0)); ensure_wallet_ledger(c,i,"USD",w); c.commit(); c.close()
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
    expected=os.getenv("NAQAA_ADMIN_KEY") or ADMIN_API_KEY
    if not expected: raise HTTPException(503,"financial admin controls are not configured")
    if not secrets.compare_digest(request.headers.get("X-Admin-Key",""),expected): raise HTTPException(403,"admin authorization required")

def audit(c, actor_id, action, entity, entity_id=None, old_value=None, new_value=None, request=None):
    c.execute(
        """INSERT INTO audit_logs(id,actor_id,action,entity,entity_id,old_value,new_value,ip,device,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), actor_id, action, entity, entity_id,
         None if old_value is None else str(old_value),
         None if new_value is None else str(new_value),
         None if request is None else (request.client.host if request.client else None),
         None if request is None else request.headers.get("user-agent"), now()),
    )

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
        if old["account_id"]!=a["id"] or old["type"]!="deposit" or old["currency"]!=x.currency or to_cents(old["amount"])!=cents:
            c.rollback(); c.close(); raise HTTPException(409,"Idempotency-Key was already used for a different deposit request")
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
        if old["account_id"]!=a["id"] or old["type"]!="withdraw" or old["currency"]!=x.currency or to_cents(old["amount"])!=cents:
            c.rollback(); c.close(); raise HTTPException(409,"Idempotency-Key was already used for a different withdrawal request")
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
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),fw["id"],"payment",float(amount(cents)),"USD",ref,x.description,"completed",now()))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),tw["id"],"receipt",float(amount(cents)),"USD",ref,x.description,"completed",now()))
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
    wallet_mismatches=[]
    for w in c.execute("SELECT id,account_id,balance_cents,held_cents FROM wallets").fetchall():
        lid="WALLET:"+w["id"]
        cr=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM journal_lines WHERE ledger_account_id=? AND side='credit'",(lid,)).fetchone()["n"]
        dr=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM journal_lines WHERE ledger_account_id=? AND side='debit'",(lid,)).fetchone()["n"]
        ledger_balance=int(cr or 0)-int(dr or 0)
        if ledger_balance!=int(w["balance_cents"] or 0):
            wallet_mismatches.append({"account_id":w["account_id"],"stored_balance_cents":int(w["balance_cents"] or 0),"ledger_balance_cents":ledger_balance})
    negatives=c.execute("SELECT id,account_id,balance_cents,held_cents FROM wallets WHERE balance_cents<0 OR held_cents<0 OR held_cents>balance_cents").fetchall()
    c.close()
    return {"ok":not bad and not negatives and not wallet_mismatches,"unbalanced_entries":bad,"wallet_mismatches":wallet_mismatches,"invalid_wallets":[dict(x) for x in negatives],"checked":"double_entry_wallet_balance_and_invariants"}

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

def active_commission(c, transaction_type, currency="USD"):
    return c.execute("""
      SELECT * FROM commission_rules
      WHERE transaction_type=? AND currency=? AND status='active'
        AND effective_from<=?
        AND (effective_to IS NULL OR effective_to>?)
      ORDER BY datetime(effective_from) DESC LIMIT 1
    """,(transaction_type,currency,now(),now())).fetchone()

def fee_cents(amount_cents, rate_bps, fixed_cents, minimum_cents=0, maximum_cents=None):
    fee=(amount_cents*int(rate_bps)+5000)//10000+int(fixed_cents)
    fee=max(fee,int(minimum_cents))
    if maximum_cents is not None:
        fee=min(fee,int(maximum_cents))
    return fee

class KYCSubmit(BaseModel):
    document_type:str=Field(pattern="^(passport|national_id)$")
    identity_country:str

class KYBSubmit(BaseModel):
    company_name:str
    registration_number:str

@app.post("/api/v1/compliance/kyc")
def submit_kyc(x:KYCSubmit,token:str):
    c=db(); a=account_for(c,token); cid=str(uuid.uuid4())
    c.execute("INSERT INTO kyc_cases(id,user_id,document_type,status,risk_level,created_at) VALUES(?,?,?,?,?,?)",
              (cid,a["id"],x.document_type,"pending","standard",now()))
    c.commit(); c.close()
    return {"case_id":cid,"status":"pending","message":"KYC case submitted for review"}

@app.post("/api/v1/compliance/kyb")
def submit_kyb(x:KYBSubmit,token:str):
    c=db(); a=account_for(c,token)
    if a["account_type"]!="company":
        c.close(); raise HTTPException(400,"KYB is available for company accounts")
    cid=str(uuid.uuid4())
    c.execute("INSERT INTO kyb_cases(id,user_id,company_name,registration_number,status,risk_level,created_at) VALUES(?,?,?,?,?,?,?)",
              (cid,a["id"],x.company_name.strip(),x.registration_number.strip(),"pending","standard",now()))
    c.commit(); c.close()
    return {"case_id":cid,"status":"pending","message":"KYB case submitted for review"}

@app.get("/api/v1/compliance/status")
def compliance_status(token:str):
    c=db(); a=account_for(c,token)
    kyc=c.execute("SELECT * FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1",(a["id"],)).fetchone()
    kyb=c.execute("SELECT * FROM kyb_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1",(a["id"],)).fetchone()
    c.close()
    return {"account_id":a["id"],"kyc":dict(kyc) if kyc else None,"kyb":dict(kyb) if kyb else None,
            "production_eligible":bool((kyc and kyc["status"]=="approved") or (kyb and kyb["status"]=="approved"))}

@app.post("/api/v1/admin/compliance/{case_type}/{case_id}/{decision}")
def review_compliance(case_type:str,case_id:str,decision:str,request:Request):
    require_admin(request)
    if case_type not in ("kyc","kyb") or decision not in ("approve","reject"):
        raise HTTPException(400,"invalid compliance review request")
    c=db(); table="kyc_cases" if case_type=="kyc" else "kyb_cases"
    q=c.execute(f"SELECT * FROM {table} WHERE id=?",(case_id,)).fetchone()
    if not q: c.close(); raise HTTPException(404,"compliance case not found")
    status="approved" if decision=="approve" else "rejected"
    c.execute(f"UPDATE {table} SET status=?,reviewed_by=?,reviewed_at=?,rejection_reason=? WHERE id=?",
              (status,"finance-admin",now(),None if status=="approved" else "Rejected by authorized administrator",case_id))
    c.commit(); c.close()
    return {"case_id":case_id,"status":status}

class CommissionRule(BaseModel):
    transaction_type:str=Field(default="marketplace")
    currency:str="USD"
    buyer_rate_bps:int=Field(default=0,ge=0,le=10000)
    seller_rate_bps:int=Field(default=0,ge=0,le=10000)
    buyer_fixed_cents:int=Field(default=0,ge=0)
    seller_fixed_cents:int=Field(default=0,ge=0)
    minimum_fee_cents:int=Field(default=0,ge=0)
    maximum_fee_cents:int|None=Field(default=None,ge=0)

@app.post("/api/v1/admin/commission-rules")
def create_commission_rule(x:CommissionRule,request:Request):
    require_admin(request); c=db(); rid=str(uuid.uuid4())
    c.execute("""INSERT INTO commission_rules
      (id,transaction_type,currency,buyer_rate,seller_rate,buyer_fixed,seller_fixed,minimum_fee,maximum_fee,effective_from,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
      (rid,x.transaction_type,x.currency,x.buyer_rate_bps/10000,x.seller_rate_bps/10000,
       x.buyer_fixed_cents/100,x.seller_fixed_cents/100,x.minimum_fee_cents/100,
       None if x.maximum_fee_cents is None else x.maximum_fee_cents/100,now(),"active"))
    c.commit(); c.close()
    return {"rule_id":rid,"status":"active"}

@app.post("/api/v1/finance/commission-preview")
def commission_preview(amount_value:float,token:str):
    c=db(); account_for(c,token)
    cents=to_cents(amount_value); r=active_commission(c,"marketplace","USD"); c.close()
    if not r:
        return {"gross":float(amount(cents)),"buyer_fee":0.0,"seller_fee":0.0,"total_commission":0.0,"rule":"none"}
    bf=fee_cents(cents,round(float(r["buyer_rate"])*10000),round(float(r["buyer_fixed"])*100),
                 round(float(r["minimum_fee"])*100),None if r["maximum_fee"] is None else round(float(r["maximum_fee"])*100))
    sf=fee_cents(cents,round(float(r["seller_rate"])*10000),round(float(r["seller_fixed"])*100),
                 round(float(r["minimum_fee"])*100),None if r["maximum_fee"] is None else round(float(r["maximum_fee"])*100))
    return {"gross":float(amount(cents)),"buyer_fee":float(amount(bf)),"seller_fee":float(amount(sf)),
            "total_commission":float(amount(bf+sf)),"rule_id":r["id"]}

@app.post("/api/v1/listings")
def listing(x:Listing,token:str):
    c=db(); actor=account_for(c,token)
    if actor["role"]!="seller" or actor["id"]!=x.seller_id:
        c.close(); raise HTTPException(403,"only the authenticated seller can create their listing")
    if x.currency!="USD":
        c.close(); raise HTTPException(400,"only USD is enabled")
    try: cents=to_cents(x.amount)
    except ValueError as e:
        c.close(); raise HTTPException(400,str(e))
    i=str(uuid.uuid4()); c.execute("INSERT INTO listings VALUES(?,?,?,?,?,?,?,?,?)",(i,actor["id"],x.category,x.title.strip(),x.description.strip(),float(amount(cents)),"USD","draft",now())); c.commit(); c.close(); return {"id":i,"status":"draft"}
@app.post("/api/v1/listings/{listing_id}/publish")
def publish(listing_id:str,token:str):
    c=db(); actor=account_for(c,token)
    r=c.execute("SELECT id,seller_id FROM listings WHERE id=?",(listing_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"listing not found")
    if actor["id"]!=r["seller_id"] or actor["role"]!="seller":
        c.close(); raise HTTPException(403,"only the listing seller can publish it")
    c.execute("UPDATE listings SET status='published' WHERE id=?",(listing_id,)); c.commit(); c.close(); return {"id":listing_id,"status":"published"}
@app.get("/api/v1/listings")
def listings():
    c=db(); rows=c.execute("SELECT id,category,title,description,amount,currency,status,created_at FROM listings WHERE status='published' ORDER BY created_at DESC").fetchall(); c.close(); return [dict(r) for r in rows]
@app.post("/api/v1/offers")
def offer(x:Offer,token:str):
    c=db(); actor=account_for(c,token)
    if actor["role"]!="buyer" or actor["id"]!=x.buyer_id:
        c.close(); raise HTTPException(403,"only the authenticated buyer can create their offer")
    l=c.execute("SELECT id,status,amount,currency FROM listings WHERE id=?",(x.listing_id,)).fetchone()
    if not l or l["status"]!="published": c.close(); raise HTTPException(400,"listing unavailable")
    if x.currency!="USD": c.close(); raise HTTPException(400,"only USD is enabled")
    try: cents=to_cents(x.amount)
    except ValueError as e: c.close(); raise HTTPException(400,str(e))
    if cents<=0: c.close(); raise HTTPException(400,"offer amount must be greater than zero")
    i=str(uuid.uuid4()); c.execute("INSERT INTO offers VALUES(?,?,?,?,?,?,?)",(i,x.listing_id,actor["id"],float(amount(cents)),"USD","pending",now())); c.commit(); c.close(); return {"id":i,"status":"pending"}
@app.post("/api/v1/offers/{offer_id}/accept")
def accept(offer_id:str,token:str):
    c=db(); actor=account_for(c,token)
    r=c.execute("""SELECT o.*,l.seller_id,l.status listing_status FROM offers o
                   JOIN listings l ON l.id=o.listing_id WHERE o.id=?""",(offer_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"offer not found")
    if actor["id"]!=r["seller_id"] or actor["role"]!="seller":
        c.close(); raise HTTPException(403,"only the listing seller can accept this offer")
    if r["status"]!="pending" or r["listing_status"]!="published":
        c.close(); raise HTTPException(409,"offer is no longer pending")
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE offers SET status='accepted' WHERE id=? AND status='pending'",(offer_id,))
    if c.execute("SELECT changes()").fetchone()[0]!=1:
        c.rollback(); c.close(); raise HTTPException(409,"offer was already processed")
    order_id=str(uuid.uuid4()); gross=to_cents(r["amount"])
    c.execute("""INSERT INTO orders(id,listing_id,buyer_id,seller_id,gross_cents,commission_cents,status,created_at,buyer_fee_cents,seller_fee_cents,buyer_total_cents,seller_net_cents)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(order_id,r["listing_id"],r["buyer_id"],r["seller_id"],gross,0,"awaiting_payment",now(),0,0,gross,gross))
    c.commit(); c.close()
    return {"id":offer_id,"status":"accepted","order_id":order_id,"payment_status":"awaiting_payment","wallet_only":True}
@app.post("/api/v1/orders/{order_id}/pay")
def pay_order(order_id:str,token:str,request:Request):
    key=idem(request); c=db(); buyer=account_for(c,token)
    if buyer["role"]!="buyer": c.close(); raise HTTPException(403,"only a buyer can pay an order")
    c.execute("BEGIN IMMEDIATE")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.rollback(); c.close(); raise HTTPException(404,"order not found")
    if o["buyer_id"]!=buyer["id"]: c.rollback(); c.close(); raise HTTPException(403,"order access denied")
    if o["status"]=="paid":
        ref=o["payment_reference"]; c.commit(); c.close(); return {"order_id":order_id,"status":"paid","reference":ref,"duplicate":True}
    if o["status"]!="awaiting_payment": c.rollback(); c.close(); raise HTTPException(409,"order is not payable")
    kyc=c.execute("SELECT status FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1",(buyer["id"],)).fetchone()
    if not kyc or kyc["status"]!="approved":
        c.rollback(); c.close(); raise HTTPException(403,"approved KYC is required before marketplace payment")
    seller_kyc=c.execute("SELECT status FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1",(o["seller_id"],)).fetchone()
    if not seller_kyc or seller_kyc["status"]!="approved":
        c.rollback(); c.close(); raise HTTPException(403,"seller verification is required before marketplace settlement")
    rule=active_commission(c,"marketplace","USD"); gross=int(o["gross_cents"])
    if rule:
        bf=fee_cents(gross,round(float(rule["buyer_rate"])*10000),round(float(rule["buyer_fixed"])*100),round(float(rule["minimum_fee"])*100),None if rule["maximum_fee"] is None else round(float(rule["maximum_fee"])*100))
        sf=fee_cents(gross,round(float(rule["seller_rate"])*10000),round(float(rule["seller_fixed"])*100),round(float(rule["minimum_fee"])*100),None if rule["maximum_fee"] is None else round(float(rule["maximum_fee"])*100))
    else: bf=sf=0
    buyer_total=gross+bf; seller_net=gross-sf
    if seller_net<=0: c.rollback(); c.close(); raise HTTPException(400,"commission leaves no positive seller settlement")
    existing_journal=c.execute("SELECT id,reference,entry_type FROM journal_entries WHERE idempotency_key=?",(key,)).fetchone()
    if existing_journal:
        if existing_journal["entry_type"]!="marketplace_settlement":
            c.rollback(); c.close(); raise HTTPException(409,"Idempotency-Key was already used for a different financial operation")
        linked=c.execute("SELECT id,status FROM orders WHERE payment_reference=?",(existing_journal["reference"],)).fetchone()
        if not linked or linked["id"]!=order_id:
            c.rollback(); c.close(); raise HTTPException(409,"Idempotency-Key was already used for a different order")
        c.commit(); c.close(); return {"order_id":order_id,"status":"paid","reference":existing_journal["reference"],"duplicate":True}
    bw=c.execute("SELECT * FROM wallets WHERE account_id=?",(buyer["id"],)).fetchone(); sw=c.execute("SELECT * FROM wallets WHERE account_id=?",(o["seller_id"],)).fetchone()
    if not bw or not sw: c.rollback(); c.close(); raise HTTPException(404,"buyer or seller wallet not found")
    available=int(bw["balance_cents"] or 0)-int(bw["held_cents"] or 0)
    if buyer_total>available: c.rollback(); c.close(); raise HTTPException(400,"insufficient available buyer balance including commission")
    bl=ensure_wallet_ledger(c,buyer["id"],"USD",bw["id"]); sl=ensure_wallet_ledger(c,o["seller_id"],"USD",sw["id"])
    ref="ORD-"+uuid.uuid4().hex[:10].upper()
    lines=[{"account_id":bl,"side":"debit","amount_cents":buyer_total},{"account_id":sl,"side":"credit","amount_cents":seller_net}]
    if bf+sf: lines.append({"account_id":"SYSTEM:COMMISSION_REVENUE","side":"credit","amount_cents":bf+sf})
    post_entry(c,ref,"marketplace_settlement",lines,"Marketplace settlement with buyer and seller commissions",key)
    set_wallet_balance(c,bw["id"],int(bw["balance_cents"])-buyer_total,int(bw["held_cents"] or 0))
    for side,fee,rate,fixed in [("buyer",bf,0 if not rule else round(float(rule["buyer_rate"])*10000),0 if not rule else round(float(rule["buyer_fixed"])*100)),
                                ("seller",sf,0 if not rule else round(float(rule["seller_rate"])*10000),0 if not rule else round(float(rule["seller_fixed"])*100))]:
        c.execute("INSERT INTO commission_entries(id,order_id,side,amount_cents,currency,rate_bps,fixed_cents,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()),order_id,side,fee,"USD",rate,fixed,"posted",now()))
    c.execute("""UPDATE orders SET commission_cents=?,buyer_fee_cents=?,seller_fee_cents=?,buyer_total_cents=?,seller_net_cents=?,payment_reference=?,paid_at=?,status='paid' WHERE id=?""",
              (bf+sf,bf,sf,buyer_total,seller_net,ref,now(),order_id))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),bw["id"],"marketplace_payment",float(amount(buyer_total)),"USD",ref,"Marketplace order payment incl. buyer commission","completed",now()))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),sw["id"],"marketplace_settlement",float(amount(seller_net)),"USD",ref,"Marketplace seller settlement net of commission","completed",now()))
    set_wallet_balance(c,sw["id"],int(sw["balance_cents"])+seller_net,int(sw["held_cents"] or 0)+seller_net)
    c.execute("INSERT INTO escrow_transactions(id,order_id,amount_cents,currency,status,held_at,released_at) VALUES(?,?,?,?,?,?,?)",
              (str(uuid.uuid4()),order_id,seller_net,"USD","held",now(),None))
    audit(c,buyer["id"],"marketplace_payment","order",order_id,"awaiting_payment","paid",request)
    audit(c,buyer["id"],"escrow_hold","escrow",order_id,None,f"held:{seller_net}",request)
    c.commit(); c.close()
    return {"order_id":order_id,"status":"paid","reference":ref,"gross":float(amount(gross)),"buyer_fee":float(amount(bf)),"seller_fee":float(amount(sf)),"buyer_total":float(amount(buyer_total)),"seller_net":float(amount(seller_net)),"currency":"USD","wallet_only":True}

@app.post("/api/v1/admin/orders/{order_id}/escrow/release")
def release_escrow(order_id:str,request:Request):
    require_admin(request)
    c=db(); c.execute("BEGIN IMMEDIATE")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.rollback(); c.close(); raise HTTPException(404,"order not found")
    if o["status"]!="paid": c.rollback(); c.close(); raise HTTPException(409,"only paid orders can release escrow")
    e=c.execute("SELECT * FROM escrow_transactions WHERE order_id=? ORDER BY held_at DESC LIMIT 1",(order_id,)).fetchone()
    if not e: c.rollback(); c.close(); raise HTTPException(404,"escrow record not found")
    if e["status"]=="released":
        c.commit(); c.close(); return {"order_id":order_id,"status":"released","duplicate":True}
    if e["status"]!="held": c.rollback(); c.close(); raise HTTPException(409,"escrow is not releasable")
    seller=c.execute("SELECT * FROM wallets WHERE account_id=?",(o["seller_id"],)).fetchone()
    seller_net=int(e["amount_cents"])
    if not seller or int(seller["held_cents"] or 0)<seller_net:
        c.rollback(); c.close(); raise HTTPException(409,"escrow hold is inconsistent")
    set_wallet_balance(c,seller["id"],int(seller["balance_cents"]),int(seller["held_cents"])-seller_net)
    c.execute("UPDATE escrow_transactions SET status='released',released_at=? WHERE id=? AND status='held'",(now(),e["id"]))
    if c.execute("SELECT changes()").fetchone()[0]!=1:
        c.rollback(); c.close(); raise HTTPException(409,"escrow was already released")
    audit(c,"finance-admin","escrow_release","escrow",order_id,"held","released",request)
    c.commit(); c.close()
    return {"order_id":order_id,"status":"released","amount":float(amount(seller_net))}

class DisputeOpen(BaseModel):
    reason:str=Field(min_length=3,max_length=2000)

@app.post("/api/v1/orders/{order_id}/dispute")
def open_dispute(order_id:str,x:DisputeOpen,token:str):
    c=db(); actor=account_for(c,token); c.execute("BEGIN IMMEDIATE")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.rollback(); c.close(); raise HTTPException(404,"order not found")
    if actor["id"] not in (o["buyer_id"],o["seller_id"]):
        c.rollback(); c.close(); raise HTTPException(403,"order access denied")
    if o["status"]!="paid":
        c.rollback(); c.close(); raise HTTPException(409,"only paid orders can be disputed")
    existing=c.execute("SELECT id,status FROM disputes WHERE transaction_reference=? AND status IN ('open','under_review')",(o["payment_reference"],)).fetchone()
    if existing:
        c.rollback(); c.close(); return {"dispute_id":existing["id"],"status":existing["status"],"duplicate":True}
    did=str(uuid.uuid4())
    c.execute("INSERT INTO disputes(id,transaction_reference,opened_by,amount_cents,reason,status,created_at) VALUES(?,?,?,?,?,?,?)",
              (did,o["payment_reference"],actor["id"],int(o["gross_cents"]),x.reason.strip(),"open",now()))
    c.commit(); c.close()
    return {"dispute_id":did,"order_id":order_id,"status":"open"}

@app.post("/api/v1/admin/disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id:str,decision:str,request:Request):
    require_admin(request)
    if decision not in ("approve_refund","reject"):
        raise HTTPException(400,"decision must be approve_refund or reject")
    c=db(); c.execute("BEGIN IMMEDIATE")
    d=c.execute("SELECT * FROM disputes WHERE id=?",(dispute_id,)).fetchone()
    if not d: c.rollback(); c.close(); raise HTTPException(404,"dispute not found")
    if d["status"] not in ("open","under_review"):
        c.rollback(); c.close(); return {"dispute_id":dispute_id,"status":d["status"],"duplicate":True}
    o=c.execute("SELECT * FROM orders WHERE payment_reference=?",(d["transaction_reference"],)).fetchone()
    if not o: c.rollback(); c.close(); raise HTTPException(404,"settled order not found")
    if decision=="reject":
        c.execute("UPDATE disputes SET status='rejected',resolution=?,resolved_at=? WHERE id=?",
                  ("Rejected by authorized dispute administrator",now(),dispute_id))
        c.commit(); c.close(); return {"dispute_id":dispute_id,"status":"rejected"}
    if o["status"]!="paid":
        c.rollback(); c.close(); raise HTTPException(409,"order is not refundable")
    already=c.execute("SELECT id FROM journal_entries WHERE reference=?",(f"REF-{o['id']}",)).fetchone()
    if already:
        c.rollback(); c.close(); raise HTTPException(409,"order refund already posted")
    buyer=c.execute("SELECT * FROM wallets WHERE account_id=?",(o["buyer_id"],)).fetchone()
    seller=c.execute("SELECT * FROM wallets WHERE account_id=?",(o["seller_id"],)).fetchone()
    if not buyer or not seller: c.rollback(); c.close(); raise HTTPException(404,"wallet not found")
    gross=int(o["gross_cents"]); bf=int(o["buyer_fee_cents"] or 0); sf=int(o["seller_fee_cents"] or 0)
    buyer_total=int(o["buyer_total_cents"] or (gross+bf)); seller_net=int(o["seller_net_cents"] or (gross-sf))
    seller_balance=int(seller["balance_cents"] or 0)
    seller_held=int(seller["held_cents"] or 0)
    if seller_balance < seller_net:
        c.rollback(); c.close(); raise HTTPException(409,"seller balance is insufficient for refund")
    refund_from_held=min(seller_held,seller_net)
    bl=ensure_wallet_ledger(c,o["buyer_id"],"USD",buyer["id"]); sl=ensure_wallet_ledger(c,o["seller_id"],"USD",seller["id"])
    ref=f"REF-{o['id']}"
    lines=[{"account_id":bl,"side":"credit","amount_cents":buyer_total},{"account_id":sl,"side":"debit","amount_cents":seller_net}]
    if bf+sf: lines.append({"account_id":"SYSTEM:COMMISSION_REVENUE","side":"debit","amount_cents":bf+sf})
    post_entry(c,ref,"marketplace_refund",lines,"Full marketplace refund after approved dispute",f"refund:{o['id']}")
    set_wallet_balance(c,buyer["id"],int(buyer["balance_cents"])+buyer_total,int(buyer["held_cents"] or 0))
    set_wallet_balance(c,seller["id"],seller_balance-seller_net,seller_held-refund_from_held)
    c.execute("UPDATE escrow_transactions SET status='refunded',released_at=COALESCE(released_at,?) WHERE order_id=? AND status='held'",(now(),o["id"]))
    c.execute("UPDATE orders SET status='refunded' WHERE id=? AND status='paid'",(o["id"],))
    if c.execute("SELECT changes()").fetchone()[0]!=1:
        c.rollback(); c.close(); raise HTTPException(409,"order was already refunded")
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),buyer["id"],"marketplace_refund",float(amount(buyer_total)),"USD",ref,"Full order refund","completed",now()))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),seller["id"],"marketplace_refund_debit",float(amount(seller_net)),"USD",ref,"Seller reversal for refunded order","completed",now()))
    c.execute("UPDATE disputes SET status='resolved',resolution=?,resolved_at=? WHERE id=?",
              ("Full refund approved by authorized administrator",now(),dispute_id))
    audit(c,"finance-admin","refund_approved","order",o["id"],"paid","refunded",request)
    audit(c,"finance-admin","escrow_refund","escrow",o["id"],"held" if refund_from_held else "released","refunded",request)
    c.commit(); c.close()
    return {"dispute_id":dispute_id,"order_id":o["id"],"status":"resolved","refund_reference":ref,"refunded_to_buyer":float(amount(buyer_total))}

@app.get("/api/v1/orders/{order_id}")
def get_order(order_id:str,token:str):
    c=db(); a=account_for(c,token); o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.close(); raise HTTPException(404,"order not found")
    if a["id"] not in (o["buyer_id"],o["seller_id"]): c.close(); raise HTTPException(403,"order access denied")
    data=dict(o); data["counterparty"]="BUYER-PRIVATE" if a["id"]==o["seller_id"] else "SELLER-PRIVATE"
    data.pop("buyer_id",None); data.pop("seller_id",None); c.close(); return data

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
    try: c.execute("INSERT INTO webhooks VALUES(?,?,?,?)",(str(uuid.uuid4()),eid,"processed",now())); c.commit(); first=True
    except sqlite3.IntegrityError: first=False
    c.close(); return {"accepted":True,"duplicate":not first,"mode":"sandbox"}
@app.get("/api/v1/production-readiness")
def readiness():
    return {"wallet_only":True,"real_money_enabled":REAL_MONEY_ENABLED,"requirements":["legal entity/provider approval","KYC/KYB","verified provider webhooks","persistent production database","production secrets","withdrawal approval controls"]}
