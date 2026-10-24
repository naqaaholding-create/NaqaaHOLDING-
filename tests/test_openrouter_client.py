import pytest

import openrouter_client as client


def test_requires_server_side_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "example/model")
    with pytest.raises(client.OpenRouterConfigurationError):
        client.chat_completion([{"role": "user", "content": "hello"}])


def test_requires_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    with pytest.raises(client.OpenRouterConfigurationError):
        client.chat_completion([{"role": "user", "content": "hello"}])


def test_rejects_invalid_messages(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setenv("OPENROUTER_MODEL", "example/model")
    with pytest.raises(ValueError):
        client.chat_completion([{"role": "system", "content": 123}])


def test_sends_server_key_and_returns_text(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setenv("OPENROUTER_MODEL", "example/model")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "  test reply  "}}]}

    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)
    result = client.chat_completion([{"role": "user", "content": "hello"}])

    assert result == "test reply"
    assert captured["url"] == client.OPENROUTER_URL
    assert captured["headers"]["Authorization"] == "Bearer test-only"
    assert captured["json"]["model"] == "example/model"
