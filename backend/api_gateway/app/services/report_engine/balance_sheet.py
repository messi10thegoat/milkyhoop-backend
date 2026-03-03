"""
Neraca / Laporan Posisi Keuangan (Balance Sheet) — PSAK staffel.
All data from journal_lines (Law 1).

Sign convention:
  - Assets (ASSET, RECEIVABLE): debit-normal → raw positive = normal
  - Contra-assets (ACCUM_DEPRECIATION): raw negative = normal (don't negate)
  - Liabilities (LIABILITY, PAYABLE): credit-normal → raw negative → negate for display
  - Equity (EQUITY): credit-normal → raw negative → negate for display

IMPORTANT: RECEIVABLE and PAYABLE are separate account_types in MilkyHoop,
not subsets of ASSET/LIABILITY. Must query both for totals.
"""
from decimal import Decimal
from .ledger import compute_balance, compute_balance_detail
from .income_statement import generate_income_statement


# Debit-normal types that contribute to total assets
ASSET_TYPES = ["ASSET", "RECEIVABLE"]

# Credit-normal types that contribute to total liabilities
LIABILITY_TYPES = ["LIABILITY", "PAYABLE"]

# Sub-categories that belong to non-current assets
NON_CURRENT_SUBS = ["FIXED_ASSET", "ACCUM_DEPRECIATION"]

# Sub-categories already bucketed in aset lancar
CURRENT_KNOWN_SUBS = ["TRADE_RECEIVABLE", "INVENTORY"]

# Sub-categories for current vs non-current liabilities
CURRENT_LIABILITY_SUBS = ["CURRENT_LIABILITY", "TRADE_PAYABLE"]
NON_CURRENT_LIABILITY_SUBS = ["NON_CURRENT_LIABILITY"]


def _section(total, detail, negate=False):
    """Build section with total + akun[] breakdown."""
    if negate:
        return {
            "total": float(-total),
            "akun": [
                {
                    "account_code": d["account_code"],
                    "account_name": d["account_name"],
                    "balance": float(-d["balance"]),
                }
                for d in detail
            ],
        }
    return {
        "total": float(total),
        "akun": [
            {
                "account_code": d["account_code"],
                "account_name": d["account_name"],
                "balance": float(d["balance"]),
            }
            for d in detail
        ],
    }


