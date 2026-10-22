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


# ---- Laba-rugi BASIS KAS (D1; putusan pemilik LANGSUNG 22 Okt 2026: pola QuickBooks cash basis) ------------------------------------------
# Pendapatan diakui saat faktur DIBAYAR: Σ (alokasi penerimaan AKTIF pada penerimaan posted bertanggal di periode + uang muka ber-jurnal tak void
# yang DITERAPKAN ke faktur, bertanggal uang muka di periode) × (pendapatan jurnal efektif faktur itu ÷ total faktur) -> tanpa PPN, proporsional.
# Beban diakui saat DIBAYAR: beban jurnal efektif yang ber-baris kas (beban dibayar kas langsung) + Σ alokasi Pembayaran Keluar posted di periode
# × (debit beban jurnal efektif tagihan ÷ total tagihan) -- pembelian persediaan (aset) bukan beban di basis kas.
SQL_PENDAPATAN_KAS = """
    WITH rev AS (
        SELECT je.source_id AS inv, SUM(jl.credit - jl.debit) AS r
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
          AND c.account_type IN ('REVENUE', 'OTHER_INCOME') AND je.source_type IN ('INVOICE_REVENUE', 'INVOICE')
        GROUP BY je.source_id
    ),
    bayar AS (
        SELECT a.invoice_id, a.amount_applied, rp.payment_date AS tgl
        FROM receive_payment_allocations a JOIN receive_payments rp ON rp.id = a.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
        WHERE a.status = 'active'
        UNION ALL
        SELECT cda.invoice_id, cda.amount_applied, cd.deposit_date
        FROM customer_deposit_applications cda
        JOIN customer_deposits cd ON cd.id = cda.deposit_id AND cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
        WHERE cda.tenant_id = $1 AND cda.status = 'active'
    )
    SELECT COALESCE(SUM(b.amount_applied * COALESCE(rev.r, 0) / NULLIF(si.total_amount, 0)), 0) AS pendapatan
    FROM bayar b JOIN sales_invoices si ON si.id = b.invoice_id AND si.tenant_id = $1 LEFT JOIN rev ON rev.inv = si.id
    WHERE b.tgl BETWEEN $2 AND $3
"""
SQL_BEBAN_KAS_LANGSUNG = """
    SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
    FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
      AND c.account_type IN ('COGS', 'EXPENSE', 'OTHER_EXPENSE') AND je.journal_date BETWEEN $2 AND $3
      AND EXISTS (SELECT 1 FROM journal_lines k JOIN chart_of_accounts kc ON kc.id = k.account_id WHERE k.journal_id = je.id AND kc.is_cash)
"""
SQL_BEBAN_TAGIHAN_DIBAYAR = """
    WITH beban_tagihan AS (
        SELECT je.source_id AS bill, SUM(jl.debit - jl.credit) AS beban
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND je.source_type = 'BILL'
          AND c.account_type IN ('COGS', 'EXPENSE', 'OTHER_EXPENSE')
        GROUP BY je.source_id
    )
    SELECT COALESCE(SUM(a.amount_applied * COALESCE(bt.beban, 0) / NULLIF(COALESCE(b.grand_total, b.amount), 0)), 0)
    FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id AND p.tenant_id = $1 AND p.status = 'posted'
    JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $1 LEFT JOIN beban_tagihan bt ON bt.bill = b.id
    WHERE p.payment_date BETWEEN $2 AND $3
"""


async def laba_rugi_kas(conn, tid: str, mulai: date, akhir: date) -> dict:
    """-> {pendapatan, beban, laba} BASIS KAS (pola QBO; putusan pemilik 22 Okt). Decimal 2dp."""
    q2 = Decimal("0.01")
    pendapatan = d(await conn.fetchval(SQL_PENDAPATAN_KAS, tid, mulai, akhir)).quantize(q2)
    beban = (d(await conn.fetchval(SQL_BEBAN_KAS_LANGSUNG, tid, mulai, akhir))
             + d(await conn.fetchval(SQL_BEBAN_TAGIHAN_DIBAYAR, tid, mulai, akhir))).quantize(q2)
    return {"pendapatan": pendapatan, "beban": beban, "laba": pendapatan - beban}
