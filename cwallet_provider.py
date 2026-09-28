"""Cwallet provider adapter for NAQAA Market.

Provider-specific endpoint paths and authentication are intentionally configurable.
Do not enable live money until the exact Cwallet merchant/API contract is verified.
"""
import os
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

import httpx


class CwalletConfigurationError(RuntimeError):
    pass


class CwalletProvider:
    def __init__(self) -> None:
        self.base_url = os.getenv("CWALLET_API_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("CWALLET_API_KEY", "")
        self.api_secret = os.getenv("CWALLET_API_SECRET", "")
        self.payment_path = os.getenv("CWALLET_PAYMENT_PATH", "")
        self.payout_path = os.getenv("CWALLET_PAYOUT_PATH", "")
        self.webhook_secret = os.getenv("CWALLET_WEBHOOK_SECRET", "")
        self.enabled = os.getenv("CWALLET_ENABLED", "0") == "1"

    def _require(self, *values: str) -> None:
        if not self.enabled:
            raise CwalletConfigurationError("Cwallet integration is disabled")
        if not all(values):
            raise CwalletConfigurationError("Cwallet API configuration is incomplete")

    def _headers(self) -> Dict[str, str]:
        self._require(self.base_url, self.api_key, self.api_secret)
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require(self.base_url, self.api_key, self.api_secret, path)
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.base_url}/{path.lstrip('/')}",
                headers=self._headers(),
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Cwallet API error: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Cwallet returned a non-JSON response") from exc

    def create_payment(self, *, reference: str, amount: str, currency: str,
                       asset: str, network: str, callback_url: str,
                       metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a provider payment request.

        The payload keys are kept in one place so they can be mapped to the
        exact Cwallet merchant API contract after account/API access is enabled.
        """
        payload = {
            "reference": reference,
            "amount": amount,
            "currency": currency,
            "asset": asset,
            "network": network,
            "callback_url": callback_url,
            "metadata": metadata or {},
        }
        return self._post(self.payment_path, payload)

    def create_payout(self, *, reference: str, amount: str, asset: str,
                      network: str, destination: str,
                      metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "reference": reference,
            "amount": amount,
            "asset": asset,
            "network": network,
            "destination": destination,
            "metadata": metadata or {},
        }
        return self._post(self.payout_path, payload)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        if not self.webhook_secret or not signature:
            return False
        digest = hmac.new(
            self.webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    @staticmethod
    def parse_webhook(raw_body: bytes) -> Dict[str, Any]:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Cwallet webhook JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Cwallet webhook must be a JSON object")
        return payload
