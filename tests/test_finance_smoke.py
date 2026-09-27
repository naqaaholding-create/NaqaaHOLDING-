import os, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "naqaa_finance_smoke.db")
os.environ["NAQAA_ADMIN_KEY"] = "ci-admin-key"
os.environ["REAL_MONEY_ENABLED"] = "0"

try:
    os.remove(os.environ["DATABASE_PATH"])
except FileNotFoundError:
    pass

from fastapi.testclient import TestClient
from app import app

c=TestClient(app)

def post(path, body, headers=None, expected=200):
    r=c.post(path,json=body,headers=headers or {})
    assert r.status_code==expected,(path,r.status_code,r.text)
    return r.json()

def get(path, expected=200):
    r=c.get(path)
    assert r.status_code==expected,(path,r.status_code,r.text)
    return r.json()

buyer=post("/api/v1/auth/register",{"role":"buyer","name":"Buyer Test","email":"buyer@test.local","password":"StrongPass123","dob":"1990-01-01","nationality":"Test","phone":"+100000000","identity_type":"passport","identity_number":"P1234567","identity_country":"TEST"})
seller=post("/api/v1/auth/register",{"role":"seller","name":"Seller Test","email":"seller@test.local","password":"StrongPass123","dob":"1990-01-01","nationality":"Test","phone":"+100000001","identity_type":"passport","identity_number":"P7654321","identity_country":"TEST"})

lb=post("/api/v1/auth/login",{"email":"buyer@test.local","password":"StrongPass123"})
ls=post("/api/v1/auth/login",{"email":"seller@test.local","password":"StrongPass123"})
bh={"Authorization":"Bearer "+lb["token"]}
sh={"Authorization":"Bearer "+ls["token"]}

# Current API authenticates by token query parameter.
dep=post("/api/v1/wallet/deposit-request?token="+lb["token"],{"account_id":buyer["id"],"amount":1000,"currency":"USD"},{"Idempotency-Key":"dep-1"})
assert dep["status"]=="pending"

# Pending deposits must not create spendable funds.
r=c.post("/api/v1/wallet/withdraw-request?token="+lb["token"],json={"account_id":buyer["id"],"amount":1,"currency":"USD"},headers={"Idempotency-Key":"wd-before"})
assert r.status_code==400

approved=post("/api/v1/admin/wallet-requests/"+dep["request_id"]+"/approve",{},{"X-Admin-Key":"ci-admin-key"})
assert approved["status"]=="approved"
w=get("/api/v1/wallet/"+buyer["id"]+"?token="+lb["token"])
assert w["balance_cents"]==100000 and w["available_cents"]==100000

# 10,000 cannot be withdrawn from 1,000.
r=c.post("/api/v1/wallet/withdraw-request?token="+lb["token"],json={"account_id":buyer["id"],"amount":10000,"currency":"USD"},headers={"Idempotency-Key":"wd-too-big"})
assert r.status_code==400

wd=post("/api/v1/wallet/withdraw-request?token="+lb["token"],{"account_id":buyer["id"],"amount":1000,"currency":"USD"},{"Idempotency-Key":"wd-1000"})
w=get("/api/v1/wallet/"+buyer["id"]+"?token="+lb["token"])
assert w["balance_cents"]==100000 and w["held_cents"]==100000 and w["available_cents"]==0

post("/api/v1/admin/wallet-requests/"+wd["request_id"]+"/reject",{},{"X-Admin-Key":"ci-admin-key"})
w=get("/api/v1/wallet/"+buyer["id"]+"?token="+lb["token"])
assert w["available_cents"]==100000 and w["held_cents"]==0

# Transfer is atomic and idempotent.
pay=post("/api/v1/wallet/pay?token="+lb["token"],{"from_account_id":buyer["id"],"to_account_id":seller["id"],"amount":250,"currency":"USD","description":"test"},{"Idempotency-Key":"pay-1"})
pay2=post("/api/v1/wallet/pay?token="+lb["token"],{"from_account_id":buyer["id"],"to_account_id":seller["id"],"amount":250,"currency":"USD","description":"test"},{"Idempotency-Key":"pay-1"})
assert pay2["reference"]==pay["reference"] and pay2["duplicate"] is True

wb=get("/api/v1/wallet/"+buyer["id"]+"?token="+lb["token"])
ws=get("/api/v1/wallet/"+seller["id"]+"?token="+ls["token"])
assert wb["balance_cents"]==75000 and ws["balance_cents"]==25000

rec=get("/api/v1/finance/reconciliation")
assert rec["ok"],rec
print("NAQAA finance smoke test passed")
