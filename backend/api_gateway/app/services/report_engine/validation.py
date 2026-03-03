"""
Auto-validation: 4 mandatory PSAK reconciliation checks.
Run after generating all 3 statements to verify internal consistency.
"""


async def validate_reports(bs: dict, pl: dict, cf: dict) -> list:
    """
    Validate cross-report consistency.
    Returns list of error strings. Empty list = all checks pass.
    """
    errors = []

    # Check 1: Neraca balance (Aset = Kewajiban + Ekuitas)
    if not bs.get("_balanced", False):
        gap = bs["total_aset"] - bs["total_liabilitas_ekuitas"]
        errors.append(f"NERACA TIDAK BALANCE: gap Rp {gap:,.0f}")

    # Check 2: Laba P&L = Laba di Neraca
    pl_profit = pl.get("laba_bersih", 0)
    bs_profit = bs.get("ekuitas", {}).get("laba_periode", 0)
    if abs(pl_profit - bs_profit) > 0.01:
        errors.append(
            f"LABA TIDAK RECONCILE: P&L={pl_profit:,.0f}, Neraca={bs_profit:,.0f}"
        )

    # Check 3: Arus kas internal reconciliation
    if not cf.get("_reconciled", False):
        kas_awal = cf.get("kas_awal", 0)
        delta = cf.get("kenaikan_kas_bersih", 0)
        kas_akhir = cf.get("kas_akhir", 0)
        errors.append(
            f"ARUS KAS: kas_awal({kas_awal:,.0f}) + delta({delta:,.0f}) != kas_akhir({kas_akhir:,.0f})"
        )

    # Check 4: Kas di arus kas = kas di neraca
    cf_kas = cf.get("kas_akhir", 0)
    # kas_setara_kas is now a section dict with .total
    bs_kas_section = bs.get("aset_lancar", {}).get("kas_setara_kas", {})
    if isinstance(bs_kas_section, dict):
        bs_kas = bs_kas_section.get("total", 0)
    else:
        bs_kas = bs_kas_section  # backward compat
    if abs(cf_kas - bs_kas) > 0.01:
        errors.append(
            f"KAS TIDAK COCOK: Arus Kas={cf_kas:,.0f}, Neraca={bs_kas:,.0f}"
        )

    return errors
