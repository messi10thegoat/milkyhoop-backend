"""
Laporan Laba Rugi (Income Statement) — PSAK by nature.
All data from journal_lines (Law 1).

Sign convention:
  - Revenue (credit-normal): raw = negative → negate for positive display
  - COGS, Expense (debit-normal): raw = positive → display as-is
  - Other Income (credit-normal): raw = negative → negate
  - Other Expense (debit-normal): raw = positive → display as-is
"""
from decimal import Decimal
from .ledger import compute_balance, compute_balance_detail


def _make_section(total: Decimal, detail: list, negate: bool = False) -> dict:
    """
    Build a section dict with total and per-account breakdown.
    negate=True for credit-normal types (revenue, other_income) → flip sign.
    """
    if negate:
        display_total = float(-total)
        akun = [
            {
                "account_code": d["account_code"],
                "account_name": d["account_name"],
                "balance": float(-d["balance"]),
            }
            for d in detail
        ]
    else:
        display_total = float(total)
        akun = [
            {
                "account_code": d["account_code"],
                "account_name": d["account_name"],
                "balance": float(d["balance"]),
            }
            for d in detail
        ]
    return {"total": display_total, "akun": akun}


async def generate_income_statement(conn, tenant_id: str, start: str, end: str, basis: str = "accrual") -> dict:
    """Generate P&L for period start..end. All amounts derive from journal_lines."""

    cash_filter = {"cash_only": True} if basis == "cash" else {}

    async def b(fp):
        return await compute_balance(conn, tenant_id, fp, start_date=start, end_date=end)

    async def bd(fp):
        return await compute_balance_detail(conn, tenant_id, fp, start_date=start, end_date=end)

    # Revenue — credit normal → raw is negative → negate for display
    revenue_raw = await b({"account_type": "REVENUE", **cash_filter})
    revenue_detail = await bd({"account_type": "REVENUE", **cash_filter})
    revenue = -revenue_raw

    # COGS — debit normal → raw is positive
    cogs_raw = await b({"account_type": "COGS", **cash_filter})
    cogs_detail = await bd({"account_type": "COGS", **cash_filter})

    gross = revenue - cogs_raw

    # Operating expenses — debit normal → raw is positive
    opex_raw = await b({"account_type": "EXPENSE", **cash_filter})
    opex_detail = await bd({"account_type": "EXPENSE", **cash_filter})

    op_profit = gross - opex_raw

    # Other income — credit normal → negate
    other_inc_raw = await b({"account_type": "OTHER_INCOME", **cash_filter})
    other_inc_detail = await bd({"account_type": "OTHER_INCOME", **cash_filter})
    other_inc = -other_inc_raw

    # Other expense — debit normal
    other_exp_raw = await b({"account_type": "OTHER_EXPENSE", **cash_filter})
    other_exp_detail = await bd({"account_type": "OTHER_EXPENSE", **cash_filter})

    pbt = op_profit + other_inc - other_exp_raw

    # Net income (no separate tax account type in current CoA)
    net = pbt

    return {
        "basis_applied": basis,
        "period": {"start": start, "end": end},
        "pendapatan": _make_section(revenue_raw, revenue_detail, negate=True),
        "hpp": _make_section(cogs_raw, cogs_detail, negate=False),
        "laba_kotor": float(gross),
        "beban_usaha": _make_section(opex_raw, opex_detail, negate=False),
        "laba_usaha": float(op_profit),
        "pendapatan_lain": _make_section(other_inc_raw, other_inc_detail, negate=True),
        "beban_lain": _make_section(other_exp_raw, other_exp_detail, negate=False),
        "laba_sebelum_pajak": float(pbt),
        "beban_pajak": {"total": 0.0, "akun": []},
        "laba_bersih": float(net),
    }
