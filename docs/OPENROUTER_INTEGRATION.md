# OpenRouter server integration

This repository contains the shared NAQAA Market / NAQAA ATAA 120980 design reference. The OpenRouter adapter is server-side only.

## Configuration
Set these values in the hosting provider's secret/environment settings, not in Git:
- `OPENROUTER_API_KEY`: newly generated OpenRouter key
- `OPENROUTER_MODEL`: exact model ID enabled in the OpenRouter account
- `OPENROUTER_SITE_URL`: optional public application URL for provider attribution
- `OPENROUTER_APP_NAME`: optional application label

## Calling the adapter
Backend Python code may call `chat_completion([{"role": "user", "content": "..."}])` from `openrouter_client.py`. The adapter returns assistant text and raises safe errors if configuration or the provider request fails.

## Security / status
- Never put the key in HTML, JavaScript, mobile app bundles, logs, or source control.
- Keep each project's authorization checks before calling the provider; do not expose an unauthenticated public proxy.
- Configure rate limits, request-size limits, and user quotas before exposing any AI endpoint.
- This commit adds the server-side provider adapter and configuration contract. It does not claim that a user-facing AI workflow is enabled until a specific authorized backend route calls the adapter and a new key is configured in the hosting environment.
