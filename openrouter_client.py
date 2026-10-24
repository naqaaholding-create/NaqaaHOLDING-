"""Server-side OpenRouter client for NAQAA Market / NAQAA ATAA.

The API key is read only from the hosting environment. Never expose it to browser
JavaScript or commit a real key. This module deliberately provides a provider
adapter only; application routes must enforce the project's existing user and
role authorization before calling it.
"""
from __future__ import annotations

import os
from typing import Any, Sequence

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterConfigurationError(RuntimeError):
    """OpenRouter is not configured on this server."""


class OpenRouterRequestError(RuntimeError):
    """OpenRouter rejected or failed the request."""


def chat_completion(
    messages: Sequence[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 700,
    timeout_seconds: float = 30.0,
) -> str:
    """Return the assistant text; never include credentials in errors.

    Required environment variable: OPENROUTER_API_KEY.
    Optional: OPENROUTER_MODEL, OPENROUTER_SITE_URL, OPENROUTER_APP_NAME.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise OpenRouterConfigurationError("OPENROUTER_API_KEY is not configured")

    selected_model = (model or os.getenv("OPENROUTER_MODEL", "")).strip()
    if not selected_model:
        raise OpenRouterConfigurationError("OPENROUTER_MODEL is not configured")
    if not messages or any(
        not isinstance(item, dict)
        or item.get("role") not in {"system", "user", "assistant"}
        or not isinstance(item.get("content"), str)
        for item in messages
    ):
        raise ValueError("messages must contain role/content text objects")
    if not 1 <= max_tokens <= 4096:
        raise ValueError("max_tokens must be between 1 and 4096")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": os.getenv("OPENROUTER_APP_NAME", "NAQAA Market / ATAA"),
    }
    site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
    if site_url:
        headers["HTTP-Referer"] = site_url

    try:
        response = httpx.post(
            OPENROUTER_URL,
            headers=headers,
            json={
                "model": selected_model,
                "messages": list(messages),
                "max_tokens": max_tokens,
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload: Any = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterRequestError("OpenRouter returned an empty response")
        return content.strip()
    except OpenRouterRequestError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # Do not include response bodies or request headers; they may contain sensitive data.
        raise OpenRouterRequestError("OpenRouter request failed; check server logs and provider status") from exc
