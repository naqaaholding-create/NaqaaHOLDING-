from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_wallet_screen_serves_html():
    response = client.get("/wallet")
    assert response.status_code == 200
    assert "محفظتي الرقمية" in response.text


def test_wallet_query_token_is_not_used():
    response = client.get("/wallet?token=should-not-authenticate")
    assert response.status_code == 200
    assert "محفظتي الرقمية" in response.text
