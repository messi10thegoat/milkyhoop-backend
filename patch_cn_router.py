"""CN router Law 25: hapus pembulatan int() pada nominal; Decimal quantize 2dp sepanjang pipa (DB numeric)."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/credit_notes.py"
t = io.open(P, encoding="utf-8").read()


def rep(old, new, n):
    c = t.count(old)
    if c != n:
        print(f"GAGAL ({c}!={n}): {old[:60]!r}"); sys.exit(1)
    return t.replace(old, new)


# import + helper _q2
t = rep("from decimal import Decimal\n", "from decimal import Decimal, ROUND_HALF_UP\n", 1)
t = rep("def calculate_item_totals(",
        'def _q2(v) -> Decimal:\n'
        '    """Bulatkan nominal ke 2 desimal (Decimal) untuk DB numeric(18,2); presisi dijaga sepanjang pipa."""\n'
        '    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n\n\n'
        'def calculate_item_totals(', 1)

# calc return: int()->_q2()
t = rep('        "subtotal": int(subtotal),\n        "discount_amount": int(discount),\n'
        '        "tax_amount": int(tax_amount),\n        "total": int(total),\n',
        '        "subtotal": _q2(subtotal),\n        "discount_amount": _q2(discount),\n'
        '        "tax_amount": _q2(tax_amount),\n        "total": _q2(total),\n', 1)

# get_invoice_remaining: -> int + return int() -> Decimal
t = rep("async def get_invoice_remaining_from_journal(conn, tenant_id: str, invoice_id) -> int:",
        "async def get_invoice_remaining_from_journal(conn, tenant_id: str, invoice_id) -> Decimal:", 1)
t = rep("    return int(result or 0)\n",
        "    return result if result is not None else Decimal(0)\n", 1)

# list aggregates int()->float()
t = rep('                    "total_value": int(row["total_value"] or 0),\n'
        '                    "total_applied": int(row["total_applied"] or 0),\n'
        '                    "total_refunded": int(row["total_refunded"] or 0),\n'
        '                    "available_balance": int(row["available_balance"] or 0),\n',
        '                    "total_value": float(row["total_value"] or 0),\n'
        '                    "total_applied": float(row["total_applied"] or 0),\n'
        '                    "total_refunded": float(row["total_refunded"] or 0),\n'
        '                    "available_balance": float(row["available_balance"] or 0),\n', 1)

# overall discount/tax create (622/631) + update (802/811): int(...)->_q2(...)
t = rep('                    overall_discount = int(\n'
        '                        subtotal * Decimal(str(body.discount_percent)) / 100\n'
        '                    )\n',
        '                    overall_discount = _q2(\n'
        '                        subtotal * Decimal(str(body.discount_percent)) / 100\n'
        '                    )\n', 1)
t = rep('                    overall_tax = int(\n'
        '                        after_discount * Decimal(str(body.tax_rate)) / 100\n'
        '                    )\n',
        '                    overall_tax = _q2(\n'
        '                        after_discount * Decimal(str(body.tax_rate)) / 100\n'
        '                    )\n', 1)
t = rep('                        overall_discount = int(\n'
        '                            subtotal * Decimal(str(discount_percent)) / 100\n'
        '                        )\n',
        '                        overall_discount = _q2(\n'
        '                            subtotal * Decimal(str(discount_percent)) / 100\n'
        '                        )\n', 1)
t = rep('                        overall_tax = int(after_discount * Decimal(str(tax_rate)) / 100)\n',
        '                        overall_tax = _q2(after_discount * Decimal(str(tax_rate)) / 100)\n', 1)

io.open(P, "w", encoding="utf-8").write(t)
print("OK CN router: int()->_q2/Decimal/float, helper _q2 ditambah")
