import os, tempfile, importlib
from fastapi.testclient import TestClient

def test_company_structure_and_permissions():
    db_path=tempfile.mktemp(suffix=".db")
    os.environ["DATABASE_PATH"]=db_path
    os.environ["NAQAA_ADMIN_KEY"]="ci-company-admin"
    os.environ["REAL_MONEY_ENABLED"]="0"
    import app
    importlib.reload(app)
    c=TestClient(app.app)

    r=c.post("/api/v1/auth/register",json={
        "role":"buyer","name":"Director Candidate","email":"director@test.local","password":"StrongPass123",
        "account_type":"individual","identity_type":"passport","identity_number":"AA123456"})
    assert r.status_code==200
    login=c.post("/api/v1/auth/login",json={"email":"director@test.local","password":"StrongPass123"})
    token=login.json()["token"]; aid=login.json()["user"]["id"]

    promoted=c.post(f"/api/v1/admin/accounts/{aid}/role",json={"role":"company_director"},headers={"X-Admin-Key":"ci-company-admin"})
    assert promoted.status_code==200, promoted.text

    me=c.get(f"/api/v1/auth/me?token={token}")
    assert me.status_code==200
    assert me.json()["permissions"]["company_admin"] is True
    assert me.json()["permissions"]["financial_admin"] is False

    org=c.get(f"/api/v1/company?token={token}")
    assert org.status_code==200, org.text
    assert len(org.json()["departments"]) == 4

    dept=c.post(f"/api/v1/company/departments?token={token}",json={"name":"اختبار التقنية","code":"QA","description":"قسم اختبار"})
    assert dept.status_code==200, dept.text
    dept_id=dept.json()["id"]

    r2=c.post("/api/v1/auth/register",json={
        "role":"buyer","name":"Employee Candidate","email":"employee@test.local","password":"StrongPass123",
        "account_type":"individual","identity_type":"passport","identity_number":"BB123456"})
    assert r2.status_code==200

    emp=c.post(f"/api/v1/company/employees?token={token}",json={"account_email":"employee@test.local","department_id":dept_id,"job_title":"موظف اختبار"})
    assert emp.status_code==200, emp.text
    employee_id=emp.json()["id"]

    role=c.post(f"/api/v1/company/employees/{employee_id}/role?token={token}",json={"role":"listing_manager"})
    assert role.status_code==200
    status=c.post(f"/api/v1/company/employees/{employee_id}/status?status=suspended&token={token}")
    assert status.status_code==200

    org2=c.get(f"/api/v1/company?token={token}").json()
    row=next(x for x in org2["employees"] if x["id"]==employee_id)
    assert row["role"]=="listing_manager"
    assert row["employment_status"]=="suspended"

    # Financial administrator remains separate from company director.
    assert org2["roles"]
