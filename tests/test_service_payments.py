import os
from fastapi.testclient import TestClient
import app

def test_service_payment_uses_editable_price_and_is_idempotent(tmp_path):
    db_path=tmp_path/"service.db"
    app.DB=str(db_path)
    os.environ["DATABASE_PATH"]=str(db_path)
    app.init_db()
    c=app.db()
    seller_id="seller-test"
    c.execute("INSERT INTO accounts(id,role,name,email,status,created_at,password_hash) VALUES(?,?,?,?,?,?,?)",(seller_id,"seller","Seller","seller-service@example.test","active",app.now(),"x"))
    app.ensure_wallet_ledger(c,seller_id,"USD",None)
    w=c.execute("SELECT * FROM wallets WHERE account_id=?",(seller_id,)).fetchone()
    app.set_wallet_balance(c,w["id"],1000,0)
    c.execute("UPDATE pricing_settings SET amount_cents=275 WHERE code='listing'")
    c.commit(); c.close()
    client=TestClient(app.app)
    token=cookie_token(seller_id)
    headers={"Idempotency-Key":"svc-1"}
    r=client.post("/api/v1/services/pay?token="+token,headers=headers,json={"code":"listing","description":"listing fee"})
    assert r.status_code==200
    assert r.json()["amount"]==2.75
    r2=client.post("/api/v1/services/pay?token="+token,headers=headers,json={"code":"listing","description":"listing fee"})
    assert r2.status_code==200 and r2.json()["duplicate"] is True

def cookie_token(account_id):
    c=app.db()
    t="test-token-"+account_id
    c.execute("INSERT INTO sessions(token,account_id,created_at) VALUES(?,?,?)",(t,account_id,app.now()))
    c.commit(); c.close()
    return t
