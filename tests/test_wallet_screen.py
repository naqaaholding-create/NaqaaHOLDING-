from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_wallet_screen_serves_html():
    response = client.get("/wallet")
    assert response.status_code == 200
    assert "المحفظة" in response.text
    assert "NAQAA MARKET · WALLET" in response.text


def test_wallet_query_token_is_not_used():
    response = client.get("/wallet?token=should-not-authenticate")
    assert response.status_code == 200
    assert "المحفظة" in response.text
    # The wallet screen must not authenticate a session from a query-string token.
    me = client.get("/api/v1/auth/me", params={"token": "should-not-authenticate"})
    assert me.status_code == 401
