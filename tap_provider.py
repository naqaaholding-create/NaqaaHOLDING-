import os
import httpx

class TapConfigurationError(RuntimeError):
    pass

class TapProvider:
    def __init__(self):
        self.enabled=os.getenv("TAP_ENABLED","0")=="1"
        self.base_url=os.getenv("TAP_API_BASE_URL","https://api.tap.company").rstrip("/")
        self.secret_key=os.getenv("TAP_SECRET_KEY","")
        self.merchant_id=os.getenv("TAP_MERCHANT_ID","")
        self.source_id=os.getenv("TAP_SOURCE_ID","src_all")
        self.currency=os.getenv("TAP_CURRENCY","USD").upper()
        self.marketplace_mode=os.getenv("TAP_MARKETPLACE_MODE","0")=="1"

    def require(self):
        if not self.enabled:
            raise TapConfigurationError("Tap integration is disabled")
        if not self.secret_key or not self.merchant_id:
            raise TapConfigurationError("Tap secret key and merchant ID are required")

    def post_charge(self, *, amount, reference, order_id, customer, post_url, redirect_url, destination_id=None, destination_amount=None):
        self.require()
        payload={
            "amount": float(amount),
            "currency": self.currency,
            "customer_initiated": True,
            "threeDSecure": True,
            "save_card": False,
            "description": "NAQAA Market payment",
            "metadata": {"naqa_order_id": order_id},
            "reference": {"transaction": reference, "order": order_id, "idempotent": reference},
            "customer": customer,
            "merchant": {"id": self.merchant_id},
            "source": {"id": self.source_id},
            "post": {"url": post_url},
            "redirect": {"url": redirect_url},
        }
        if self.marketplace_mode:
            if not destination_id:
                raise TapConfigurationError("seller Tap destination ID is required for marketplace mode")
            payload["destinations"]={"destination":[{"id":str(destination_id),"amount":float(destination_amount),"currency":self.currency}]}
        with httpx.Client(timeout=30.0) as client:
            r=client.post(f"{self.base_url}/v2/charges/",
                headers={"Authorization":f"Bearer {self.secret_key}","accept":"application/json","content-type":"application/json","lang_code":"ar"},
                json=payload)
        if r.status_code>=400:
            raise RuntimeError(f"Tap API error: HTTP {r.status_code}")
        return r.json()
