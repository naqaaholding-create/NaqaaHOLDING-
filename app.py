import os, sqlite3, hashlib, uuid, secrets, base64
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from finance_ledger import ensure_schema, ensure_wallet_ledger, post_entry, set_wallet_balance, wallet_snapshot, to_cents, amount
from market_pricing import pricing_catalog, SELLER_SUBSCRIPTION_CENTS, BUYER_SUBSCRIPTION_CENTS, LISTING_FEE_CENTS, CONTACT_UNLOCK_FEE_CENTS

APP_VERSION="6.9.3"
DB=os.getenv("DATABASE_PATH","naqaa_market.db")
REAL_MONEY_ENABLED=os.getenv("REAL_MONEY_ENABLED","0")=="1"
FINANCE_PRODUCTION_APPROVED=os.getenv("FINANCE_PRODUCTION_APPROVED","0")=="1"
ADMIN_API_KEY=os.getenv("NAQAA_ADMIN_KEY","")

# Production money safety gate. Real-money settlement must never be enabled by a
# single flag: storage, compliance, provider contract and operational controls
# must all be explicitly confirmed first.
PRODUCTION_APPROVED=os.getenv("PRODUCTION_APPROVED","0")=="1"
KYC_KYB_PRODUCTION_APPROVED=os.getenv("KYC_KYB_PRODUCTION_APPROVED","0")=="1"
DATABASE_PERSISTENT=os.getenv("DATABASE_PERSISTENT","0")=="1"

def production_money_blockers():
    blockers=[]
    if not PRODUCTION_APPROVED: blockers.append("PRODUCTION_APPROVED")
    if not FINANCE_PRODUCTION_APPROVED: blockers.append("FINANCE_PRODUCTION_APPROVED")
    if not KYC_KYB_PRODUCTION_APPROVED: blockers.append("KYC_KYB_PRODUCTION_APPROVED")
    if not DATABASE_PERSISTENT: blockers.append("DATABASE_PERSISTENT")
    if not os.getenv("DATABASE_PATH"): blockers.append("DATABASE_PATH")
    if not os.getenv("NAQAA_ADMIN_KEY"): blockers.append("NAQAA_ADMIN_KEY")
    return blockers

def require_live_finance(provider="internal"):
    """Hard runtime gate for live financial operations; no third-party wallet provider is required."""
    if not REAL_MONEY_ENABLED:
        raise HTTPException(503, "real-money settlement is disabled")
    blockers=production_money_blockers()
    if blockers:
        raise HTTPException(503, "live financial operation blocked by production safety gate")
    return True

if REAL_MONEY_ENABLED:
    _blockers=production_money_blockers()
    if _blockers:
        raise RuntimeError("REAL_MONEY_ENABLED=1 blocked by production safety gate: "+", ".join(_blockers))
