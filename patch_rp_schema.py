"""L1 receive_payments schema (Law 25, pola CN): REQUEST nominal int->Decimal; RESPONSE int->float
(tetap angka JSON). count (invoice_count/overdue_days/total list) tetap int."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/schemas/receive_payments.py"
t = io.open(P, encoding="utf-8").read()
def rep(o, n, tag):
    global t
    if t.count(o) != 1:
        print(f"GAGAL {tag}: {t.count(o)}"); sys.exit(1)
    t = t.replace(o, n)

# import Decimal (setelah baris pydantic import)
if "from decimal import Decimal" not in t:
    rep("from pydantic import BaseModel, Field", "from decimal import Decimal\nfrom pydantic import BaseModel, Field", "import")

# REQUEST int->Decimal
rep('    amount_applied: int = Field(..., gt=0, description="Amount to apply in IDR")',
    '    amount_applied: Decimal = Field(..., gt=0, description="Amount to apply in IDR")', "amount_applied req")
rep('    total_amount: int = Field(..., gt=0, description="Total payment amount in IDR")',
    '    total_amount: Decimal = Field(..., gt=0, description="Total payment amount in IDR")', "total_amount req")
rep('    discount_amount: int = Field(0, ge=0, description="Early payment discount in IDR")',
    '    discount_amount: Decimal = Field(Decimal("0"), ge=0, description="Early payment discount in IDR")', "discount req")
rep('    total_amount: Optional[int] = Field(None, gt=0)',
    '    total_amount: Optional[Decimal] = Field(None, gt=0)', "total_amount upd")
rep('    discount_amount: Optional[int] = Field(None, ge=0)',
    '    discount_amount: Optional[Decimal] = Field(None, ge=0)', "discount upd")

# RESPONSE int->float (blok multi-medan unik)
rep('''    invoice_amount: int
    remaining_before: int
    amount_applied: int
    remaining_after: int''',
    '''    invoice_amount: float
    remaining_before: float
    amount_applied: float
    remaining_after: float''', "AllocationResponse")
rep('''    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    status: str
    invoice_count: int = 0''',
    '''    total_amount: float
    allocated_amount: float
    unapplied_amount: float
    status: str
    invoice_count: int = 0''', "ListItem")
rep('''    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    discount_amount: int
    discount_account_id: Optional[str] = None''',
    '''    total_amount: float
    allocated_amount: float
    unapplied_amount: float
    discount_amount: float
    discount_account_id: Optional[str] = None''', "Detail")
rep('''    total_amount: int
    paid_amount: int
    remaining_amount: int
    is_overdue: bool = False''',
    '''    total_amount: float
    paid_amount: float
    remaining_amount: float
    is_overdue: bool = False''', "OpenInvoiceItem")
rep('''    amount: int
    amount_applied: int
    amount_refunded: int
    remaining_amount: int''',
    '''    amount: float
    amount_applied: float
    amount_refunded: float
    remaining_amount: float''', "AvailableDepositItem")

io.open(P, "w", encoding="utf-8").write(t)
print("OK receive_payments schema: request Decimal, response float")