async def generate_balance_sheet(
    conn, tenant_id: str, as_of: str, period_start: str = None, basis: str = "accrual"
) -> dict:
    """
    Balance sheet as of date. period_start used for current period P&L.
    Defaults to Jan 1 of as_of year.
    """
    if not period_start:
        period_start = as_of[:4] + "-01-01"

    async def b(fp):
        return await compute_balance(conn, tenant_id, fp, end_date=as_of)

    async def bd(fp):
        return await compute_balance_detail(conn, tenant_id, fp, end_date=as_of)

    # ===== ASET (debit-normal → positive is normal) =====

    # Kas & setara kas
    kas = await b({"is_cash": True})
    kas_detail = await bd({"is_cash": True})

    # Piutang usaha (RECEIVABLE accounts)
    piutang = await b({"account_types": ASSET_TYPES, "psak_sub_category": "TRADE_RECEIVABLE"})
    piutang_detail = await bd({"account_types": ASSET_TYPES, "psak_sub_category": "TRADE_RECEIVABLE"})

    # Persediaan
    persediaan = await b({"psak_sub_category": "INVENTORY"})
    persediaan_detail = await bd({"psak_sub_category": "INVENTORY"})

    # Aset lancar lainnya: ASSET+RECEIVABLE minus kas, piutang, persediaan, non-current
    lainnya_filter = {
        "account_types": ASSET_TYPES,
        "is_cash": False,
        "psak_sub_category_not_in": CURRENT_KNOWN_SUBS + NON_CURRENT_SUBS,
        "is_header_false": True,
    }
    aset_lancar_lain = await b(lainnya_filter)
    aset_lancar_lain_detail = await bd(lainnya_filter)

    total_aset_lancar = kas + piutang + persediaan + aset_lancar_lain

    # Aset tetap (gross + akumulasi penyusutan in one list)
    aset_tetap_gross = await b({"psak_sub_category": "FIXED_ASSET"})
    aset_tetap_gross_detail = await bd({"psak_sub_category": "FIXED_ASSET"})
    akum_penyusutan = await b({"psak_sub_category": "ACCUM_DEPRECIATION"})
    akum_penyusutan_detail = await bd({"psak_sub_category": "ACCUM_DEPRECIATION"})

    aset_tetap_neto = aset_tetap_gross + akum_penyusutan

    # Combine fixed asset + accum depreciation into one akun list
    aset_tetap_all_detail = [
        {"account_code": d["account_code"], "account_name": d["account_name"], "balance": float(d["balance"])}
        for d in aset_tetap_gross_detail
    ] + [
        {"account_code": d["account_code"], "account_name": d["account_name"], "balance": float(d["balance"])}
        for d in akum_penyusutan_detail
    ]

    # Total all assets (ASSET + RECEIVABLE)
    total_aset_raw = await b({"account_types": ASSET_TYPES})

    # ===== LIABILITAS (credit-normal → raw negative → negate for display) =====

    # Utang usaha (TRADE_PAYABLE) — Jangka Pendek
    utang_usaha_raw = await b({"account_types": LIABILITY_TYPES, "psak_sub_category": "TRADE_PAYABLE"})
    utang_usaha_detail = await bd({"account_types": LIABILITY_TYPES, "psak_sub_category": "TRADE_PAYABLE"})

    # Liabilitas jangka pendek lainnya (CURRENT_LIABILITY)
    current_liab_other_raw = await b({"account_types": LIABILITY_TYPES, "psak_sub_category": "CURRENT_LIABILITY"})
    current_liab_other_detail = await bd({"account_types": LIABILITY_TYPES, "psak_sub_category": "CURRENT_LIABILITY"})

    # Liabilitas jangka panjang (NON_CURRENT_LIABILITY)
    noncurrent_liab_raw = await b({"account_types": LIABILITY_TYPES, "psak_sub_category": "NON_CURRENT_LIABILITY"})
    noncurrent_liab_detail = await bd({"account_types": LIABILITY_TYPES, "psak_sub_category": "NON_CURRENT_LIABILITY"})

    # Total liabilities from ledger (LIABILITY + PAYABLE)
    total_liab_raw = await b({"account_types": LIABILITY_TYPES})
    total_liab = -total_liab_raw

    utang_usaha_sec = _section(utang_usaha_raw, utang_usaha_detail, negate=True)
    current_other_sec = _section(current_liab_other_raw, current_liab_other_detail, negate=True)
    noncurrent_sec = _section(noncurrent_liab_raw, noncurrent_liab_detail, negate=True)

    total_jp = utang_usaha_sec["total"] + current_other_sec["total"]
    total_jpp = noncurrent_sec["total"]

    # ===== EKUITAS (credit-normal → raw negative → negate for display) =====

    # Modal disetor
    modal_raw = await b({"psak_sub_category": "PAID_IN_CAPITAL"})
    modal_detail = await bd({"psak_sub_category": "PAID_IN_CAPITAL"})
    modal = -modal_raw

    # Saldo laba ditahan
    saldo_laba_raw = await b({"psak_sub_category": "RETAINED_EARNINGS"})
    saldo_laba_detail = await bd({"psak_sub_category": "RETAINED_EARNINGS"})
    saldo_laba = -saldo_laba_raw

    # Laba periode berjalan (derived from P&L)
    pl = await generate_income_statement(conn, tenant_id, period_start, as_of, basis=basis)
    laba_periode = Decimal(str(pl["laba_bersih"]))

    # Total equity from ledger (EQUITY type only)
    total_ekuitas_raw = await b({"account_type": "EQUITY"})
    total_ekuitas_ledger = -total_ekuitas_raw

    # The P&L hasn't been closed to retained earnings yet, so:
    # Total equity = ledger equity + current period P&L
    total_ekuitas = total_ekuitas_ledger + laba_periode

    # Other equity detail (prive, drawings, etc.)
    ekuitas_lain_filter = {
        "account_type": "EQUITY",
        "psak_sub_category_not_in": ["PAID_IN_CAPITAL", "RETAINED_EARNINGS"],
        "is_header_false": True,
    }
    ekuitas_lain_detail = await bd(ekuitas_lain_filter)
    ekuitas_lain = total_ekuitas_ledger - modal - saldo_laba

    total_le = total_liab + total_ekuitas

    return {
        "as_of": as_of,
        "aset_lancar": {
            "kas_setara_kas": _section(kas, kas_detail),
            "piutang_usaha": _section(piutang, piutang_detail),
            "persediaan": _section(persediaan, persediaan_detail),
            "lainnya": _section(aset_lancar_lain, aset_lancar_lain_detail),
            "total": float(total_aset_lancar),
        },
        "aset_tidak_lancar": {
            "aset_tetap": {
                "total": float(aset_tetap_neto),
                "akun": aset_tetap_all_detail,
            },
            "total": float(aset_tetap_neto),
        },
        "total_aset": float(total_aset_raw),
        "liabilitas": {
            "jangka_pendek": {
                "utang_usaha": utang_usaha_sec,
                "lainnya": current_other_sec,
                "total": float(total_jp),
            },
            "jangka_panjang": {
                "akun": noncurrent_sec["akun"],
                "total": float(total_jpp),
            },
            "total": float(total_liab),
        },
        "ekuitas": {
            "modal_disetor": _section(modal_raw, modal_detail, negate=True),
            "saldo_laba": _section(saldo_laba_raw, saldo_laba_detail, negate=True),
            "laba_periode": float(laba_periode),
            "lainnya": _section(Decimal(str(ekuitas_lain)), ekuitas_lain_detail, negate=True),
            "total": float(total_ekuitas),
        },
        "total_liabilitas_ekuitas": float(total_le),
        "_balanced": abs(total_aset_raw - total_le) < Decimal("0.01"),
        "basis_applied": basis,
    }
