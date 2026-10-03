"""NAQAA Market revenue/pricing catalog.

These are the prices already used by the current marketplace UI.  This module
is a single source of truth for displaying and validating the commercial plan.
It does not enable real-money collection.
"""

PRICING_VERSION = "2026-10"
CURRENCY = "USD"

SELLER_SUBSCRIPTION_CENTS = 1000
BUYER_SUBSCRIPTION_CENTS = 300
LISTING_FEE_CENTS = 200
CONTACT_UNLOCK_FEE_CENTS = 100

REVENUE_SOURCES = (
    {
        "code": "subscription",
        "label": "الاشتراكات",
        "items": (
            {"code": "seller_monthly", "label": "اشتراك البائع الشهري", "amount_cents": SELLER_SUBSCRIPTION_CENTS},
            {"code": "buyer_monthly", "label": "اشتراك المشتري الشهري", "amount_cents": BUYER_SUBSCRIPTION_CENTS},
        ),
    },
    {
        "code": "advertising",
        "label": "الإعلانات",
        "items": (
            {"code": "listing", "label": "نشر إعلان", "amount_cents": LISTING_FEE_CENTS},
            {"code": "contact_unlock", "label": "فتح بيانات التواصل", "amount_cents": CONTACT_UNLOCK_FEE_CENTS},
        ),
    },
    {
        "code": "protected_sales_commission",
        "label": "عمولة البيع المحمي",
        "items": (),
        "note": "تُحسب فقط عند اختيار البائع والمشتري التسوية المحمية داخل نقاء؛ النسبة تُدار عبر commission_rules ولا تُخزّن كنسبة ثابتة هنا.",
    },
)

def pricing_catalog():
    return {
        "version": PRICING_VERSION,
        "currency": CURRENCY,
        "real_money_collection": False,
        "revenue_sources": REVENUE_SOURCES,
    }
