import importlib
import pytest
from fastapi.testclient import TestClient

@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "security.db"))
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "security-admin")
    monkeypatch.setenv("REAL_MONEY_ENABLED", "0")
    import app
    importlib.reload(app)
    return app, TestClient(app.app)

def register(client, role, email, account_type="individual"):
    r=client.post("/api/v1/auth/register",json={
        "role":role,"name":role.title(),"email":email,"password":"StrongPass123",
        "account_type":account_type,"identity_type":"passport","identity_number":"P12345678"
    })
    assert r.status_code==200, r.text
    token=client.post("/api/v1/auth/login",json={"email":email,"password":"StrongPass123"}).json()["token"]
    return r.json()["id"],token

def approve_kyc(app, uid):
    c=app.db(); import uuid
    c.execute("INSERT INTO kyc_cases(id,user_id,document_type,status,risk_level,created_at) VALUES(?,?,?,?,?,?)",
              (str(uuid.uuid4()),uid,"passport","approved","standard",app.now()))
    c.commit(); c.close()

def test_contract_requires_participant_and_is_idempotent(ctx):
    app,client=ctx
    seller,st=register(client,"seller","seller@contract.test")
    buyer,bt=register(client,"buyer","buyer@contract.test")
    outsider,ot=register(client,"buyer","outsider@contract.test")
    listing=client.post("/api/v1/listings",params={"token":st},
                        json={"seller_id":seller,"category":"test","title":"Item","amount":10,"currency":"USD"})
    lid=listing.json()["id"]
    assert client.post(f"/api/v1/listings/{lid}/publish",params={"token":st}).status_code==200
    offer=client.post("/api/v1/offers",params={"token":bt},
                      json={"listing_id":lid,"buyer_id":buyer,"amount":10,"currency":"USD"}).json()
    accepted=client.post(f"/api/v1/offers/{offer['id']}/accept",params={"token":st})
    assert accepted.status_code==200
    offer_id=offer["id"]
    denied=client.post(f"/api/v1/contracts/{offer_id}",params={"token":ot})
    assert denied.status_code==403
    made=client.post(f"/api/v1/contracts/{offer_id}",params={"token":bt})
    assert made.status_code==200
    duplicate=client.post(f"/api/v1/contracts/{offer_id}",params={"token":st})
    assert duplicate.status_code==200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["contract_id"]==made.json()["contract_id"]

def test_production_eligibility_matches_account_type(ctx):
    app,client=ctx
    individual,it=register(client,"buyer","individual@eligibility.test")
    company,ct=register(client,"buyer","company@eligibility.test","company")
    approve_kyc(app,individual)
    approve_kyc(app,company)
    assert client.get("/api/v1/compliance/status",params={"token":it}).json()["production_eligible"] is True
    assert client.get("/api/v1/compliance/status",params={"token":ct}).json()["production_eligible"] is False
    c=app.db(); import uuid
    c.execute("INSERT INTO kyb_cases(id,user_id,company_name,registration_number,status,risk_level,created_at) VALUES(?,?,?,?,?,?,?)",
              (str(uuid.uuid4()),company,"Test Co","REG-1","approved","standard",app.now()))
    c.commit(); c.close()
    assert client.get("/api/v1/compliance/status",params={"token":ct}).json()["production_eligible"] is True
