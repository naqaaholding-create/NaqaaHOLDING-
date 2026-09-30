import os, sqlite3, hashlib, secrets, uuid, json, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from pydantic import BaseModel, Field

def _now():
    return datetime.now(timezone.utc)

def _iso():
    return _now().isoformat()

def _db(app_module):
    return app_module.db()

def _send_resend(to_email: str, code: str):
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "email verification service is not configured")
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()
    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": "NAQAA Market — رمز التحقق من البريد الإلكتروني",
        "html": f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;line-height:1.7">
          <h2 style="color:#0b3b2d">NAQAA Market</h2>
          <p>رمز التحقق الخاص بحسابك هو:</p>
          <div style="font-size:34px;font-weight:800;letter-spacing:8px;text-align:center;padding:18px;background:#f2f7f4;border-radius:12px">{code}</div>
          <p>صلاحية الرمز 10 دقائق. لا تشارك هذا الرمز مع أي شخص.</p>
          <p style="color:#777;font-size:12px">هذه رسالة آلية من سوق نقاء.</p>
        </div>
        """,
        "text": f"NAQAA Market — رمز التحقق: {code}\nصلاحية الرمز 10 دقائق. لا تشاركه مع أي شخص."
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise HTTPException(502, "email provider rejected the message")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise HTTPException(502, "email provider error")
    except urllib.error.URLError:
        raise HTTPException(502, "email provider is temporarily unavailable")

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()

class VerifiedRegister(BaseModel):
    role: str = Field(pattern="^(seller|buyer)$")
    name: str
    email: str
    password: str = Field(min_length=8)
    account_type: str = Field(default="individual", pattern="^(individual|company)$")
    dob: str = ""
    nationality: str = ""
    phone: str = ""
    identity_type: str = Field(default="passport", pattern="^(passport|national_id)$")
    identity_number: str = ""
    identity_country: str = ""
    company_name: str = ""
    company_registration: str = ""

class VerifyEmail(BaseModel):
    email: str
    code: str = Field(min_length=6, max_length=6)

class ResendVerification(BaseModel):
    email: str

def install(app, app_module):
    def init_verification_table():
        c = app_module.db()
        c.execute("""CREATE TABLE IF NOT EXISTS email_verifications(
            id TEXT PRIMARY KEY,
            account_id TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at TEXT NOT NULL,
            verified_at TEXT
        )""")
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN email_verified_at TEXT")
        except sqlite3.OperationalError:
            pass
        c.commit()
        c.close()
    init_verification_table()

    def issue_code(c, account_id, email):
        last = c.execute("SELECT last_sent_at FROM email_verifications WHERE account_id=?", (account_id,)).fetchone()
        if last and last["last_sent_at"]:
            try:
                age = (_now() - datetime.fromisoformat(last["last_sent_at"])).total_seconds()
                if age < 60:
                    raise HTTPException(429, "please wait before requesting another verification code")
            except ValueError:
                pass
        code = f"{secrets.randbelow(1000000):06d}"
        c.execute("""INSERT INTO email_verifications(id,account_id,email,code_hash,expires_at,attempts,last_sent_at,verified_at)
                     VALUES(?,?,?,?,?,?,?,NULL)
                     ON CONFLICT(account_id) DO UPDATE SET
                       email=excluded.email,code_hash=excluded.code_hash,expires_at=excluded.expires_at,
                       attempts=0,last_sent_at=excluded.last_sent_at,verified_at=NULL""",
                  (str(uuid.uuid4()),account_id,email,_hash_code(code),(_now()+timedelta(minutes=10)).isoformat(),0,_iso()))
        _send_resend(email, code)

    @app.post("/api/v1/auth/register-verified")
    def register_verified(x: VerifiedRegister):
        email = x.email.strip().lower()
        c = app_module.db()
        if c.execute("SELECT id FROM accounts WHERE lower(email)=?", (email,)).fetchone():
            c.close()
            raise HTTPException(409, "email already registered")
        account_id = str(uuid.uuid4())
        last4 = x.identity_number.replace(" ","")[-4:] if x.identity_number else ""
        c.execute("""INSERT INTO accounts(id,role,name,email,status,created_at,password_hash,account_type,dob,nationality,phone,identity_type,identity_last4,identity_country,company_name,company_registration,email_verified_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                  (account_id,x.role,x.name.strip(),email,"pending",app_module.now(),app_module.hp(x.password),x.account_type,
                   x.dob,x.nationality.strip(),x.phone.strip(),x.identity_type,last4,x.identity_country.strip(),
                   x.company_name.strip(),x.company_registration.strip()))
        wallet_id = str(uuid.uuid4())
        c.execute("INSERT INTO wallets(id,account_id,currency,balance,updated_at,balance_cents,held_cents) VALUES(?,?,?,?,?,?,?)",
                  (wallet_id,account_id,"USD",0,app_module.now(),0,0))
        app_module.ensure_wallet_ledger(c,account_id,"USD",wallet_id)
        try:
            issue_code(c, account_id, email)
            c.commit()
        except Exception:
            c.rollback()
            c.execute("DELETE FROM wallets WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            c.commit()
            c.close()
            raise
        c.close()
        return {"status":"verification_required","email":email,"message":"Verification code sent to your email."}

    @app.post("/api/v1/auth/verify-email")
    def verify_email(x: VerifyEmail):
        email = x.email.strip().lower()
        code = x.code.strip()
        c = app_module.db()
        row = c.execute("""SELECT v.*,a.id account_id,a.status account_status
                           FROM email_verifications v JOIN accounts a ON a.id=v.account_id
                           WHERE lower(v.email)=?""", (email,)).fetchone()
        if not row:
            c.close()
            raise HTTPException(404, "verification request not found")
        if row["verified_at"]:
            c.close()
            raise HTTPException(409, "email is already verified")
        if datetime.fromisoformat(row["expires_at"]) < _now():
            c.close()
            raise HTTPException(410, "verification code expired")
        if int(row["attempts"]) >= 5:
            c.close()
            raise HTTPException(429, "too many verification attempts; request a new code")
        c.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE id=?", (row["id"],))
        if not secrets.compare_digest(_hash_code(code), row["code_hash"]):
            c.commit()
            c.close()
            raise HTTPException(400, "invalid verification code")
        verified_at = _iso()
        c.execute("UPDATE email_verifications SET verified_at=? WHERE id=?", (verified_at,row["id"]))
        c.execute("UPDATE accounts SET status='active',email_verified_at=? WHERE id=?", (verified_at,row["account_id"]))
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions VALUES(?,?,?)", (token,row["account_id"],app_module.now()))
        a = c.execute("SELECT * FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
        c.commit()
        c.close()
        return {"status":"verified","token":token,"user":app_module.account_public(a)}

    @app.post("/api/v1/auth/resend-verification")
    def resend_verification(x: ResendVerification):
        email = x.email.strip().lower()
        c = app_module.db()
        a = c.execute("SELECT id,status,email_verified_at FROM accounts WHERE lower(email)=?", (email,)).fetchone()
        if not a:
            c.close()
            raise HTTPException(404, "account not found")
        if a["email_verified_at"] or a["status"] == "active":
            c.close()
            raise HTTPException(409, "email is already verified")
        try:
            issue_code(c, a["id"], email)
            c.commit()
        finally:
            c.close()
        return {"status":"sent","message":"A new verification code was sent."}
