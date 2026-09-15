"""CN schema Law 25: REQUEST nominal int->Decimal (presisi masuk); RESPONSE int->float
(tetap ANGKA JSON, bukan Decimal-string; kontrak FE tak berubah). quantity/percent/rate tetap float."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/schemas/credit_notes.py"
t = io.open(P, encoding="utf-8").read()


def rep(old, new, n):
    c = t.count(old)
    if c != n:
        print(f"GAGAL jangkar ({c}!={n}): {old[:60]}"); sys.exit(1)
    return t.replace(old, new)


# import Decimal
t = rep("from pydantic import BaseModel, Field, field_validator\n",
        "from pydantic import BaseModel, Field, field_validator\nfrom decimal import Decimal\n", 1)

# --- REQUEST: int -> Decimal ---
# unit_price (create + update)
t = rep('    unit_price: int = Field(..., ge=0, description="Price per unit in IDR")\n',
        '    unit_price: Decimal = Field(..., ge=0, description="Price per unit in IDR")\n', 1)
t = rep('    unit_price: Optional[int] = Field(None, ge=0)\n',
        '    unit_price: Optional[Decimal] = Field(None, ge=0)\n', 1)
# discount_amount item create
t = rep('    discount_amount: int = Field(0, ge=0)\n',
        '    discount_amount: Decimal = Field(0, ge=0)\n', 1)
# discount_amount item/cn update (Optional)  (muncul 2x: item update + cn update)
t = rep('    discount_amount: Optional[int] = Field(None, ge=0)\n',
        '    discount_amount: Optional[Decimal] = Field(None, ge=0)\n', 2)
# overall discount_amount create
t = rep('    discount_amount: int = Field(0, ge=0, description="Overall discount amount")\n',
        '    discount_amount: Decimal = Field(0, ge=0, description="Overall discount amount")\n', 1)
# apply amount
t = rep('    amount: int = Field(..., gt=0, description="Amount to apply in IDR")\n',
        '    amount: Decimal = Field(..., gt=0, description="Amount to apply in IDR")\n', 1)
# refund amount
t = rep('    amount: int = Field(..., gt=0, description="Refund amount in IDR")\n',
        '    amount: Decimal = Field(..., gt=0, description="Refund amount in IDR")\n', 1)

# --- RESPONSE: int -> float (tetap angka JSON) ---
# item response
t = rep('    unit_price: int\n    discount_percent: float = 0\n    discount_amount: int = 0\n'
        '    tax_code: Optional[str] = None\n    tax_rate: float = 0\n    tax_amount: int = 0\n'
        '    subtotal: int\n    total: int\n',
        '    unit_price: float\n    discount_percent: float = 0\n    discount_amount: float = 0\n'
        '    tax_code: Optional[str] = None\n    tax_rate: float = 0\n    tax_amount: float = 0\n'
        '    subtotal: float\n    total: float\n', 1)
# application response
t = rep('    amount_applied: int\n    application_date: str\n',
        '    amount_applied: float\n    application_date: str\n', 1)
# refund response
t = rep('    id: str\n    amount: int\n    refund_date: str\n',
        '    id: str\n    amount: float\n    refund_date: str\n', 1)
# list item
t = rep('    total_amount: int\n    amount_applied: int = 0\n    amount_refunded: int = 0\n'
        '    remaining_amount: int = 0\n    status: str\n    reason: str\n',
        '    total_amount: float\n    amount_applied: float = 0\n    amount_refunded: float = 0\n'
        '    remaining_amount: float = 0\n    status: str\n    reason: str\n', 1)
# detail
t = rep('    subtotal: int\n    discount_percent: float = 0\n    discount_amount: int = 0\n'
        '    tax_rate: float = 0\n    tax_amount: int = 0\n    total_amount: int\n'
        '    amount_applied: int = 0\n    amount_refunded: int = 0\n    remaining_amount: int = 0\n',
        '    subtotal: float\n    discount_percent: float = 0\n    discount_amount: float = 0\n'
        '    tax_rate: float = 0\n    tax_amount: float = 0\n    total_amount: float\n'
        '    amount_applied: float = 0\n    amount_refunded: float = 0\n    remaining_amount: float = 0\n', 1)

io.open(P, "w", encoding="utf-8").write(t)
print("OK CN schema: request int->Decimal, response int->float")
