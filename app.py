import os, sqlite3, hashlib, uuid, secrets, base64
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

APP_VERSION="6.2.0"
DB=os.getenv("DATABASE_PATH","naqaa_market.db")
REAL_MONEY_ENABLED=os.getenv("REAL_MONEY_ENABLED","0")=="1"
app=FastAPI(title="NAQAA Market API",version=APP_VERSION)

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,role TEXT NOT NULL,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,password_hash TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,account_id TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallets(id TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,currency TEXT NOT NULL DEFAULT 'USD',balance REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_transactions(id TEXT PRIMARY KEY,wallet_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,reference TEXT,description TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_requests(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,reference TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS listings(id TEXT PRIMARY KEY,seller_id TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY,listing_id TEXT NOT NULL,buyer_id TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY,offer_id TEXT NOT NULL,contract_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    """)
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
    role:str=Field(pattern="^(seller|buyer)$"); name:str; email:str; password:str=Field(min_length=8)
class Login(BaseModel): email:str; password:str
class Listing(BaseModel):
    seller_id:str; category:str; title:str; description:str=""; amount:float=Field(gt=0); currency:str="USD"
class Offer(BaseModel): listing_id:str; buyer_id:str; amount:float=Field(gt=0); currency:str="USD"
class WalletRequest(BaseModel): account_id:str; amount:float=Field(gt=0); currency:str="USD"
class WalletPay(BaseModel): from_account_id:str; to_account_id:str; amount:float=Field(gt=0); currency:str="USD"; description:str="Marketplace wallet payment"

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
def status(): return {"product":"NAQAA Market","version":APP_VERSION,"wallet_only":True,"card_payments":False,"stripe_live":False,"money":"disabled" if not REAL_MONEY_ENABLED else "enabled","kyc_kyb_required":True}

@app.post("/api/v1/auth/register")
def register(x:Register):
    email=x.email.strip().lower(); c=db()
    if c.execute("SELECT id FROM accounts WHERE lower(email)=?",(email,)).fetchone(): c.close(); raise HTTPException(409,"email already registered")
    i=str(uuid.uuid4()); c.execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?)",(i,x.role,x.name.strip(),email,"pending",now(),hp(x.password)))
    w=str(uuid.uuid4()); c.execute("INSERT INTO wallets VALUES(?,?,?,?,?)",(w,i,"USD",0,now())); c.commit(); c.close()
    return {"id":i,"role":x.role,"status":"pending","message":"Account created. Verification/KYC-KYB may be required."}

@app.post("/api/v1/auth/login")
def login(x:Login):
    c=db(); a=c.execute("SELECT * FROM accounts WHERE lower(email)=?",(x.email.strip().lower(),)).fetchone()
    if not a or not a["password_hash"] or not vp(x.password,a["password_hash"]): c.close(); raise HTTPException(401,"invalid email or password")
    t=secrets.token_urlsafe(32); c.execute("INSERT INTO sessions VALUES(?,?,?)",(t,a["id"],now())); c.commit(); c.close()
    return {"token":t,"user":{"id":a["id"],"name":a["name"],"email":a["email"],"role":a["role"],"status":a["status"]}}

@app.get("/api/v1/auth/me")
def me(token:str):
    c=db(); a=account_for(c,token); c.close()
    return {"id":a["id"],"name":a["name"],"email":a["email"],"role":a["role"],"status":a["status"]}

@app.get("/api/v1/wallet/{account_id}")
def wallet(account_id:str,token:str):
    c=db(); a=account_for(c,token)
    if a["id"]!=account_id: c.close(); raise HTTPException(403,"wallet access denied")
    w=c.execute("SELECT * FROM wallets WHERE account_id=?",(account_id,)).fetchone()
    tx=c.execute("SELECT type,amount,currency,reference,description,status,created_at FROM wallet_transactions WHERE wallet_id=? ORDER BY created_at DESC LIMIT 50",(w["id"],)).fetchall()
    c.close(); return {"wallet_id":w["id"],"account_id":account_id,"currency":w["currency"],"balance":w["balance"],"transactions":[dict(x) for x in tx],"wallet_only":True}

@app.post("/api/v1/wallet/deposit-request")
def deposit(x:WalletRequest,token:str):
    c=db(); a=account_for(c,token)
    if a["id"]!=x.account_id: c.close(); raise HTTPException(403,"wallet access denied")
    rid=str(uuid.uuid4()); ref="DEP-"+uuid.uuid4().hex[:10].upper()
    c.execute("INSERT INTO wallet_requests VALUES(?,?,?,?,?,?,?,?)",(rid,a["id"],"deposit",x.amount,x.currency,"pending",ref,now())); c.commit(); c.close()
    return {"request_id":rid,"reference":ref,"status":"pending","message":"Deposit request recorded. No external money is moved until an approved provider is integrated."}

@app.post("/api/v1/wallet/withdraw-request")
def withdraw(x:WalletRequest,token:str):
    c=db(); a=account_for(c,token)
    if a["id"]!=x.account_id: c.close(); raise HTTPException(403,"wallet access denied")
    w=c.execute("SELECT * FROM wallets WHERE account_id=?",(a["id"],)).fetchone()
    if x.currency!=w["currency"] or x.amount>w["balance"]: c.close(); raise HTTPException(400,"insufficient wallet balance")
    rid=str(uuid.uuid4()); ref="WDR-"+uuid.uuid4().hex[:10].upper()
    c.execute("INSERT INTO wallet_requests VALUES(?,?,?,?,?,?,?,?)",(rid,a["id"],"withdraw",x.amount,x.currency,"pending",ref,now())); c.commit(); c.close()
    return {"request_id":rid,"reference":ref,"status":"pending","message":"Withdrawal request recorded. It remains pending until approved provider/KYC integration is active."}

@app.post("/api/v1/wallet/pay")
def pay(x:WalletPay,token:str):
    c=db(); a=account_for(c,token)
    if a["id"]!=x.from_account_id: c.close(); raise HTTPException(403,"payment source denied")
    if x.from_account_id==x.to_account_id: c.close(); raise HTTPException(400,"source and destination must differ")
    s=c.execute("SELECT * FROM accounts WHERE id=?",(x.from_account_id,)).fetchone(); r=c.execute("SELECT * FROM accounts WHERE id=?",(x.to_account_id,)).fetchone()
    if not s or not r: c.close(); raise HTTPException(404,"account not found")
    if r["role"]!="seller": c.close(); raise HTTPException(400,"destination must be a seller wallet")
    fw=c.execute("SELECT * FROM wallets WHERE account_id=?",(x.from_account_id,)).fetchone(); tw=c.execute("SELECT * FROM wallets WHERE account_id=?",(x.to_account_id,)).fetchone()
    if fw["currency"]!=x.currency or x.amount>fw["balance"]: c.close(); raise HTTPException(400,"insufficient wallet balance")
    ref="PAY-"+uuid.uuid4().hex[:10].upper()
    c.execute("UPDATE wallets SET balance=balance-?,updated_at=? WHERE id=?",(x.amount,now(),fw["id"]))
    c.execute("UPDATE wallets SET balance=balance+?,updated_at=? WHERE id=?",(x.amount,now(),tw["id"]))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),fw["id"],"payment",-x.amount,x.currency,ref,x.description,"completed",now()))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),tw["id"],"receipt",x.amount,x.currency,ref,x.description,"completed",now()))
    c.commit(); c.close()
    return {"reference":ref,"status":"completed","amount":x.amount,"currency":x.currency,"from_account_id":x.from_account_id,"to_account_id":x.to_account_id,"wallet_only":True}

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
