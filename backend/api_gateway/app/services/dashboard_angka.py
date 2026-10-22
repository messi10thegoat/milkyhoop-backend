"""PENENTU ANGKA dashboard -- SATU fungsi untuk GET /api/dashboard/all (lama) DAN /api/dashboard/v2/summary (T1-BE "ANGKA BENAR", 22 Okt 2026).

Audit T0 (backend/docs/AUDIT-dashboard-angka-20261022.md) membuktikan /all benar terhadap hitung ulang journal_lines, sedangkan v2 memakai
report_engine.compute_balance status POSTED saja: 16 void beban lama kaos membalik jurnal asli ke status 'VOID' (pelanggaran Law 2 lama) ->
pembalik POSTED terhitung tanpa aslinya (kas +65.218), dan arus kas v2 menghitung pasangan asli+pembalik yang dibatalkan sebagai arus.
Maka SEMUA angka dashboard = jurnal EFEKTIF (`is_effective_journal`: POSTED, bukan asli yang dibalik, bukan pembalik):
  kas_per_akun     saldo akun kas/bank s.d. hari ini (compute_balance_detail + filter efektif)
  arus_kas_harian  {tanggal: (masuk, keluar)} = baris akun kas BRUTO pada jurnal efektif, jurnal transfer antar-kas murni dikecualikan
                   (definisi /all yang terukur benar; == neto per jurnal efektif pada data 22 Okt)
  laba_rugi        pendapatan = REVENUE + OTHER_INCOME; beban = COGS + EXPENSE + OTHER_EXPENSE (rumus report_engine.income_statement), per akun
  saldo_kontrol    saldo GL akun kontrol (RECEIVABLE / PAYABLE / is_cash) s.d. tanggal -- satu definisi untuk 'kini' dan 'sebelumnya' (change_pct)
Uang = Decimal (pemanggil memformat). Filter tenant eksplisit. Baca-saja."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .report_engine.ledger import compute_balance, compute_balance_detail

NOL = Decimal("0")
EFEKTIF = {"efektif": True}
TIPE_PENDAPATAN = ("REVENUE", "OTHER_INCOME")
TIPE_BEBAN = ("COGS", "EXPENSE", "OTHER_EXPENSE")

SQL_ARUS_KAS_HARIAN = """
    WITH non_transfer AS (
        SELECT je.id, je.journal_date
        FROM journal_entries je
        WHERE je.tenant_id = $1 AND je.journal_date >= $2 AND je.journal_date <= $3
          AND je.status = 'POSTED' AND is_effective_journal(je.id) = true
          AND EXISTS (SELECT 1 FROM journal_lines jl2 JOIN chart_of_accounts c2 ON c2.id = jl2.account_id
                      WHERE jl2.journal_id = je.id AND COALESCE(c2.is_cash, false) = false)
    )
    SELECT e.journal_date AS tgl,
           COALESCE(SUM(CASE WHEN jl.debit > 0 AND c.is_cash THEN jl.debit ELSE 0 END), 0) AS masuk,
           COALESCE(SUM(CASE WHEN jl.credit > 0 AND c.is_cash THEN jl.credit ELSE 0 END), 0) AS keluar
    FROM non_transfer e JOIN journal_lines jl ON jl.journal_id = e.id JOIN chart_of_accounts c ON c.id = jl.account_id
    GROUP BY e.journal_date
"""


def d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


async def kas_per_akun(conn, tid: str, hari_ini: date) -> tuple:
    """-> (total, [{account_code, account_name, balance}]) saldo akun is_cash jurnal EFEKTIF s.d. hari ini."""
    rinci = await compute_balance_detail(conn, tid, {"is_cash": True, **EFEKTIF}, end_date=hari_ini)
    return sum((d(r["balance"]) for r in rinci), NOL), rinci


async def arus_kas_harian(conn, tid: str, mulai: date, akhir: date) -> dict:
    return {r["tgl"]: (d(r["masuk"]), d(r["keluar"])) for r in await conn.fetch(SQL_ARUS_KAS_HARIAN, tid, mulai, akhir)}


async def laba_rugi(conn, tid: str, mulai: date, akhir: date) -> dict:
    """-> {pendapatan, beban, laba, rinci_beban[{account_code, account_name, balance, account_type}]} jurnal EFEKTIF dalam periode."""
    pendapatan = NOL
    for t in TIPE_PENDAPATAN:
        pendapatan -= await compute_balance(conn, tid, {"account_type": t, **EFEKTIF}, start_date=mulai, end_date=akhir)
    rinci = []
    for t in TIPE_BEBAN:
        rinci += [{**r, "account_type": t} for r in
                  await compute_balance_detail(conn, tid, {"account_type": t, **EFEKTIF}, start_date=mulai, end_date=akhir)]
    beban = sum((d(r["balance"]) for r in rinci), NOL)
    return {"pendapatan": pendapatan, "beban": beban, "laba": pendapatan - beban, "rinci_beban": rinci}


async def hpp(conn, tid: str, mulai: date, akhir: date) -> Decimal:
    return await compute_balance(conn, tid, {"account_type": "COGS", **EFEKTIF}, start_date=mulai, end_date=akhir)


async def saldo_kontrol(conn, tid: str, jenis: str, per_tgl: date) -> Decimal:
    """Saldo GL efektif s.d. per_tgl: 'piutang' (RECEIVABLE, debit+), 'hutang' (PAYABLE, kredit+), 'kas' (is_cash, debit+)."""
    if jenis == "kas":
        return await compute_balance(conn, tid, {"is_cash": True, **EFEKTIF}, end_date=per_tgl)
    tipe = {"piutang": "RECEIVABLE", "hutang": "PAYABLE"}[jenis]
    s = await compute_balance(conn, tid, {"account_type": tipe, **EFEKTIF}, end_date=per_tgl)
    return s if jenis == "piutang" else -s


def hari_periode(mulai: date, akhir: date) -> int:
    """Jumlah hari INKLUSIF (1–31 Okt = 31), dulu (akhir − mulai).days kurang 1."""
    return (akhir - mulai).days + 1


def periode_sebelumnya(mulai: date, akhir: date) -> tuple:
    """Periode sebanding SEBELUMNYA dengan panjang sama (pola QBO 'previous period')."""
    n = hari_periode(mulai, akhir)
    akhir_p = mulai - timedelta(days=1)
    return akhir_p - timedelta(days=n - 1), akhir_p


def dpo(hutang: Decimal, hpp_periode: Decimal, hari: int) -> float:
    """Days Payable Outstanding baku = Hutang ÷ (HPP periode ÷ hari). HPP 0 -> 0.0 (tak terdefinisi, bukan pengeluaran total)."""
    if hpp_periode <= NOL or hari <= 0:
        return 0.0
    return float(round(hutang / (hpp_periode / hari), 1))


def dso(piutang: Decimal, pendapatan: Decimal, hari: int) -> float:
    if pendapatan <= NOL or hari <= 0:
        return 0.0
    return float(round(piutang / (pendapatan / hari), 1))
