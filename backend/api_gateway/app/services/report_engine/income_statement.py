"""
Laporan Laba Rugi (Income Statement) — PSAK by nature.
All data from journal_lines (Law 1).

Sign convention:
  - Revenue (credit-normal): raw = negative → negate for positive display
  - COGS, Expense (debit-normal): raw = positive → display as-is
  - Other Income (credit-normal): raw = negative → negate
  - Other Expense (debit-normal): raw = positive → display as-is

Gelombang 4 L1 (10 Nov 2026): hitungan SELURUHNYA Decimal (hitung_laba_rugi; Law 9/25) -- medan lama float dibentuk di batas keluaran
(_make_section / float()), nilainya tetap; + baris[] tampil server (report_engine.baris, SATU peta label).
"""
from decimal import Decimal
from . import baris as B
from .ledger import _to_date, compute_balance, compute_balance_detail


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


async def hitung_laba_rugi(conn, tenant_id: str, start: str, end: str, basis: str = "accrual") -> dict:
    """Model Decimal Laba Rugi start..end (dipakai generate_income_statement DAN neraca untuk laba periode berjalan)."""
    async def b(fp):
        return await compute_balance(conn, tenant_id, fp, start_date=start, end_date=end)

    async def bd(fp):
        return await compute_balance_detail(conn, tenant_id, fp, start_date=start, end_date=end)

    kas = None
    if basis == "cash":  # laporan-fix-2: basis kas = pendapatan + HPP faktur proporsional saat DIBAYAR, beban saat dibayar (SATU sumber dgn dasbor)
        from . import basis_kas
        kas = await basis_kas.detail(conn, tenant_id, _to_date(start), _to_date(end))
    m = {}
    for k, tipe in (("revenue", "REVENUE"), ("cogs", "COGS"), ("opex", "EXPENSE"), ("other_inc", "OTHER_INCOME"),
                    ("other_exp", "OTHER_EXPENSE")):
        if kas is not None:
            m[k + "_detail"] = kas[tipe]
            m[k + "_raw"] = sum((z["balance"] for z in kas[tipe]), Decimal("0"))
        else:
            m[k + "_raw"] = await b({"account_type": tipe})
            m[k + "_detail"] = await bd({"account_type": tipe})
    m["revenue"] = -m["revenue_raw"]                      # credit normal -> negate
    m["gross"] = m["revenue"] - m["cogs_raw"]
    m["op_profit"] = m["gross"] - m["opex_raw"]
    m["other_inc"] = -m["other_inc_raw"]
    m["other_net"] = m["other_inc"] - m["other_exp_raw"]
    m["pbt"] = m["op_profit"] + m["other_inc"] - m["other_exp_raw"]
    m["net"] = m["pbt"]                                   # no separate tax account type in current CoA
    m.update({"basis": basis, "start": start, "end": end})
    return m


def baris_laba_rugi(m: dict) -> list:
    """Urutan tampil PSAK (== layar Laba Rugi desktop sebelum L1)."""
    r = [B.judul("pendapatan")] + [B.akun(d, 1, negasi=True) for d in m["revenue_detail"]]
    r.append(B.angka("total", "total_pendapatan", m["revenue"]))
    r += [B.judul("hpp")] + [B.akun(d, 1) for d in m["cogs_detail"]]
    r.append(B.angka("total", "total_hpp", m["cogs_raw"]))
    r.append(B.angka("hasil", "laba_kotor", m["gross"]))
    r += [B.judul("beban_usaha")] + [B.akun(d, 1) for d in m["opex_detail"]]
    r.append(B.angka("total", "total_beban_usaha", m["opex_raw"]))
    r.append(B.angka("hasil", "laba_usaha", m["op_profit"]))
    r += ([B.judul("lain")] + [B.akun(d, 1, negasi=True) for d in m["other_inc_detail"]]
          + [B.akun(d, 1, negasi=True) for d in m["other_exp_detail"]])  # beban lain = mengurangi (nilai sajian negatif)
    r.append(B.angka("total", "total_lain", m["other_net"]))
    r.append(B.angka("hasil", "laba_sebelum_pajak", m["pbt"]))
    r.append(B.angka("info", "beban_pajak", Decimal("0")))
    r.append(B.angka("hasil", "laba_bersih", m["net"]))
    return r


async def generate_income_statement(conn, tenant_id: str, start: str, end: str, basis: str = "accrual") -> dict:
    """Generate P&L for period start..end. All amounts derive from journal_lines."""
    m = await hitung_laba_rugi(conn, tenant_id, start, end, basis)
    return {
        "basis_applied": basis,
        "period": {"start": start, "end": end},
        "pendapatan": _make_section(m["revenue_raw"], m["revenue_detail"], negate=True),
        "hpp": _make_section(m["cogs_raw"], m["cogs_detail"], negate=False),
        "laba_kotor": float(m["gross"]),
        "beban_usaha": _make_section(m["opex_raw"], m["opex_detail"], negate=False),
        "laba_usaha": float(m["op_profit"]),
        "pendapatan_lain": _make_section(m["other_inc_raw"], m["other_inc_detail"], negate=True),
        "beban_lain": _make_section(m["other_exp_raw"], m["other_exp_detail"], negate=False),
        "laba_sebelum_pajak": float(m["pbt"]),
        "beban_pajak": {"total": 0.0, "akun": []},
        "laba_bersih": float(m["net"]),
        # gelombang 4 L1: tampilan server (aditif)
        "judul": B.JUDUL["laba_rugi"],
        "periode_text": B.periode_berakhir(end),
        "periode_rentang_text": B.periode_rentang(start, end),
        "basis_text": B.BASIS_TEXT.get(basis, basis),
        "satuan_text": B.SATUAN_TEXT,
        "baris": baris_laba_rugi(m),
    }
