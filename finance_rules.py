from decimal import Decimal, ROUND_HALF_UP

CENT=Decimal("0.01")

def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)

def fee_amount(gross, rate_percent="0", fixed="0", minimum="0", maximum=None):
    gross=money(gross); rate=Decimal(str(rate_percent))/Decimal(100)
    fee=money(gross*rate+Decimal(str(fixed)))
    fee=max(fee,money(minimum))
    if maximum is not None:
        fee=min(fee,money(maximum))
    return fee

def split_settlement(gross,buyer_rate="0",seller_rate="0",buyer_fixed="0",seller_fixed="0"):
    gross=money(gross)
    buyer_fee=fee_amount(gross,buyer_rate,buyer_fixed)
    seller_fee=fee_amount(gross,seller_rate,seller_fixed)
    seller_net=gross-seller_fee
    return {"gross":gross,"buyer_fee":buyer_fee,"seller_fee":seller_fee,"seller_net":seller_net,"total_fees":buyer_fee+seller_fee}
