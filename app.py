import os, sqlite3, hashlib, uuid, secrets, base64
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

APP_VERSION = "6.1.0"
DB = os.getenv("DATABASE_PATH", "naqaa_market.db")
REAL_MONEY_ENABLED = os.getenv("REAL_MONEY_ENABLED", "0") == "1"
STRIPE_LIVE_ENABLED = os.getenv("STRIPE_LIVE_ENABLED", "0") == "1"
app = FastAPI(title="NAQAA Market API", version=APP_VERSION)

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, role TEXT NOT NULL, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, password_hash TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, account_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS listings(id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, listing_id TEXT NOT NULL, buyer_id TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY, offer_id TEXT NOT NULL, contract_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    ''')
    cols={r[1] for r in c.execute("PRAGMA table_info(accounts)").fetchall()}
    if "password_hash" not in cols: c.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT")
    c.commit(); c.close()
init_db()

def hash_password(password):
    salt=secrets.token_bytes(16); dk=hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000); return "pbkdf2$200000$"+base64.b64encode(salt).decode()+"$"+base64.b64encode(dk).decode()
def verify_password(password, stored):
    try:
        _, rounds, s, d=stored.split("$"); salt=base64.b64decode(s); expected=base64.b64decode(d); actual=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,int(rounds)); return secrets.compare_digest(actual,expected)
    except Exception: return False

class Register(BaseModel):
    role: str = Field(pattern="^(seller|buyer)$")
    name: str
    email: str
    password: str = Field(min_length=8)
class Login(BaseModel): email: str; password: str
class Listing(BaseModel): seller_id: str; category: str; title: str; description: str = ""; amount: float = Field(gt=0); currency: str = "USD"
class Offer(BaseModel): listing_id: str; buyer_id: str; amount: float = Field(gt=0); currency: str = "USD"
class Sign(BaseModel): signer_role: str = Field(pattern="^(buyer|seller)$")

@app.get("/")
def root(): return FileResponse("static/index.html")
@app.get("/health")
def health(): return {"status":"ok","version":APP_VERSION,"real_money":REAL_MONEY_ENABLED,"provider_mode":"LIVE" if REAL_MONEY_ENABLED else "SANDBOX","stripe_live":STRIPE_LIVE_ENABLED}
@app.get("/api/v1/status")
def status(): return {"product":"NAQAA Market","version":APP_VERSION,"money":"disabled" if not REAL_MONEY_ENABLED else "enabled","kyc_kyb_required":True,"wordpress_secrets":False}

@app.post("/api/v1/auth/register")
def register(x:Register):
    email=x.email.strip().lower(); c=db()
    if c.execute("SELECT id FROM accounts WHERE lower(email)=?",(email,)).fetchone(): c.close(); raise HTTPException(409,"email already registered")
    i=str(uuid.uuid4()); c.execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?)",(i,x.role,x.name.strip(),email,"pending",now(),hash_password(x.password))); c.commit(); c.close(); return {"id":i,"role":x.role,"status":"pending","message":"Account created. Verification/KYC-KYB may be required before marketplace actions."}

@app.post("/api/v1/auth/login")
def login(x:Login):
    email=x.email.strip().lower(); c=db(); a=c.execute("SELECT * FROM accounts WHERE lower(email)=?",(email,)).fetchone()
    if not a or not a["password_hash"] or not verify_password(x.password,a["password_hash"]): c.close(); raise HTTPException(401,"invalid email or password")
    token=secrets.token_urlsafe(32); c.execute("INSERT INTO sessions VALUES(?,?,?)",(token,a["id"],now())); c.commit(); c.close(); return {"token":token,"user":{"id":a["id"],"name":a["name"],"email":a["email"],"role":a["role"],"status":a["status"]}}

@app.get("/api/v1/auth/me")
def me(token:str):
    c=db(); r=c.execute("SELECT a.* FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token=?",(token,)).fetchone(); c.close()
    if not r: raise HTTPException(401,"invalid session")
    return {"id":r["id"],"name":r["name"],"email":r["email"],"role":r["role"],"status":r["status"]}

@app.post("/api/v1/accounts")
def account(x: Register): return register(x)
@app.post("/api/v1/listings")
def listing(x: Listing):
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
    c.execute("UPDATE offers SET status='accepted' WHERE id=?",(offer_id,)); c.commit(); c.close(); return {"id":offer_id,"status":"accepted","checkout":"sandbox_only"}
@app.post("/api/v1/contracts/{offer_id}")
def contract(offer_id:str):
    c=db(); r=c.execute("SELECT * FROM offers WHERE id=? AND status='accepted'",(offer_id,)).fetchone()
    if not r: c.close(); raise HTTPException(400,"accepted offer required")
    cid="NQ-EC-"+uuid.uuid4().hex[:10].upper(); raw=f"{cid}|{offer_id}|{r['amount']}|{r['currency']}"; h=hashlib.sha256(raw.encode()).hexdigest(); c.execute("INSERT INTO contracts VALUES(?,?,?,?,?)",(cid,offer_id,h,"awaiting_signatures",now())); c.commit(); c.close(); return {"contract_id":cid,"sha256":h,"status":"awaiting_signatures"}
@app.post("/api/v1/webhooks")
def webhook(payload:dict):
    if REAL_MONEY_ENABLED: raise HTTPException(501,"provider webhook integration must be configured and verified before live money")
    eid=payload.get("event_id")
    if not eid: raise HTTPException(400,"event_id required")
    c=db()
    try: c.execute("INSERT INTO webhooks VALUES(?,?,?,?,?)",(str(uuid.uuid4()),eid,"processed",now())); c.commit(); first=True
    except sqlite3.IntegrityError: first=False
    c.close(); return {"accepted":True,"duplicate":not first,"mode":"sandbox"}
@app.get("/api/v1/production-readiness")
def readiness(): return {"real_money_enabled":REAL_MONEY_ENABLED,"stripe_live_enabled":STRIPE_LIVE_ENABLED,"requirements":["legal entity/provider approval","KYC/KYB","provider webhook signature verification","persistent production database","secrets in hosting environment"]}