app=FastAPI(title="NAQAA Market API",version=APP_VERSION)
CORS_ORIGINS=[x.strip() for x in os.getenv("CORS_ORIGINS","https://naqaaholding.wordpress.com").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["GET","POST","OPTIONS"], allow_headers=["Content-Type","Authorization","Idempotency-Key"])

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
    CREATE TABLE IF NOT EXISTS company_profile(id TEXT PRIMARY KEY,legal_name TEXT NOT NULL,display_name TEXT NOT NULL,registration_number TEXT,country TEXT,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS departments(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,code TEXT NOT NULL UNIQUE,description TEXT DEFAULT '',manager_account_id TEXT,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS employees(id TEXT PRIMARY KEY,account_id TEXT NOT NULL UNIQUE,employee_number TEXT NOT NULL UNIQUE,department_id TEXT NOT NULL,job_title TEXT NOT NULL,employment_status TEXT NOT NULL DEFAULT 'active',joined_at TEXT NOT NULL,manager_employee_id TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallets(id TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,currency TEXT NOT NULL DEFAULT 'USD',balance REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_transactions(id TEXT PRIMARY KEY,wallet_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,reference TEXT,description TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_requests(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,reference TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS listings(id TEXT PRIMARY KEY,seller_id TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,image_data TEXT);
    CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY,listing_id TEXT NOT NULL,buyer_id TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY,offer_id TEXT NOT NULL,contract_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pricing_settings(code TEXT PRIMARY KEY,amount_cents INTEGER NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pricing_audit(id TEXT PRIMARY KEY,code TEXT NOT NULL,old_amount_cents INTEGER NOT NULL,new_amount_cents INTEGER NOT NULL,changed_at TEXT NOT NULL,changed_by TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS service_payments(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,code TEXT NOT NULL,amount_cents INTEGER NOT NULL,currency TEXT NOT NULL,reference TEXT UNIQUE NOT NULL,status TEXT NOT NULL,description TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,plan_code TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',started_at TEXT NOT NULL,active_until TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(account_id,plan_code));
    CREATE INDEX IF NOT EXISTS idx_subscriptions_account ON subscriptions(account_id,status,active_until);
    CREATE TABLE IF NOT EXISTS referral_codes(id TEXT PRIMARY KEY,employee_id TEXT NOT NULL UNIQUE,code TEXT NOT NULL UNIQUE,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS referrals(id TEXT PRIMARY KEY,code_id TEXT NOT NULL,employee_id TEXT NOT NULL,account_id TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active');
    CREATE TABLE IF NOT EXISTS employee_incentives(id TEXT PRIMARY KEY,referral_id TEXT,employee_id TEXT NOT NULL,order_id TEXT,kind TEXT NOT NULL,revenue_cents INTEGER NOT NULL DEFAULT 0,rate_bps INTEGER NOT NULL DEFAULT 0,amount_cents INTEGER NOT NULL DEFAULT 0,currency TEXT NOT NULL DEFAULT 'USD',status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,paid_at TEXT);
    CREATE TABLE IF NOT EXISTS employee_incentive_settings(id TEXT PRIMARY KEY,kind TEXT NOT NULL UNIQUE,rate_bps INTEGER NOT NULL DEFAULT 0,fixed_cents INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL,updated_by TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS employee_invitations(id TEXT PRIMARY KEY,token TEXT NOT NULL UNIQUE,email TEXT NOT NULL,name TEXT NOT NULL,department_id TEXT NOT NULL,job_title TEXT NOT NULL,invited_by TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,used_at TEXT);
    CREATE TABLE IF NOT EXISTS job_openings(id TEXT PRIMARY KEY,title TEXT NOT NULL,department_id TEXT,description TEXT NOT NULL,location TEXT DEFAULT 'Remote / Global',employment_type TEXT DEFAULT 'Flexible',status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS job_applications(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,name TEXT NOT NULL,email TEXT NOT NULL,country TEXT DEFAULT '',experience TEXT DEFAULT '',qualifications TEXT DEFAULT '',cv_url TEXT DEFAULT '',message TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'submitted',review_notes TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewed_by TEXT);
    CREATE INDEX IF NOT EXISTS idx_job_applications_status ON job_applications(status);
    CREATE TABLE IF NOT EXISTS wallet_assets(code TEXT PRIMARY KEY,symbol TEXT NOT NULL,name TEXT NOT NULL,network TEXT NOT NULL,contract_address TEXT,decimals INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
    """)
    for col,typ in [("account_type","TEXT"),("dob","TEXT"),("nationality","TEXT"),("phone","TEXT"),("identity_type","TEXT"),("identity_last4","TEXT"),("identity_country","TEXT"),("company_name","TEXT"),("company_registration","TEXT"),("email_verified_at","TEXT")]:
        try: c.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE listings ADD COLUMN image_data TEXT")
    except sqlite3.OperationalError: pass
    # Initialize finance/marketplace and crypto tables before altering their columns so
    # every fresh or reset database has the complete application schema.
    # Defensive DDL keeps startup safe even if an older database migration is partial.
    c.execute("""CREATE TABLE IF NOT EXISTS ledger_accounts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT, currency TEXT NOT NULL, created_at TEXT NOT NULL)""")
    ensure_schema(c)
    from crypto_wallet import ensure_crypto_schema
    ensure_crypto_schema(c)
    c.execute("""CREATE TABLE IF NOT EXISTS ledger_accounts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT, currency TEXT NOT NULL, created_at TEXT NOT NULL)""")
    c.commit()
    for code, cents in [("seller_monthly",SELLER_SUBSCRIPTION_CENTS),("buyer_monthly",BUYER_SUBSCRIPTION_CENTS),("listing",LISTING_FEE_CENTS),("contact_unlock",CONTACT_UNLOCK_FEE_CENTS)]:
        c.execute("INSERT OR IGNORE INTO pricing_settings(code,amount_cents,updated_at,updated_by) VALUES(?,?,?,?)",(code,cents,now(),"system"))
    for code,symbol,name,network,contract,decimals in [
        ("USDT","USDT","Tether USD","BSC","0x55d398326f99059fF775485246999027B3197955",18),
        ("USDC","USDC","USD Coin","BSC","0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",18),
        ("BNB","BNB","BNB","BSC",None,18)
    ]:
        c.execute("INSERT OR IGNORE INTO wallet_assets(code,symbol,name,network,contract_address,decimals,active,updated_at) VALUES(?,?,?,?,?,?,1,?)",(code,symbol,name,network,contract,decimals,now()))
    for kind,bps in [("referral_subscription",1000),("referral_listing",1000),("referral_sale",2000)]:
        c.execute("INSERT OR IGNORE INTO employee_incentive_settings(id,kind,rate_bps,fixed_cents,active,updated_at,updated_by) VALUES(?,?,?,?,1,?,?)",(str(uuid.uuid4()),kind,bps,0,now(),"system"))
        c.execute("INSERT OR IGNORE INTO pricing_settings(code,amount_cents,updated_at,updated_by) VALUES(?,?,?,?)",(code,cents,now(),"system"))
    c.commit()
    c.execute("INSERT OR IGNORE INTO ledger_accounts(id,kind,owner_id,currency,created_at) VALUES(?,?,?,?,?)",("SYSTEM:COMPANY_WALLET","system","COMPANY","USD",now()))
    for col,typ in [("offer_id","TEXT"),("settlement_mode","TEXT NOT NULL DEFAULT 'off_platform'"),("seller_choice","TEXT"),("buyer_choice","TEXT"),("buyer_confirmed_at","TEXT"),("protected_at","TEXT"),("buyer_fee_cents","INTEGER NOT NULL DEFAULT 0"),("seller_fee_cents","INTEGER NOT NULL DEFAULT 0"),("buyer_total_cents","INTEGER NOT NULL DEFAULT 0"),("seller_net_cents","INTEGER NOT NULL DEFAULT 0"),("payment_reference","TEXT"),("paid_at","TEXT"),("commission_rule_id","TEXT"),("buyer_rate_bps","INTEGER NOT NULL DEFAULT 0"),("seller_rate_bps","INTEGER NOT NULL DEFAULT 0"),("buyer_fixed_cents","INTEGER NOT NULL DEFAULT 0"),("seller_fixed_cents","INTEGER NOT NULL DEFAULT 0"),("minimum_fee_cents","INTEGER NOT NULL DEFAULT 0"),("maximum_fee_cents","INTEGER"),("external_tx_hash","TEXT"),("external_paid_at","TEXT")]:
        try: c.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    c.commit(); c.close()
init_db()
def seed_company_structure():
    c=db()
    t=now()
    if not c.execute("SELECT id FROM company_profile LIMIT 1").fetchone():
        c.execute("INSERT INTO company_profile VALUES(?,?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()),"NAQAA Holding","NAQAA MARKET","","","active",t,t))

    # الشركة تعمل بأربعة أقسام رئيسية فقط:
    # 1) الإدارة العامة  2) المالية  3) السوق والمبيعات  4) التشغيل والدعم
    target=[
        ("الإدارة العامة","EXEC","الإدارة العامة والحوكمة"),
        ("المالية","FIN","المحاسبة والخزينة والتسويات المالية"),
        ("السوق والمبيعات","MARKET","المبيعات والتسويق والإعلانات وإدارة السوق"),
        ("التشغيل والدعم","OPS","الموارد البشرية والامتثال وخدمة العملاء والتقنية"),
    ]
    for name,code,desc in target:
        row=c.execute("SELECT id FROM departments WHERE code=?",(code,)).fetchone()
        if row:
            c.execute("UPDATE departments SET name=?,description=?,status='active' WHERE id=?",(name,desc,row["id"]))
        else:
            c.execute("INSERT INTO departments(id,name,code,description,created_at) VALUES(?,?,?,?,?)",
                      (str(uuid.uuid4()),name,code,desc,t))

    # ترحيل الأقسام القديمة إلى الأقسام الأربعة الجديدة، مع الحفاظ على الموظفين.
    legacy_map={
        "الإدارة العامة":"EXEC",
        "المالية":"FIN",
        "المبيعات والتسويق":"MARKET",
        "الإعلانات والسوق":"MARKET",
        "الموارد البشرية":"OPS",
        "الامتثال":"OPS",
        "خدمة العملاء":"OPS",
        "التقنية":"OPS",
    }
    for legacy_name,target_code in legacy_map.items():
        legacy=c.execute("SELECT id FROM departments WHERE name=? AND code!=?",(legacy_name,target_code)).fetchone()
        target=c.execute("SELECT id FROM departments WHERE code=?",(target_code,)).fetchone()
        if legacy and target:
            c.execute("UPDATE employees SET department_id=? WHERE department_id=?",(target["id"],legacy["id"]))
            c.execute("DELETE FROM departments WHERE id=?",(legacy["id"],))

    # Seed the initial public hiring opportunities without creating duplicates.
    seed_jobs=[
        ("مدير السوق والمبيعات","MARKET","قيادة المبيعات وتطوير الأعمال والشراكات وإدارة أداء السوق.","Global / Remote","Full-time"),
        ("مدير حسابات مالية","FIN","إدارة الحسابات والتسويات والتقارير المالية وفق صلاحيات الشركة.","Global / Remote","Full-time"),
        ("موظف مبيعات وتطوير أعمال","MARKET","التواصل مع المشترين والبائعين وتطوير العملاء ومتابعة الفرص.","Global / Remote","Flexible"),
        ("مسؤول خدمة العملاء","OPS","متابعة استفسارات العملاء وتصنيفها ورفع الحالات التي تحتاج قرارًا إداريًا.","Global / Remote","Flexible"),
    ]
    for title,code,description,location,employment_type in seed_jobs:
        dep=c.execute("SELECT id FROM departments WHERE code=?",(code,)).fetchone()
        if dep and not c.execute("SELECT id FROM job_openings WHERE title=? AND status='open'",(title,)).fetchone():
            c.execute("INSERT INTO job_openings(id,title,department_id,description,location,employment_type,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()),title,dep["id"],description,location,employment_type,"open",t))
    c.commit()
    c.close()
seed_company_structure()

from crypto_wallet import ensure_crypto_schema
from crypto_routes import router as crypto_router
_crypto_db = db(); ensure_crypto_schema(_crypto_db); _crypto_db.commit(); _crypto_db.close()

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
class SettlementChoice(BaseModel): settlement_mode:str=Field(default="off_platform",pattern="^(off_platform|naqa_protected)$")
class WalletRequest(BaseModel): account_id:str; amount:float=Field(gt=0); currency:str="USD"
class WalletPay(BaseModel): from_account_id:str; to_account_id:str; amount:float=Field(gt=0); currency:str="USD"; description:str="Marketplace wallet payment"
class ReferralApply(BaseModel): code:str=Field(min_length=4,max_length=40)
class IncentiveSettingsUpdate(BaseModel):
    kind:str=Field(pattern="^(referral_subscription|referral_listing|referral_sale)$")
    rate_bps:int=Field(ge=0,le=10000)
    fixed_cents:int=Field(ge=0,le=100000000)
    active:bool=True

ROLE_PERMISSIONS={
"company_director":{"company_admin":True,"manage_employees":True,"manage_departments":True,"manage_listings":True,"publish_listings":True,"financial_admin":True,"finance_view":True,"compliance_admin":True,"hr_admin":True,"manage_referrals":True,"manage_incentives":True,"manage_wallet_assets":True},
"hr_manager":{"company_admin":False,"manage_employees":True,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":True,"manage_referrals":False,"manage_incentives":False,"manage_wallet_assets":False},
"finance_manager":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":True,"finance_view":True,"compliance_admin":False,"hr_admin":False,"manage_referrals":False,"manage_incentives":False,"manage_wallet_assets":False},
"sales_manager":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":True,"publish_listings":True,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False},
"listing_manager":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":True,"publish_listings":True,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False},
"compliance_manager":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":False,"finance_view":True,"compliance_admin":True,"hr_admin":False},
"customer_service":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False},
"employee":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False},
"manager":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":True,"publish_listings":True,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False},
"buyer":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False},
"seller":{"company_admin":False,"manage_employees":False,"manage_departments":False,"manage_listings":False,"publish_listings":False,"financial_admin":False,"finance_view":False,"compliance_admin":False,"hr_admin":False}
}
def role_permissions(role): return dict(ROLE_PERMISSIONS.get(role,ROLE_PERMISSIONS["employee"]))
def account_public(a):
    role=a["role"]; p=role_permissions(role)
    return {
        "id":a["id"],"name":a["name"],"email":a["email"],"role":role,"status":a["status"],
        "account_type":a["account_type"],"dob":a["dob"],"nationality":a["nationality"],"phone":a["phone"],
        "identity_type":a["identity_type"],"identity_last4":a["identity_last4"],"identity_country":a["identity_country"],
        "company_name":a["company_name"],"company_registration":a["company_registration"],
        "permissions":p
    }

# Email verification is part of the production API entrypoint, not a separate process.
# This guarantees seller/buyer registration always has the verification routes when uvicorn runs app:app.
import email_verification
email_verification.install(app, __import__("app"))

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

@app.get("/api/v1/config/external-wallet")
def external_wallet_config():
    # Reown project IDs are public client configuration, not wallet credentials.
    # Never expose or accept seed phrases/private keys here.
    return {
        "provider": "Reown",
        # The existing NAQAA Reown project is the default client configuration.
        # REOWN_PROJECT_ID can still override it per deployment.
        "project_id": os.getenv("REOWN_PROJECT_ID", "f16487b81fa56f898df3053b3b00b566"),
        "app_url": os.getenv("REOWN_APP_URL", ""),
        "network": "BSC",
        "chain_id": 56,
        "asset": "USDT",
        "transfers_enabled": False,
    }
class ServicePayment(BaseModel):
    code:str=Field(pattern="^(seller_monthly|buyer_monthly|listing|contact_unlock)$")
    description:str=""

@app.post("/api/v1/services/pay")
def pay_service(x:ServicePayment,token:str,request:Request):
    """Charge an editable marketplace service price from the user's internal wallet.
    External real-money collection remains behind the production gate/provider layer.
    """
    key=idem(request)
    c=db(); actor=account_for(c,token); c.execute("BEGIN IMMEDIATE")
    row=c.execute("SELECT amount_cents FROM pricing_settings WHERE code=?",(x.code,)).fetchone()
    if not row: c.rollback(); c.close(); raise HTTPException(404,"service price not found")
    cents=int(row["amount_cents"])
    if cents<0: c.rollback(); c.close(); raise HTTPException(409,"invalid service price")
    role_rules={
        "seller_monthly": "seller",
        "buyer_monthly": "buyer",
        "listing": "seller",
        "contact_unlock": "buyer",
    }
    required_role=role_rules.get(x.code)
    if required_role and actor["role"]!=required_role:
        c.rollback(); c.close(); raise HTTPException(403,"service is not available for this account role")
    existing=c.execute("SELECT * FROM journal_entries WHERE idempotency_key=?",(key+"::service",)).fetchone()
    if existing:
        paid=c.execute("SELECT * FROM service_payments WHERE reference=?",(existing["reference"],)).fetchone()
        c.commit(); c.close()
        if paid:
            return {"payment_id":paid["id"],"reference":paid["reference"],"status":paid["status"],"duplicate":True}
        raise HTTPException(409,"service payment idempotency record is inconsistent")
    w=c.execute("SELECT * FROM wallets WHERE account_id=?",(actor["id"],)).fetchone()
    if not w: c.rollback(); c.close(); raise HTTPException(404,"wallet not found")
    available=int(w["balance_cents"] or 0)-int(w["held_cents"] or 0)
    if cents>available: c.rollback(); c.close(); raise HTTPException(400,"insufficient available balance for service")
    wallet_ledger=ensure_wallet_ledger(c,actor["id"],"USD",w["id"])
    ref="SVC-"+uuid.uuid4().hex[:10].upper()
    idem_desc="[idem:"+key+"] "+(x.description or x.code)
    lines=[{"account_id":wallet_ledger,"side":"debit","amount_cents":cents},{"account_id":"SYSTEM:COMPANY_WALLET","side":"credit","amount_cents":cents}]
    post_entry(c,ref,"service_payment",lines,idem_desc,key+"::service")
    set_wallet_balance(c,w["id"],int(w["balance_cents"])-cents,int(w["held_cents"] or 0))
    pid=str(uuid.uuid4())
    c.execute("INSERT INTO service_payments(id,account_id,code,amount_cents,currency,reference,status,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (pid,actor["id"],x.code,cents,"USD",ref,"completed",idem_desc,now()))
    if x.code in ("seller_monthly","buyer_monthly"):
        t=datetime.now(timezone.utc)
        current=c.execute("SELECT active_until FROM subscriptions WHERE account_id=? AND plan_code=? AND status='active' ORDER BY active_until DESC LIMIT 1",
                          (actor["id"],x.code)).fetchone()
        if current:
            try: current_until=datetime.fromisoformat(current["active_until"])
            except ValueError: current_until=t
            start=max(t,current_until)
        else: start=t
        active_until=(start+__import__("datetime").timedelta(days=30)).isoformat()
        c.execute("""INSERT INTO subscriptions(id,account_id,plan_code,status,started_at,active_until,updated_at)
                     VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(account_id,plan_code) DO UPDATE SET
                       status='active',active_until=excluded.active_until,updated_at=excluded.updated_at""",
                  (str(uuid.uuid4()),actor["id"],x.code,"active",start.isoformat(),active_until,t.isoformat()))
    c.execute("INSERT INTO wallet_transactions VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),w["id"],"service_payment",float(amount(cents)),"USD",ref,x.description or x.code,"completed",now()))
    award_kind={"seller_monthly":"referral_subscription","buyer_monthly":"referral_subscription","listing":"referral_listing","contact_unlock":"referral_listing"}.get(x.code)
    if award_kind: award_referral_incentive(c,actor["id"],award_kind,cents)
    c.commit(); c.close()
    return {"payment_id":pid,"reference":ref,"status":"completed","code":x.code,"amount":float(amount(cents)),"currency":"USD","wallet_only":True}

@app.get("/api/v1/services/payments")
def service_payments(token:str):
    c=db(); actor=account_for(c,token)
    rows=c.execute("SELECT id,code,amount_cents,currency,reference,status,description,created_at FROM service_payments WHERE account_id=? ORDER BY created_at DESC LIMIT 100",(actor["id"],)).fetchall()
    c.close()
    return [{**dict(r),"amount":float(amount(int(r["amount_cents"])))} for r in rows]

@app.get("/api/v1/finance/pricing")
def finance_pricing():
    """Public commercial-plan catalog; prices are editable by authorized finance/admin users."""
    c=db()
    rows=c.execute("SELECT code,amount_cents FROM pricing_settings").fetchall()
    c.close()
    return pricing_catalog({r["code"]:r["amount_cents"] for r in rows})

class PricingUpdate(BaseModel):
    code:str=Field(pattern="^(seller_monthly|buyer_monthly|listing|contact_unlock)$")
    amount_cents:int=Field(ge=0,le=100000000)

@app.post("/api/v1/finance/pricing")
def update_finance_pricing(x:PricingUpdate, token:str):
    c=db()
    actor=account_for(c,token)
    if actor["role"]!="company_director":
        c.close(); raise HTTPException(403,"only the owner/general manager may change prices")
    perms=role_permissions(actor["role"])
    if not (perms.get("financial_admin") or perms.get("company_admin")):
        c.close()
        raise HTTPException(403,"financial admin permission required")
    row=c.execute("SELECT amount_cents FROM pricing_settings WHERE code=?",(x.code,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404,"pricing item not found")
    t=now()
    c.execute("UPDATE pricing_settings SET amount_cents=?,updated_at=?,updated_by=? WHERE code=?",(x.amount_cents,t,actor["id"],x.code))
    c.execute("INSERT INTO pricing_audit(id,code,old_amount_cents,new_amount_cents,changed_at,changed_by) VALUES(?,?,?,?,?,?)",(str(uuid.uuid4()),x.code,row["amount_cents"],x.amount_cents,t,actor["id"]))
    c.commit()
    rows=c.execute("SELECT code,amount_cents FROM pricing_settings").fetchall()
    c.close()
    return {"ok":True,"code":x.code,"amount_cents":x.amount_cents,"pricing":pricing_catalog({r["code"]:r["amount_cents"] for r in rows})}

@app.get("/api/v1/status")
def status(): return {"product":"NAQAA Market","version":APP_VERSION,"wallet_only":True,"card_payments":False,"stripe_live":False,"money":"disabled" if not REAL_MONEY_ENABLED else "enabled","kyc_kyb_required":True,"ledger":"double_entry","precision":"integer_cents","idempotency":True,"reconciliation":"/api/v1/finance/reconciliation"}

@app.get("/api/v1/production/preflight")
def production_preflight():
    checks = {
        "real_money_flag": REAL_MONEY_ENABLED,
        "finance_production_approved": FINANCE_PRODUCTION_APPROVED,
        "cwallet_live_contract_verified": CWALLET_LIVE_CONTRACT_VERIFIED,
        "cwallet_enabled": os.getenv("CWALLET_ENABLED","0")=="1",
        "cwallet_production_env": os.getenv("CWALLET_ENV","sandbox").lower()=="production",
        "cwallet_api_base_configured": bool(os.getenv("CWALLET_API_BASE_URL")),
        "cwallet_api_key_configured": bool(os.getenv("CWALLET_API_KEY")),
        "cwallet_api_secret_configured": bool(os.getenv("CWALLET_API_SECRET")),
        "cwallet_webhook_secret_configured": bool(os.getenv("CWALLET_WEBHOOK_SECRET")),
        "cwallet_payment_path_configured": bool(os.getenv("CWALLET_PAYMENT_PATH")),
        "cwallet_payout_path_configured": bool(os.getenv("CWALLET_PAYOUT_PATH")),
        "admin_key_configured": bool(os.getenv("NAQAA_ADMIN_KEY") or ADMIN_API_KEY),
        "database_path_configured": bool(os.getenv("DATABASE_PATH")),
    }
    blockers=[]
    if not checks["finance_production_approved"]: blockers.append("FINANCE_PRODUCTION_APPROVED is not enabled")
    if not checks["cwallet_live_contract_verified"]: blockers.append("Cwallet live API/webhook contract has not been verified")
    if not checks["cwallet_enabled"]: blockers.append("CWALLET_ENABLED is not enabled")
    if not checks["cwallet_production_env"]: blockers.append("CWALLET_ENV is not set to production")
    for key, label in [
        ("cwallet_api_base_configured", "CWALLET_API_BASE_URL is not configured"),
        ("cwallet_api_key_configured", "CWALLET_API_KEY is not configured"),
        ("cwallet_api_secret_configured", "CWALLET_API_SECRET is not configured"),
        ("cwallet_webhook_secret_configured", "CWALLET_WEBHOOK_SECRET is not configured"),
        ("cwallet_payment_path_configured", "CWALLET_PAYMENT_PATH is not configured"),
        ("cwallet_payout_path_configured", "CWALLET_PAYOUT_PATH is not configured"),
    ]:
        if not checks[key]: blockers.append(label)
    if not checks["admin_key_configured"]: blockers.append("NAQAA_ADMIN_KEY is not configured")
    if not checks["database_path_configured"]: blockers.append("DATABASE_PATH is not explicitly configured for production storage")
    if REAL_MONEY_ENABLED and blockers:
        return {"ready":False,"money_mode":"blocked","checks":checks,"blockers":blockers}
    return {"ready":not blockers,"money_mode":"live" if REAL_MONEY_ENABLED else "sandbox","checks":checks,"blockers":blockers}

@app.get("/api/v1/production/readiness")
def production_readiness():
    blockers=production_money_blockers()
    return {"ready": len(blockers)==0, "real_money_enabled": REAL_MONEY_ENABLED, "blockers": blockers, "note":"Readiness only. It does not activate funds."}

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

class RoleUpdate(BaseModel):
    role:str=Field(pattern="^(buyer|seller|manager|company_director|hr_manager|finance_manager|sales_manager|listing_manager|compliance_manager|customer_service|employee)$")

class DepartmentCreate(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    code:str=Field(min_length=2,max_length=30)
    description:str=""
class EmployeeCreate(BaseModel):
    account_id:str=""
    account_email:str=""
    department_id:str
    job_title:str=Field(min_length=2,max_length=120)
    employee_number:str=""

class JobApplicationCreate(BaseModel):
    job_id:str
    name:str=Field(min_length=2,max_length=120)
    email:str
    country:str=""
    experience:str=""
    qualifications:str=""
    cv_url:str=""
    message:str=""

class JobApplicationStatus(BaseModel):
    status:str=Field(pattern="^(submitted|reviewing|interview|accepted|rejected|withdrawn)$")
    review_notes:str=""

class EmployeeInviteCreate(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    email:str
    department_id:str
    job_title:str=Field(min_length=2,max_length=120)
class EmployeeRoleUpdate(BaseModel):
    role:str=Field(pattern="^(company_director|hr_manager|finance_manager|sales_manager|listing_manager|compliance_manager|customer_service|employee|manager)$")
class CompanyProfileUpdate(BaseModel):
    legal_name:str=Field(min_length=2,max_length=180)
    display_name:str=Field(min_length=2,max_length=120)
    registration_number:str=""
    country:str=""


@app.get("/api/v1/careers/jobs")
def careers_jobs():
    c=db()
    rows=[dict(r) for r in c.execute("""SELECT j.id,j.title,j.description,j.location,j.employment_type,j.status,j.created_at,
        COALESCE(d.name,'') department_name FROM job_openings j LEFT JOIN departments d ON d.id=j.department_id
        WHERE j.status='open' ORDER BY j.created_at DESC""").fetchall()]
    c.close()
    return {"jobs":rows}

@app.post("/api/v1/careers/applications")
def submit_career_application(x:JobApplicationCreate,request:Request):
    c=db()
    job=c.execute("SELECT id,title FROM job_openings WHERE id=? AND status='open'",(x.job_id,)).fetchone()
    if not job:
        c.close(); raise HTTPException(404,"job opening not found or closed")
    email=x.email.strip().lower()
    duplicate=c.execute("SELECT id FROM job_applications WHERE job_id=? AND lower(email)=? AND status NOT IN ('rejected','withdrawn')",(x.job_id,email)).fetchone()
    if duplicate:
        c.close(); raise HTTPException(409,"يوجد طلب تقديم قائم لهذا البريد على هذه الوظيفة")
    aid=str(uuid.uuid4()); ts=now()
    c.execute("""INSERT INTO job_applications(id,job_id,name,email,country,experience,qualifications,cv_url,message,status,review_notes,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,'submitted','',?,?)""",
              (aid,job["id"],x.name.strip(),email,x.country.strip(),x.experience.strip(),x.qualifications.strip(),x.cv_url.strip(),x.message.strip(),ts,ts))
    c.commit(); c.close()
    return {"id":aid,"status":"submitted","message":"تم استلام طلبك وسيتم مراجعته من إدارة الموارد البشرية."}

@app.get("/api/v1/company/careers/applications")
def careers_applications(token:str,status:str=""):
    c=db(); company_actor(c,token,"manage_employees")
    q="""SELECT a.*,j.title job_title,COALESCE(d.name,'') department_name
         FROM job_applications a JOIN job_openings j ON j.id=a.job_id
         LEFT JOIN departments d ON d.id=j.department_id"""
    params=[]
    if status:
        q+=" WHERE a.status=?"; params.append(status)
    q+=" ORDER BY a.created_at DESC"
    rows=[dict(r) for r in c.execute(q,params).fetchall()]
    c.close(); return {"applications":rows}

@app.post("/api/v1/company/careers/applications/{application_id}/status")
def update_career_application(application_id:str,x:JobApplicationStatus,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_employees")
    a=c.execute("""SELECT a.*,j.title job_title,j.department_id FROM job_applications a
                   JOIN job_openings j ON j.id=a.job_id WHERE a.id=?""",(application_id,)).fetchone()
    if not a: c.close(); raise HTTPException(404,"application not found")
    ts=now(); invite=None
    if x.status=="accepted":
        existing=c.execute("SELECT id FROM accounts WHERE lower(email)=?",(a["email"].lower(),)).fetchone()
        if existing:
            c.close(); raise HTTPException(409,"المتقدم لديه حساب بالفعل؛ استخدم دعوة الموظف للحساب الموجود")
        c.execute("UPDATE employee_invitations SET status='cancelled' WHERE lower(email)=? AND status='pending'",(a["email"].lower(),))
        invite_token=secrets.token_urlsafe(32); expires=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(); invite_id=str(uuid.uuid4())
        c.execute("""INSERT INTO employee_invitations(id,token,email,name,department_id,job_title,invited_by,expires_at,status,created_at)
                     VALUES(?,?,?,?,?,?,?,?,'pending',?)""",
                  (invite_id,invite_token,a["email"],a["name"],a["department_id"],a["job_title"],actor["id"],expires,ts))
        invite={"activation_path":"employee-activate.html?invite="+invite_token,"expires_at":expires}
    c.execute("UPDATE job_applications SET status=?,review_notes=?,updated_at=?,reviewed_by=? WHERE id=?",
              (x.status,x.review_notes,ts,actor["id"],application_id))
    c.commit(); c.close()
    return {"id":application_id,"status":x.status,"invitation":invite}

def company_actor(c,token,permission="company_admin"):
    actor=account_for(c,token)
    if not role_permissions(actor["role"]).get(permission,False):
        raise HTTPException(403,"company permission required: "+permission)
    return actor

def award_referral_incentive(c,account_id,kind,revenue_cents,order_id=None):
    """Record an auditable employee incentive from revenue actually collected by NAQAA."""
    if revenue_cents <= 0: return None
    ref=c.execute("SELECT employee_id,id FROM referrals WHERE account_id=? AND status='active'",(account_id,)).fetchone()
    if not ref: return None
    setting=c.execute("SELECT * FROM employee_incentive_settings WHERE kind=? AND active=1",(kind,)).fetchone()
    if not setting: return None
    amount_cents=(int(revenue_cents)*int(setting["rate_bps"])//10000)+int(setting["fixed_cents"])
    if amount_cents <= 0: return None
    if order_id and c.execute("SELECT id FROM employee_incentives WHERE order_id=? AND employee_id=? AND kind=?",(order_id,ref["employee_id"],kind)).fetchone():
        return None
    iid=str(uuid.uuid4())
    c.execute("INSERT INTO employee_incentives(id,referral_id,employee_id,order_id,kind,revenue_cents,rate_bps,amount_cents,currency,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?)",
              (iid,ref["id"],ref["employee_id"],order_id,kind,int(revenue_cents),int(setting["rate_bps"]),int(amount_cents),"USD",now()))
    return iid

@app.get("/api/v1/wallet/assets")
def wallet_assets():
    c=db()
    rows=[dict(r) for r in c.execute("SELECT code,symbol,name,network,contract_address,decimals,active FROM wallet_assets WHERE active=1 ORDER BY symbol")]
    c.close()
    return {"scalable":True,"assets":rows,"note":"الأصول قابلة للزيادة؛ لا يتم تخزين Seed Phrase أو Private Key."}

@app.get("/api/v1/referrals/me")
def my_referrals(token:str):
    c=db(); actor=account_for(c,token)
    emp=c.execute("SELECT * FROM employees WHERE account_id=? AND employment_status='active'",(actor["id"],)).fetchone()
    if not emp:
        c.close(); raise HTTPException(403,"employee account required")
    code=c.execute("SELECT code,active FROM referral_codes WHERE employee_id=?",(emp["id"],)).fetchone()
    stats=c.execute("SELECT COUNT(*) n FROM referrals WHERE employee_id=?",(emp["id"],)).fetchone()
    incentives=c.execute("SELECT COALESCE(SUM(amount_cents),0) total,COUNT(*) n FROM employee_incentives WHERE employee_id=? AND status IN ('pending','approved','paid')",(emp["id"],)).fetchone()
    c.close()
    return {"employee_id":emp["id"],"code":dict(code) if code else None,"referrals":int(stats["n"]),"incentive_cents":int(incentives["total"]),"incentive_count":int(incentives["n"])}

@app.post("/api/v1/referrals/apply")
def apply_referral(x:ReferralApply,token:str):
    c=db(); actor=account_for(c,token)
    code=c.execute("SELECT * FROM referral_codes WHERE code=? AND active=1",(x.code.strip().upper(),)).fetchone()
    if not code: c.close(); raise HTTPException(404,"referral code not found")
    if c.execute("SELECT id FROM referrals WHERE account_id=?",(actor["id"],)).fetchone():
        c.close(); raise HTTPException(409,"referral already assigned")
    rid=str(uuid.uuid4())
    c.execute("INSERT INTO referrals(id,code_id,employee_id,account_id,created_at,status) VALUES(?,?,?,?,?,'active')",(rid,code["id"],code["employee_id"],actor["id"],now()))
    c.commit(); c.close()
    return {"status":"linked","referral_id":rid,"employee_id":code["employee_id"]}

@app.post("/api/v1/company/employees/{employee_id}/referral-code")
def create_employee_referral_code(employee_id:str,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_employees")
    e=c.execute("SELECT id FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not e: c.close(); raise HTTPException(404,"employee not found")
    existing=c.execute("SELECT code,active FROM referral_codes WHERE employee_id=?",(employee_id,)).fetchone()
    if existing:
        c.close(); return dict(existing)
    code="NAQ-"+secrets.token_hex(4).upper()
    rid=str(uuid.uuid4())
    c.execute("INSERT INTO referral_codes(id,employee_id,code,active,created_at) VALUES(?,?,?,?,?)",(rid,employee_id,code,1,now()))
    audit(c,actor["id"],"employee_referral_code_created","employee",employee_id,None,code,request)
    c.commit(); c.close()
    return {"employee_id":employee_id,"code":code,"active":True}

@app.get("/api/v1/company/incentives")
def company_incentives(token:str):
    c=db(); company_actor(c,token,"manage_incentives")
    rows=[dict(r) for r in c.execute("SELECT kind,rate_bps,fixed_cents,active,updated_at FROM employee_incentive_settings ORDER BY kind")]
    c.close(); return {"settings":rows}

@app.post("/api/v1/company/incentives")
def update_company_incentive(x:IncentiveSettingsUpdate,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_incentives")
    c.execute("INSERT INTO employee_incentive_settings(id,kind,rate_bps,fixed_cents,active,updated_at,updated_by) VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind) DO UPDATE SET rate_bps=excluded.rate_bps,fixed_cents=excluded.fixed_cents,active=excluded.active,updated_at=excluded.updated_at,updated_by=excluded.updated_by",(str(uuid.uuid4()),x.kind,x.rate_bps,x.fixed_cents,1 if x.active else 0,now(),actor["id"]))
    audit(c,actor["id"],"employee_incentive_setting_changed","incentive",x.kind,None,str(x.rate_bps)+"/"+str(x.fixed_cents),request)
    c.commit(); c.close()
    return {"status":"updated","kind":x.kind,"rate_bps":x.rate_bps,"fixed_cents":x.fixed_cents,"active":x.active}

@app.get("/api/v1/company")
def company_info(token:str):
    c=db(); company_actor(c,token)
    p=c.execute("SELECT * FROM company_profile ORDER BY created_at LIMIT 1").fetchone()
    ds=c.execute("""SELECT d.*,COUNT(e.id) employee_count,a.name manager_name FROM departments d
                    LEFT JOIN employees e ON e.department_id=d.id AND e.employment_status='active'
                    LEFT JOIN accounts a ON a.id=d.manager_account_id GROUP BY d.id ORDER BY d.name""").fetchall()
    es=c.execute("""SELECT e.id,e.employee_number,e.job_title,e.employment_status,e.joined_at,a.id account_id,a.name,a.email,a.role,
                    d.id department_id,d.name department_name FROM employees e JOIN accounts a ON a.id=e.account_id
                    JOIN departments d ON d.id=e.department_id ORDER BY d.name,a.name""").fetchall()
    return {"company":dict(p) if p else None,"departments":[dict(x) for x in ds],"employees":[dict(x) for x in es],"roles":list(ROLE_PERMISSIONS),"referral_codes":[dict(r) for r in c.execute("SELECT employee_id,code,active FROM referral_codes")] }

@app.post("/api/v1/company/profile")
def update_company_profile(x:CompanyProfileUpdate,token:str,request:Request):
    c=db(); actor=company_actor(c,token)
    p=c.execute("SELECT id FROM company_profile ORDER BY created_at LIMIT 1").fetchone()
    if p: cid=p["id"]; c.execute("UPDATE company_profile SET legal_name=?,display_name=?,registration_number=?,country=?,updated_at=? WHERE id=?",(x.legal_name.strip(),x.display_name.strip(),x.registration_number.strip(),x.country.strip(),now(),cid))
    else: cid=str(uuid.uuid4()); c.execute("INSERT INTO company_profile VALUES(?,?,?,?,?,?,?,?)",(cid,x.legal_name.strip(),x.display_name.strip(),x.registration_number.strip(),x.country.strip(),"active",now(),now()))
    audit(c,actor["id"],"company_profile_updated","company",cid,None,x.display_name.strip(),request); c.commit(); c.close()
    return {"company_id":cid,"status":"updated"}

@app.post("/api/v1/company/departments")
def create_department(x:DepartmentCreate,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_departments"); did=str(uuid.uuid4())
    try: c.execute("INSERT INTO departments(id,name,code,description,created_at) VALUES(?,?,?,?,?)",(did,x.name.strip(),x.code.strip().upper(),x.description.strip(),now()))
    except sqlite3.IntegrityError: c.close(); raise HTTPException(409,"department name or code already exists")
    audit(c,actor["id"],"department_created","department",did,None,x.name.strip(),request); c.commit(); c.close(); return {"id":did,"status":"created"}

@app.post("/api/v1/company/employees")
def create_employee(x:EmployeeCreate,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_employees")
    account=c.execute("SELECT * FROM accounts WHERE id=? OR lower(email)=?",(x.account_id.strip(),x.account_email.strip().lower())).fetchone()
    if not account or not c.execute("SELECT id FROM departments WHERE id=? AND status='active'",(x.department_id,)).fetchone():
        c.close(); raise HTTPException(404,"account or department not found")
    account_id=account["id"]
    if c.execute("SELECT id FROM employees WHERE account_id=?",(account_id,)).fetchone(): c.close(); raise HTTPException(409,"account is already an employee")
    number=x.employee_number.strip() or "NQ-"+uuid.uuid4().hex[:8].upper()
    if c.execute("SELECT id FROM employees WHERE employee_number=?",(number,)).fetchone(): c.close(); raise HTTPException(409,"employee number already exists")
    eid=str(uuid.uuid4()); c.execute("INSERT INTO employees(id,account_id,employee_number,department_id,job_title,employment_status,joined_at,created_at) VALUES(?,?,?,?,?,'active',?,?)",(eid,account_id,number,x.department_id,x.job_title.strip(),now(),now()))
    referral_id=str(uuid.uuid4()); referral_code="NAQ-"+secrets.token_hex(4).upper()
    c.execute("INSERT INTO referral_codes(id,employee_id,code,active,created_at) VALUES(?,?,?,?,?)",(referral_id,eid,referral_code,1,now()))
    audit(c,actor["id"],"employee_created","employee",eid,None,number,request); c.commit(); c.close(); return {"id":eid,"employee_number":number,"status":"active"}

@app.post("/api/v1/company/employee-invites")
def create_employee_invite(x:EmployeeInviteCreate,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_employees")
    email=x.email.strip().lower()
    if c.execute("SELECT id FROM accounts WHERE lower(email)=?",(email,)).fetchone():
        c.close(); raise HTTPException(409,"هذا البريد لديه حساب بالفعل؛ استخدم إضافة الموظف للحساب الموجود")
    dep=c.execute("SELECT id FROM departments WHERE id=? AND status='active'",(x.department_id,)).fetchone()
    if not dep:
        c.close(); raise HTTPException(404,"department not found")
    # Keep only one active invitation per email.
    c.execute("UPDATE employee_invitations SET status='cancelled' WHERE lower(email)=? AND status='pending'",(email,))
    invite_id=str(uuid.uuid4()); invite_token=secrets.token_urlsafe(32)
    expires=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()
    c.execute("""INSERT INTO employee_invitations(id,token,email,name,department_id,job_title,invited_by,expires_at,status,created_at,used_at)
                 VALUES(?,?,?,?,?,?,?,?,'pending',?,NULL)""",
              (invite_id,invite_token,email,x.name.strip(),x.department_id,x.job_title.strip(),actor["id"],expires,now()))
    audit(c,actor["id"],"employee_invited","employee_invitation",invite_id,None,email,request)
    c.commit(); c.close()
    return {"id":invite_id,"email":email,"expires_at":expires,"token":invite_token,"activation_path":"employee-activate.html?invite="+invite_token}

@app.get("/api/v1/company/employee-invites/{invite_token}")
def get_employee_invite(invite_token:str):
    c=db(); row=c.execute("""SELECT i.name,i.email,i.job_title,i.expires_at,i.status,d.name department_name
                             FROM employee_invitations i JOIN departments d ON d.id=i.department_id
                             WHERE i.token=?""",(invite_token,)).fetchone()
    c.close()
    if not row: raise HTTPException(404,"دعوة الموظف غير موجودة")
    if row["status"]!="pending": raise HTTPException(410,"دعوة الموظف لم تعد صالحة")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(410,"انتهت صلاحية دعوة الموظف")
    return dict(row)

class EmployeeInviteActivate(BaseModel):
    token:str
    password:str=Field(min_length=8)

@app.post("/api/v1/company/employee-invites/activate")
def activate_employee_invite(x:EmployeeInviteActivate):
    c=db(); row=c.execute("SELECT * FROM employee_invitations WHERE token=?",(x.token.strip(),)).fetchone()
    if not row: c.close(); raise HTTPException(404,"دعوة الموظف غير موجودة")
    if row["status"]!="pending": c.close(); raise HTTPException(410,"دعوة الموظف لم تعد صالحة")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        c.close(); raise HTTPException(410,"انتهت صلاحية دعوة الموظف")
    if c.execute("SELECT id FROM accounts WHERE lower(email)=?",(row["email"].lower(),)).fetchone():
        c.close(); raise HTTPException(409,"هذا البريد أصبح مرتبطًا بحساب")
    account_id=str(uuid.uuid4()); verified=now()
    c.execute("""INSERT INTO accounts(id,role,name,email,status,created_at,password_hash,account_type,phone,email_verified_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (account_id,"employee",row["name"],row["email"],"active",verified,hp(x.password),"individual","",verified))
    wallet_id=str(uuid.uuid4())
    c.execute("INSERT INTO wallets(id,account_id,currency,balance,updated_at,balance_cents,held_cents) VALUES(?,?,?,?,?,?,?)",
              (wallet_id,account_id,"USD",0,verified,0,0))
    ensure_wallet_ledger(c,account_id,"USD",wallet_id)
    employee_id=str(uuid.uuid4()); employee_number="NQ-"+uuid.uuid4().hex[:8].upper()
    c.execute("""INSERT INTO employees(id,account_id,employee_number,department_id,job_title,employment_status,joined_at,created_at)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (employee_id,account_id,employee_number,row["department_id"],row["job_title"],"active",verified,verified))
    referral_id=str(uuid.uuid4()); referral_code="NAQ-"+secrets.token_hex(4).upper()
    c.execute("INSERT INTO referral_codes(id,employee_id,code,active,created_at) VALUES(?,?,?,?,?)",
              (referral_id,employee_id,referral_code,1,verified))
    c.execute("UPDATE employee_invitations SET status='used',used_at=? WHERE id=?",(verified,row["id"]))
    session=secrets.token_urlsafe(32)
    c.execute("INSERT INTO sessions VALUES(?,?,?)",(session,account_id,verified))
    c.commit()
    a=c.execute("SELECT * FROM accounts WHERE id=?",(account_id,)).fetchone()
    c.close()
    return {"status":"activated","token":session,"employee_number":employee_number,"referral_code":referral_code,"user":account_public(a)}

@app.post("/api/v1/company/employees/{employee_id}/role")
def update_employee_role(employee_id:str,x:EmployeeRoleUpdate,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_employees")
    e=c.execute("SELECT e.*,a.role FROM employees e JOIN accounts a ON a.id=e.account_id WHERE e.id=?",(employee_id,)).fetchone()
    if not e: c.close(); raise HTTPException(404,"employee not found")
    old=e["role"]; c.execute("UPDATE accounts SET role=? WHERE id=?",(x.role,e["account_id"]))
    audit(c,actor["id"],"employee_role_changed","employee",employee_id,old,x.role,request); c.commit(); c.close(); return {"employee_id":employee_id,"role":x.role,"previous_role":old}

@app.post("/api/v1/company/employees/{employee_id}/status")
def update_employee_status(employee_id:str,status:str,token:str,request:Request):
    c=db(); actor=company_actor(c,token,"manage_employees")
    if status not in ("active","suspended","terminated"): c.close(); raise HTTPException(400,"invalid employee status")
    if not c.execute("SELECT id FROM employees WHERE id=?",(employee_id,)).fetchone(): c.close(); raise HTTPException(404,"employee not found")
    c.execute("UPDATE employees SET employment_status=? WHERE id=?",(status,employee_id)); audit(c,actor["id"],"employee_status_changed","employee",employee_id,None,status,request); c.commit(); c.close(); return {"employee_id":employee_id,"status":status}

@app.post("/api/v1/admin/accounts/{account_id}/role")
def update_account_role(account_id:str,x:RoleUpdate,request:Request):
    require_admin(request)
    c=db(); c.execute("BEGIN IMMEDIATE")
    a=c.execute("SELECT * FROM accounts WHERE id=?",(account_id,)).fetchone()
    if not a:
        c.rollback(); c.close(); raise HTTPException(404,"account not found")
    old=a["role"]
    if old==x.role:
        c.commit(); c.close(); return {"account_id":account_id,"role":old,"duplicate":True}
    c.execute("UPDATE accounts SET role=? WHERE id=?",(x.role,account_id))
    audit(c,"finance-admin","account_role_changed","account",account_id,old,x.role,request)
    c.commit(); c.close()
    return {"account_id":account_id,"role":x.role,"previous_role":old}

@app.get("/api/v1/manager/listings")
def manager_listings(token:str,status:str="draft"):
    c=db(); manager_for(c,token)
    allowed={"draft","rejected","published","all"}
    if status not in allowed:
        c.close(); raise HTTPException(400,"status must be draft, rejected, published or all")
    where="" if status=="all" else "WHERE l.status=?"
    params=() if status=="all" else (status,)
    rows=c.execute(f"""
        SELECT l.id,l.seller_id,l.category,l.title,l.description,l.amount,l.currency,l.status,l.created_at,l.image_data,
               a.name seller_name,a.email seller_email,a.status seller_status
        FROM listings l JOIN accounts a ON a.id=l.seller_id
        {where}
        ORDER BY l.created_at DESC
    """,params).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/v1/manager/listings/{listing_id}/publish")
def manager_publish_listing(listing_id:str,token:str,request:Request):
    c=db(); manager=manager_for(c,token); c.execute("BEGIN IMMEDIATE")
    r=c.execute("SELECT * FROM listings WHERE id=?",(listing_id,)).fetchone()
    if not r:
        c.rollback(); c.close(); raise HTTPException(404,"listing not found")
    if r["status"]=="published":
        c.commit(); c.close(); return {"id":listing_id,"status":"published","duplicate":True}
    if r["status"] not in ("draft","rejected"):
        c.rollback(); c.close(); raise HTTPException(409,"only draft or rejected listings can be published")
    if not r["title"].strip() or not r["description"].strip() or float(r["amount"])<=0:
        c.rollback(); c.close(); raise HTTPException(422,"listing must have title, description and a positive price before publication")
    c.execute("UPDATE listings SET status='published' WHERE id=?",(listing_id,))
    audit(c,manager["id"],"listing_published","listing",listing_id,r["status"],"published",request)
    c.commit(); c.close()
    return {"id":listing_id,"status":"published","published_by":manager["id"]}

@app.post("/api/v1/manager/listings/{listing_id}/reject")
def manager_reject_listing(listing_id:str,token:str,request:Request):
    c=db(); manager=manager_for(c,token); c.execute("BEGIN IMMEDIATE")
    r=c.execute("SELECT id,status FROM listings WHERE id=?",(listing_id,)).fetchone()
    if not r:
        c.rollback(); c.close(); raise HTTPException(404,"listing not found")
    if r["status"]=="published":
        c.rollback(); c.close(); raise HTTPException(409,"published listings cannot be rejected from this queue")
    if r["status"]=="rejected":
        c.commit(); c.close(); return {"id":listing_id,"status":"rejected","duplicate":True}
    c.execute("UPDATE listings SET status='rejected' WHERE id=?",(listing_id,))
    audit(c,manager["id"],"listing_rejected","listing",listing_id,r["status"],"rejected",request)
    c.commit(); c.close()
    return {"id":listing_id,"status":"rejected","rejected_by":manager["id"]}

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

def manager_for(c, token):
    actor=account_for(c,token)
    if actor["role"]!="manager":
        raise HTTPException(403,"manager authorization required")
    return actor

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
    lines=c.execute("SELECT ledger_account_id,side,amount_cents,created_at FROM journal_lines WHERE entry_id=?",(e["id"],)).fetchall()
    total_debit_cents=sum(int(x["amount_cents"]) for x in lines if x["side"]=="debit")
    total_credit_cents=sum(int(x["amount_cents"]) for x in lines if x["side"]=="credit")
    c.close()
    return {"entry":dict(e),"lines":[{**dict(x),"amount":float(amount(x["amount_cents"]))} for x in lines],"total_debit_cents":total_debit_cents,"total_credit_cents":total_credit_cents}


def _report_window(start:str|None,end:str|None):
    return start or "", end or "\uffff"

def _admin_report(request:Request):
    require_admin(request)
    return db()

@app.get("/api/v1/admin/reports/wallet-movements")
def report_wallet_movements(request:Request,start:str|None=None,end:str|None=None):
    c=_admin_report(request); lo,hi=_report_window(start,end)
    rows=c.execute("""
      SELECT w.account_id,w.currency,
        COALESCE((SELECT SUM(jl.amount_cents) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
          WHERE jl.ledger_account_id='WALLET:'||w.id AND jl.side='credit' AND je.created_at>=? AND je.created_at<=?),0) credits_cents,
        COALESCE((SELECT SUM(jl.amount_cents) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
          WHERE jl.ledger_account_id='WALLET:'||w.id AND jl.side='debit' AND je.created_at>=? AND je.created_at<=?),0) debits_cents,
        (SELECT COUNT(DISTINCT je.id) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
          WHERE jl.ledger_account_id='WALLET:'||w.id AND je.created_at>=? AND je.created_at<=?) journal_entries
      FROM wallets w ORDER BY w.account_id
    """,(lo,hi,lo,hi,lo,hi)).fetchall()
    out=[]
    for r in rows:
        credits=int(r["credits_cents"] or 0); debits=int(r["debits_cents"] or 0)
        out.append({**dict(r),"credits":float(amount(credits)),"debits":float(amount(debits)),
                    "net_movement":float(amount(credits-debits))})
    c.close()
    return {"start":start,"end":end,"currency":"USD","wallets":out,"source":"double_entry_ledger"}

@app.get("/api/v1/admin/reports/commissions")
def report_commissions(request:Request,start:str|None=None,end:str|None=None):
    c=_admin_report(request); lo,hi=_report_window(start,end)
    rows=c.execute("""
      SELECT side,currency,COUNT(*) entries,COALESCE(SUM(amount_cents),0) amount_cents,
             COALESCE(SUM(CASE WHEN side='buyer' THEN amount_cents ELSE 0 END),0) buyer_cents,
             COALESCE(SUM(CASE WHEN side='seller' THEN amount_cents ELSE 0 END),0) seller_cents
      FROM commission_entries WHERE created_at>=? AND created_at<=?
      GROUP BY side,currency ORDER BY side
    """,(lo,hi)).fetchall()
    total=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM commission_entries WHERE created_at>=? AND created_at<=?",(lo,hi)).fetchone()["n"]
    c.close()
    return {"start":start,"end":end,"total_commission":float(amount(int(total or 0))),
            "breakdown":[{**dict(r),"amount":float(amount(int(r["amount_cents"] or 0))),
                          "buyer":float(amount(int(r["buyer_cents"] or 0))),
                          "seller":float(amount(int(r["seller_cents"] or 0)))} for r in rows],
            "source":"posted_commission_entries"}

@app.get("/api/v1/admin/reports/escrow")
def report_escrow(request:Request,start:str|None=None,end:str|None=None):
    c=_admin_report(request); lo,hi=_report_window(start,end)
    rows=c.execute("""
      SELECT status,currency,COUNT(*) entries,COALESCE(SUM(amount_cents),0) amount_cents
      FROM escrow_transactions WHERE held_at>=? AND held_at<=?
      GROUP BY status,currency ORDER BY status
    """,(lo,hi)).fetchall()
    held=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM escrow_transactions WHERE status='held'").fetchone()["n"]
    released=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM escrow_transactions WHERE status='released'").fetchone()["n"]
    refunded=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM escrow_transactions WHERE status='refunded'").fetchone()["n"]
    c.close()
    return {"start":start,"end":end,
            "current":{"held":float(amount(int(held or 0))),"released":float(amount(int(released or 0))),
                       "refunded":float(amount(int(refunded or 0)))},
            "breakdown":[{**dict(r),"amount":float(amount(int(r["amount_cents"] or 0)))} for r in rows],
            "source":"escrow_transactions"}

@app.get("/api/v1/admin/reports/company-revenue")
def report_company_revenue(request:Request,start:str|None=None,end:str|None=None):
    c=_admin_report(request); lo,hi=_report_window(start,end)
    row=c.execute("""
      SELECT COALESCE(SUM(CASE WHEN jl.side='credit' THEN jl.amount_cents ELSE 0 END),0) credits_cents,
             COALESCE(SUM(CASE WHEN jl.side='debit' THEN jl.amount_cents ELSE 0 END),0) debits_cents,
             COUNT(DISTINCT je.id) entries
      FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
      WHERE jl.ledger_account_id='SYSTEM:COMMISSION_REVENUE' AND je.created_at>=? AND je.created_at<=?
    """,(lo,hi)).fetchone()
    credits=int(row["credits_cents"] or 0); debits=int(row["debits_cents"] or 0)
    c.close()
    return {"start":start,"end":end,"currency":"USD","commission_revenue":float(amount(credits-debits)),
            "credits":float(amount(credits)),"debits":float(amount(debits)),
            "journal_entries":int(row["entries"] or 0),"source":"commission_revenue_ledger"}

@app.get("/api/v1/admin/reports/journal")
def report_journal(request:Request,start:str|None=None,end:str|None=None,limit:int=200):
    c=_admin_report(request); lo,hi=_report_window(start,end)
    limit=max(1,min(int(limit),1000))
    entries=c.execute("""
      SELECT id,reference,entry_type,description,idempotency_key,created_at
      FROM journal_entries WHERE created_at>=? AND created_at<=?
      ORDER BY created_at DESC LIMIT ?
    """,(lo,hi,limit)).fetchall()
    out=[]
    for e in entries:
        lines=c.execute("SELECT ledger_account_id,side,amount_cents,created_at FROM journal_lines WHERE entry_id=? ORDER BY id",(e["id"],)).fetchall()
        out.append({"entry":dict(e),"lines":[{**dict(x),"amount":float(amount(int(x["amount_cents"])))} for x in lines]})
    c.close()
    return {"start":start,"end":end,"limit":limit,"entries":out,"source":"journal_entries"}

@app.get("/api/v1/admin/reports/daily-reconciliation")
def report_daily_reconciliation(request:Request,start:str|None=None,end:str|None=None):
    c=_admin_report(request); lo,hi=_report_window(start,end)
    rows=c.execute("""
      SELECT substr(created_at,1,10) day,
             COUNT(*) entries,
             COALESCE(SUM(CASE WHEN entry_type='marketplace_settlement' THEN 1 ELSE 0 END),0) marketplace_settlements,
             COALESCE(SUM(CASE WHEN entry_type='deposit' THEN 1 ELSE 0 END),0) deposits,
             COALESCE(SUM(CASE WHEN entry_type='withdrawal' THEN 1 ELSE 0 END),0) withdrawals,
             COALESCE(SUM(CASE WHEN entry_type='wallet_transfer' THEN 1 ELSE 0 END),0) wallet_transfers
      FROM journal_entries WHERE created_at>=? AND created_at<=?
      GROUP BY substr(created_at,1,10) ORDER BY day DESC
    """,(lo,hi)).fetchall()
    bad=[]
    for e in c.execute("SELECT id,reference FROM journal_entries WHERE created_at>=? AND created_at<=?",(lo,hi)).fetchall():
        d=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM journal_lines WHERE entry_id=? AND side='debit'",(e["id"],)).fetchone()["n"]
        cr=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM journal_lines WHERE entry_id=? AND side='credit'",(e["id"],)).fetchone()["n"]
        if int(d or 0)!=int(cr or 0): bad.append({"reference":e["reference"],"debit_cents":int(d or 0),"credit_cents":int(cr or 0)})
    c.close()
    return {"start":start,"end":end,"ok":not bad,"days":[dict(r) for r in rows],
            "unbalanced_entries":bad,"source":"journal_entries"}

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
    kyc_ok=bool(kyc and kyc["status"]=="approved")
    kyb_ok=bool(kyb and kyb["status"]=="approved")
    production_eligible=kyb_ok if a["account_type"]=="company" else kyc_ok
    return {"account_id":a["id"],"kyc":dict(kyc) if kyc else None,"kyb":dict(kyb) if kyb else None,
            "production_eligible":production_eligible}

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
    i=str(uuid.uuid4()); c.execute("INSERT INTO listings(id,seller_id,category,title,description,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(i,actor["id"],x.category,x.title.strip(),x.description.strip(),float(amount(cents)),"USD","draft",now())); c.commit(); c.close(); return {"id":i,"status":"draft"}
@app.post("/api/v1/listings/{listing_id}/publish")
def publish(listing_id:str,token:str):
    c=db(); actor=account_for(c,token)
    r=c.execute("SELECT id,seller_id,status FROM listings WHERE id=?",(listing_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"listing not found")
    if actor["id"]!=r["seller_id"] or actor["role"]!="seller":
        c.close(); raise HTTPException(403,"only the listing seller can publish it")
    if r["status"] not in ("draft","rejected"):
        c.close(); raise HTTPException(409,"listing is not publishable in its current state")
    c.execute("UPDATE listings SET status='published' WHERE id=?",(listing_id,)); c.commit(); c.close(); return {"id":listing_id,"status":"published"}
@app.post("/api/v1/listings/{listing_id}/image")
async def listing_image(listing_id:str, request:Request, token:str):
    c=db(); actor=account_for(c,token)
    r=c.execute("SELECT id,seller_id FROM listings WHERE id=?",(listing_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"listing not found")
    if actor["id"]!=r["seller_id"] or actor["role"]!="seller": c.close(); raise HTTPException(403,"only the listing seller can upload its image")
    try: payload=await request.json(); data=payload.get("image_data")
    except Exception: c.close(); raise HTTPException(400,"invalid image payload")
    if not isinstance(data,str) or not data.startswith("data:image/"): c.close(); raise HTTPException(400,"image_data must be a data:image/... URL")
    if len(data)>4_500_000: c.close(); raise HTTPException(413,"image is too large")
    c.execute("UPDATE listings SET image_data=? WHERE id=?",(data,listing_id)); c.commit(); c.close()
    return {"id":listing_id,"image_uploaded":True}

@app.get("/api/v1/listings")
def listings():
    c=db(); rows=c.execute("SELECT id,category,title,description,amount,currency,status,created_at,image_data FROM listings WHERE status='published' ORDER BY created_at DESC").fetchall(); c.close(); return [dict(r) for r in rows]
@app.get("/api/v1/offers/mine")
def my_offers(token:str):
    c=db(); actor=account_for(c,token)
    if actor["role"]=="seller":
        rows=c.execute("""SELECT o.id,o.listing_id,o.buyer_id,o.amount,o.currency,o.status,o.created_at,
                                 l.title,l.category,ord.id order_id,ord.status order_status,
                                 ord.settlement_mode,ord.seller_choice,ord.buyer_choice
                          FROM offers o JOIN listings l ON l.id=o.listing_id
                          LEFT JOIN orders ord ON ord.offer_id=o.id
                          WHERE l.seller_id=? ORDER BY o.created_at DESC""",(actor["id"],)).fetchall()
    else:
        rows=c.execute("""SELECT o.id,o.listing_id,o.buyer_id,o.amount,o.currency,o.status,o.created_at,
                                 l.title,l.category,ord.id order_id,ord.status order_status,
                                 ord.settlement_mode,ord.seller_choice,ord.buyer_choice,
                                 ord.buyer_fee_cents,ord.seller_fee_cents,ord.buyer_total_cents
                          FROM offers o JOIN listings l ON l.id=o.listing_id
                          LEFT JOIN orders ord ON ord.offer_id=o.id
                          WHERE o.buyer_id=? ORDER BY o.created_at DESC""",(actor["id"],)).fetchall()
    data=[dict(r) for r in rows]
    c.close(); return data

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
def accept(offer_id:str,token:str,x:SettlementChoice|None=None):
    c=db(); actor=account_for(c,token)
    seller_choice=(x.settlement_mode if x else "off_platform")
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
    rule=active_commission(c,"marketplace","USD")
    if rule:
        buyer_rate_bps=round(float(rule["buyer_rate"])*10000); seller_rate_bps=round(float(rule["seller_rate"])*10000)
        buyer_fixed_cents=round(float(rule["buyer_fixed"])*100); seller_fixed_cents=round(float(rule["seller_fixed"])*100)
        minimum_fee_cents=round(float(rule["minimum_fee"])*100)
        maximum_fee_cents=None if rule["maximum_fee"] is None else round(float(rule["maximum_fee"])*100)
    else:
        buyer_rate_bps=seller_rate_bps=buyer_fixed_cents=seller_fixed_cents=minimum_fee_cents=0; maximum_fee_cents=None
    bf=fee_cents(gross,buyer_rate_bps,buyer_fixed_cents,minimum_fee_cents,maximum_fee_cents)
    sf=fee_cents(gross,seller_rate_bps,seller_fixed_cents,minimum_fee_cents,maximum_fee_cents)
    buyer_total=gross+bf; seller_net=gross-sf
    if seller_net<=0: c.rollback(); c.close(); raise HTTPException(400,"commission leaves no positive seller settlement")
    c.execute("""INSERT INTO orders(id,offer_id,listing_id,buyer_id,seller_id,gross_cents,commission_cents,status,created_at,settlement_mode,seller_choice,buyer_choice,buyer_fee_cents,seller_fee_cents,buyer_total_cents,seller_net_cents,commission_rule_id,buyer_rate_bps,seller_rate_bps,buyer_fixed_cents,seller_fixed_cents,minimum_fee_cents,maximum_fee_cents)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (order_id,offer_id,r["listing_id"],r["buyer_id"],r["seller_id"],gross,0,"awaiting_buyer_choice",now(),seller_choice,seller_choice,None,0,0,0,0,
               None if not rule else rule["id"],buyer_rate_bps,seller_rate_bps,buyer_fixed_cents,seller_fixed_cents,minimum_fee_cents,maximum_fee_cents))
    c.commit(); c.close()
    return {"id":offer_id,"status":"accepted","order_id":order_id,"settlement_mode":seller_choice,
            "seller_choice":seller_choice,"payment_status":"awaiting_buyer_choice","wallet_only":True,"escrow":False}
@app.post("/api/v1/orders/{order_id}/settlement-choice")
def choose_settlement(order_id:str,token:str,x:SettlementChoice,request:Request):
    c=db(); buyer=account_for(c,token); c.execute("BEGIN IMMEDIATE")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.rollback(); c.close(); raise HTTPException(404,"order not found")
    if o["buyer_id"]!=buyer["id"]: c.rollback(); c.close(); raise HTTPException(403,"order access denied")
    if o["status"]!="awaiting_buyer_choice": c.rollback(); c.close(); raise HTTPException(409,"settlement choice is no longer available")
    if x.settlement_mode=="naqa_protected" and o["seller_choice"]!="naqa_protected":
        c.rollback(); c.close(); raise HTTPException(409,"the seller did not choose NAQAA protected settlement")
    if x.settlement_mode=="off_platform":
        seller_wallet=c.execute("""SELECT ca.address FROM crypto_addresses ca
                                  JOIN crypto_wallets cw ON cw.id=ca.wallet_id
                                  WHERE cw.account_id=? AND cw.asset='USDT' AND cw.network='BEP20'
                                    AND ca.status='active' AND ca.verification_status='verified'
                                  ORDER BY ca.verified_at DESC LIMIT 1""",(o["seller_id"],)).fetchone()
        if not seller_wallet:
            c.rollback(); c.close(); raise HTTPException(409,"seller must verify a USDT/BEP20 external wallet before accepting direct payment")
        c.execute("UPDATE orders SET settlement_mode='off_platform',buyer_choice='off_platform',status='awaiting_external_payment' WHERE id=?",(order_id,))
        audit(c,buyer["id"],"settlement_choice","order",order_id,"awaiting_buyer_choice","awaiting_external_payment",request)
        c.commit(); c.close()
        return {"order_id":order_id,"status":"awaiting_external_payment","settlement_mode":"off_platform",
                "commission":0,"escrow":False,"payment_status":"direct_external_payment",
                "asset":"USDT","network":"BEP20","recipient_address":seller_wallet["address"],
                "amount_usdt":float(amount(int(o["gross_cents"])))}
    gross=int(o["gross_cents"])
    bf=fee_cents(gross,int(o["buyer_rate_bps"] or 0),int(o["buyer_fixed_cents"] or 0),int(o["minimum_fee_cents"] or 0),None if o["maximum_fee_cents"] is None else int(o["maximum_fee_cents"]))
    sf=fee_cents(gross,int(o["seller_rate_bps"] or 0),int(o["seller_fixed_cents"] or 0),int(o["minimum_fee_cents"] or 0),None if o["maximum_fee_cents"] is None else int(o["maximum_fee_cents"]))
    buyer_total=gross+bf; seller_net=gross-sf
    if seller_net<=0: c.rollback(); c.close(); raise HTTPException(400,"commission leaves no positive seller settlement")
    c.execute("UPDATE orders SET settlement_mode='naqa_protected',buyer_choice='naqa_protected',buyer_fee_cents=?,seller_fee_cents=?,buyer_total_cents=?,seller_net_cents=?,commission_cents=?,status='awaiting_payment',protected_at=? WHERE id=?",(bf,sf,buyer_total,seller_net,bf+sf,now(),order_id))
    audit(c,buyer["id"],"settlement_choice","order",order_id,"awaiting_buyer_choice","naqa_protected",request)
    c.commit(); c.close()
    return {"order_id":order_id,"status":"awaiting_payment","settlement_mode":"naqa_protected","gross":float(amount(gross)),"buyer_fee":float(amount(bf)),"seller_fee":float(amount(sf)),"buyer_total":float(amount(buyer_total)),"seller_net":float(amount(seller_net)),"escrow":True,"payment_status":"awaiting_payment"}

@app.post("/api/v1/orders/{order_id}/pay")
def pay_order(order_id:str,token:str,request:Request):
    key=idem(request); c=db(); buyer=account_for(c,token)
    if buyer["role"]!="buyer": c.close(); raise HTTPException(403,"only a buyer can pay an order")
    c.execute("BEGIN IMMEDIATE")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.rollback(); c.close(); raise HTTPException(404,"order not found")
    if o["buyer_id"]!=buyer["id"]: c.rollback(); c.close(); raise HTTPException(403,"order access denied")
    if o["settlement_mode"]!="naqa_protected": c.rollback(); c.close(); raise HTTPException(409,"this order is outside NAQAA protected settlement")
    if o["status"]=="paid":
        ref=o["payment_reference"]; c.commit(); c.close(); return {"order_id":order_id,"status":"paid","reference":ref,"duplicate":True}
    if o["status"]!="awaiting_payment": c.rollback(); c.close(); raise HTTPException(409,"order is not payable")
    kyc=c.execute("SELECT status FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1",(buyer["id"],)).fetchone()
    if not kyc or kyc["status"]!="approved":
        c.rollback(); c.close(); raise HTTPException(403,"approved KYC is required before marketplace payment")
    seller_kyc=c.execute("SELECT status FROM kyc_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 1",(o["seller_id"],)).fetchone()
    if not seller_kyc or seller_kyc["status"]!="approved":
        c.rollback(); c.close(); raise HTTPException(403,"seller verification is required before marketplace settlement")
    gross=int(o["gross_cents"])
    bf=int(o["buyer_fee_cents"] or 0); sf=int(o["seller_fee_cents"] or 0)
    buyer_total=int(o["buyer_total_cents"] or gross+bf); seller_net=int(o["seller_net_cents"] or gross-sf)
    if buyer_total < gross or seller_net < 0:
        c.rollback(); c.close(); raise HTTPException(409,"invalid stored commission snapshot")
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
    for side,fee,rate,fixed in [("buyer",bf,int(o["buyer_rate_bps"] or 0),int(o["buyer_fixed_cents"] or 0)),
                                ("seller",sf,int(o["seller_rate_bps"] or 0),int(o["seller_fixed_cents"] or 0))]:
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
    if o["settlement_mode"]!="naqa_protected": c.rollback(); c.close(); raise HTTPException(409,"escrow is only available for NAQAA protected orders")
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
    if o["settlement_mode"]!="naqa_protected": c.rollback(); c.close(); raise HTTPException(409,"disputes and escrow protection are only available for NAQAA protected orders")
    if o["status"]!="paid":
        c.rollback(); c.close(); raise HTTPException(409,"only paid orders can be disputed")
    existing=c.execute("SELECT id,status FROM disputes WHERE transaction_reference=? AND status IN ('open','under_review')",(o["payment_reference"],)).fetchone()
    if existing:
        c.rollback(); c.close(); return {"dispute_id":existing["id"],"status":existing["status"],"duplicate":True}
    did=str(uuid.uuid4())
    c.execute("INSERT INTO disputes(id,transaction_reference,opened_by,amount_cents,reason,status,created_at) VALUES(?,?,?,?,?,?,?)",
              (did,o["payment_reference"],actor["id"],int(o["gross_cents"]),x.reason.strip(),"open",now()))
    audit(c,actor["id"],"dispute_opened","dispute",did,None,"open")
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
        audit(c,"finance-admin","dispute_rejected","dispute",dispute_id,"open","rejected",request)
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
    c.execute("UPDATE escrow_transactions SET status='refunded',released_at=COALESCE(released_at,?) WHERE order_id=? AND status IN ('held','released')",(now(),o["id"]))
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

@app.post("/api/v1/orders/{order_id}/external-payment/verify")
def verify_external_payment(order_id:str,token:str,tx_hash:str,request:Request):
    """Verify a direct USDT/BEP20 payment sent from any wallet to the seller's verified external wallet.
    NAQAA never takes custody of these funds; it only verifies the public blockchain transaction.
    """
    import httpx, re
    c=db(); buyer=account_for(c,token)
    if buyer["role"]!="buyer":
        c.close(); raise HTTPException(403,"only a buyer can verify an external payment")
    o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.close(); raise HTTPException(404,"order not found")
    if o["buyer_id"]!=buyer["id"]: c.close(); raise HTTPException(403,"order access denied")
    if o["settlement_mode"]!="off_platform" or o["status"]!="awaiting_external_payment":
        c.close(); raise HTTPException(409,"order is not awaiting direct external payment")
    tx_hash=tx_hash.strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{64}",tx_hash):
        c.close(); raise HTTPException(400,"invalid BSC transaction hash")
    duplicate=c.execute("SELECT id FROM orders WHERE external_tx_hash=? AND id<>?",(tx_hash,order_id)).fetchone()
    if duplicate:
        c.close(); raise HTTPException(409,"this transaction hash is already linked to another order")
    recipient=c.execute("""SELECT ca.address FROM crypto_addresses ca
                           JOIN crypto_wallets cw ON cw.id=ca.wallet_id
                           WHERE cw.account_id=? AND cw.asset='USDT' AND cw.network='BEP20'
                             AND ca.status='active' AND ca.verification_status='verified'
                           ORDER BY ca.verified_at DESC LIMIT 1""",(o["seller_id"],)).fetchone()
    if not recipient:
        c.close(); raise HTTPException(409,"seller external wallet is not verified")
    rpc=os.getenv("BSC_RPC_URL","https://bsc-dataseed.binance.org")
    token_contract=os.getenv("BSC_USDT_CONTRACT","0x55d398326f99059fF775485246999027B3197955").lower()
    try:
        with httpx.Client(timeout=15.0) as client:
            chain=client.post(rpc,json={"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}).json()
            receipt=client.post(rpc,json={"jsonrpc":"2.0","id":2,"method":"eth_getTransactionReceipt","params":[tx_hash]}).json()
    except Exception as exc:
        c.close(); raise HTTPException(503,"BSC verification service is temporarily unavailable") from exc
    if str(chain.get("result","")).lower()!="0x38":
        c.close(); raise HTTPException(503,"configured RPC is not connected to BSC mainnet")
    result=receipt.get("result")
    if not result:
        c.close(); raise HTTPException(409,"transaction is not confirmed on BSC yet")
    if str(result.get("status","")).lower()!="0x1":
        c.close(); raise HTTPException(409,"transaction failed on BSC")
    expected_units=int(o["gross_cents"])*10000
    recipient_topic="0x"+"0"*24+recipient["address"][2:].lower()
    transfer_topic="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    matched=False
    for log in result.get("logs",[]):
        if str(log.get("address","")).lower()!=token_contract: continue
        topics=log.get("topics") or []
        if len(topics)<3 or str(topics[0]).lower()!=transfer_topic: continue
        if str(topics[2]).lower()!=recipient_topic: continue
        try: units=int(str(log.get("data","0x0")),16)
        except ValueError: continue
        if units>=expected_units:
            matched=True; break
    if not matched:
        c.close(); raise HTTPException(409,"confirmed BSC transaction does not contain the required USDT amount to the seller wallet")
    now_value=now()
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE orders SET status='external_paid',external_tx_hash=?,external_paid_at=?,payment_reference=?,paid_at=? WHERE id=? AND status='awaiting_external_payment'",
              (tx_hash,now_value,"BSC:"+tx_hash,now_value,order_id))
    sale_revenue=int(o["buyer_fee_cents"] or 0)+int(o["seller_fee_cents"] or 0)
    award_referral_incentive(c,buyer["id"],"referral_sale",sale_revenue,order_id)
    if c.execute("SELECT changes()").fetchone()[0]!=1:
        c.rollback(); c.close(); raise HTTPException(409,"order payment was already recorded")
    audit(c,buyer["id"],"direct_external_payment","order",order_id,"awaiting_external_payment","external_paid",request)
    c.commit(); c.close()
    return {"order_id":order_id,"status":"external_paid","asset":"USDT","network":"BEP20",
            "recipient_address":recipient["address"],"amount_usdt":expected_units/1000000,
            "tx_hash":tx_hash,"custody":"none","message":"Payment verified on BSC. NAQAA did not custody the funds."}

@app.get("/api/v1/orders/{order_id}")
def get_order(order_id:str,token:str):
    c=db(); a=account_for(c,token); o=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: c.close(); raise HTTPException(404,"order not found")
    if a["id"] not in (o["buyer_id"],o["seller_id"]): c.close(); raise HTTPException(403,"order access denied")
    data=dict(o); data["counterparty"]="BUYER-PRIVATE" if a["id"]==o["seller_id"] else "SELLER-PRIVATE"
    data.pop("buyer_id",None); data.pop("seller_id",None); c.close(); return data

@app.post("/api/v1/contracts/{offer_id}")
def contract(offer_id:str,token:str):
    c=db(); actor=account_for(c,token)
    r=c.execute("""SELECT o.*,l.seller_id,l.status listing_status
                   FROM offers o JOIN listings l ON l.id=o.listing_id
                   WHERE o.id=? AND o.status='accepted'""",(offer_id,)).fetchone()
    if not r: c.close(); raise HTTPException(400,"accepted offer required")
    if actor["id"] not in (r["buyer_id"],r["seller_id"]):
        c.close(); raise HTTPException(403,"contract access denied")
    existing=c.execute("SELECT id,contract_hash,status FROM contracts WHERE offer_id=? ORDER BY created_at DESC LIMIT 1",(offer_id,)).fetchone()
    if existing:
        c.close(); return {"contract_id":existing["id"],"sha256":existing["contract_hash"],"status":existing["status"],"duplicate":True}
    cid="NQ-EC-"+uuid.uuid4().hex[:10].upper(); h=hashlib.sha256(f"{cid}|{offer_id}|{r['amount']}|{r['currency']}".encode()).hexdigest()
    c.execute("INSERT INTO contracts VALUES(?,?,?,?,?)",(cid,offer_id,h,"awaiting_signatures",now())); c.commit(); c.close()
    return {"contract_id":cid,"sha256":h,"status":"awaiting_signatures"}
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

@app.get("/wallet", include_in_schema=False)
def wallet_screen():
    return FileResponse(Path(__file__).resolve().parent / "web" / "crypto-wallet.html", media_type="text/html")

app.include_router(crypto_router)

# Serve the complete NAQAA Market frontend from the same FastAPI origin.
# This makes one public URL serve HTML, CSS, JavaScript, assets, and /api/v1/* together.
app.mount("/", StaticFiles(directory="static", html=True), name="naqaa-static")
