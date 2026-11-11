"""PENENTU ANGKA dashboard -- SATU fungsi untuk GET /api/dashboard/all (lama) DAN /api/dashboard/v2/summary (T1-BE "ANGKA BENAR", 22 Okt 2026).

Audit T0 (backend/docs/AUDIT-dashboard-angka-20261022.md) membuktikan /all benar terhadap hitung ulang journal_lines, sedangkan v2 memakai
report_engine.compute_balance status POSTED saja: 16 void beban lama kaos membalik jurnal asli ke status 'VOID' (pelanggaran Law 2 lama) ->
pembalik POSTED terhitung tanpa aslinya (kas +65.218), dan arus kas v2 menghitung pasangan asli+pembalik yang dibatalkan sebagai arus.
Maka SEMUA angka dashboard = jurnal EFEKTIF (`is_effective_journal`: POSTED, bukan asli yang dibalik, bukan pembalik):
  kas_per_akun     saldo akun kas/bank s.d. hari ini (compute_balance_detail + filter efektif)
  arus_kas_harian  {tanggal: (masuk, keluar)} = baris akun kas BRUTO pada jurnal efektif, jurnal transfer antar-kas murni dikecualikan,
                   jurnal saldo awal (SUMBER_SALDO_AWAL) dikecualikan (BUG-020)
                   (definisi /all yang terukur benar; == neto per jurnal efektif pada data 22 Okt)
  laba_rugi        pendapatan = REVENUE + OTHER_INCOME; beban = COGS + EXPENSE + OTHER_EXPENSE (rumus report_engine.income_statement), per akun
  saldo_kontrol    saldo GL akun kontrol (RECEIVABLE / PAYABLE / is_cash) s.d. tanggal -- satu definisi untuk 'kini' dan 'sebelumnya' (change_pct)
Uang = Decimal (pemanggil memformat). Filter tenant eksplisit. Baca-saja."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .report_engine.aliran_kas import KONDISI_ALIRAN  # SATU definisi aliran dgn Arus Kas PSAK (laporan-fix-2)
from .report_engine.ledger import compute_balance, compute_balance_detail

NOL = Decimal("0")
EFEKTIF = {"efektif": True}
TIPE_PENDAPATAN = ("REVENUE", "OTHER_INCOME")
TIPE_BEBAN = ("COGS", "EXPENSE", "OTHER_EXPENSE")

# BUG-020 (5 Nov 2026, pola SaaS): saldo awal bukan arus kas -- jurnal saldo awal (journal_source_types) tak dihitung sebagai uang masuk/keluar.
# Pembaliknya (OPENING_BALANCE_REVERSAL, ..._REVERSED) sudah tersingkir is_effective_journal. Saldo kas (kas_per_akun) tetap memuatnya.
SUMBER_SALDO_AWAL = ("OPENING", "OPENING_BALANCE", "BANK_OPENING_BALANCE", "VENDOR_OPENING_BALANCE")

SQL_ARUS_KAS_HARIAN = f"""
    WITH non_transfer AS (
        SELECT je.id, je.journal_date
        FROM journal_entries je
        WHERE je.tenant_id = $1 AND je.journal_date >= $2 AND je.journal_date <= $3
          AND {KONDISI_ALIRAN}
          AND je.source_type NOT IN ({", ".join(f"'{s}'" for s in SUMBER_SALDO_AWAL)})
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


# ---- T2 (23 Okt 2026, KONTRAK-dashboard-T2 §3–§4): pembanding + tren memakai filter laba_rugi/arus_kas_harian YANG SAMA -----------------
SQL_LABA_RUGI_BULANAN = """
    SELECT date_trunc('month', je.journal_date)::date AS bulan,
           COALESCE(SUM(jl.credit - jl.debit) FILTER (WHERE coa.account_type = ANY($4::text[])), 0) AS pendapatan,
           COALESCE(SUM(jl.debit - jl.credit) FILTER (WHERE coa.account_type = ANY($5::text[])), 0) AS beban
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.status = 'POSTED' AND je.tenant_id = $1 AND je.journal_date >= $2 AND je.journal_date <= $3
      AND is_effective_journal(je.id) AND coa.account_type = ANY($6::text[])
    GROUP BY 1
"""


async def laba_rugi_bulanan(conn, tid: str, mulai: date, akhir: date) -> dict:
    """{awal_bulan: {pendapatan, beban, laba}} SATU kueri `GROUP BY bulan` dengan filter laba_rugi (jurnal EFEKTIF, tipe akun sama).
    Setara laba_rugi(awal_bulan, akhir_bulan) per bulan (diuji + harness nyata T2). Bulan tanpa baris tak muncul (pemanggil = 0)."""
    rows = await conn.fetch(SQL_LABA_RUGI_BULANAN, tid, mulai, akhir, list(TIPE_PENDAPATAN), list(TIPE_BEBAN),
                            list(TIPE_PENDAPATAN + TIPE_BEBAN))
    return {r["bulan"]: {"pendapatan": d(r["pendapatan"]), "beban": d(r["beban"]), "laba": d(r["pendapatan"]) - d(r["beban"])}
            for r in rows}


async def laba_rugi_rentang(conn, tid: str, rentang: list) -> list:
    """[{pendapatan, beban, laba}] untuk BEBERAPA rentang [(mulai, akhir)] dalam SATU kueri (FILTER per rentang) dengan filter laba_rugi
    (jurnal EFEKTIF, tipe akun sama) -- setara laba_rugi(mulai, akhir) per rentang (diuji + harness nyata T2). Untuk pembanding T2:
    3 rentang x 5 compute_balance = 15 kueri -> 1 (syarat latensi MASTER)."""
    if not rentang:
        return []
    par, kolom, i = [tid, list(TIPE_PENDAPATAN), list(TIPE_BEBAN)], [], 4
    for a, b in rentang:
        kolom.append(f"COALESCE(SUM(jl.credit - jl.debit) FILTER (WHERE coa.account_type = ANY($2::text[]) AND je.journal_date BETWEEN ${i} AND ${i + 1}), 0)")
        kolom.append(f"COALESCE(SUM(jl.debit - jl.credit) FILTER (WHERE coa.account_type = ANY($3::text[]) AND je.journal_date BETWEEN ${i} AND ${i + 1}), 0)")
        par += [a, b]
        i += 2
    lo, hi = min(a for a, _ in rentang), max(b for _, b in rentang)
    row = await conn.fetchrow(
        f"""SELECT {", ".join(kolom)}
            FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.status = 'POSTED' AND je.tenant_id = $1 AND je.journal_date >= ${i} AND je.journal_date <= ${i + 1}
              AND is_effective_journal(je.id) AND (coa.account_type = ANY($2::text[]) OR coa.account_type = ANY($3::text[]))""",
        *par, lo, hi)
    out = []
    for n in range(len(rentang)):
        p, bb = d(row[2 * n]), d(row[2 * n + 1])
        out.append({"pendapatan": p, "beban": bb, "laba": p - bb})
    return out


async def arus_kas_neto_rentang(conn, tid: str, rentang: list) -> list:
    """[kas bersih] per rentang dari SATU arus_kas_harian atas gabungan rentang (definisi arus kas D3 yang sama)."""
    if not rentang:
        return []
    harian = await arus_kas_harian(conn, tid, min(a for a, _ in rentang), max(b for _, b in rentang))
    return [sum((m - k for t, (m, k) in harian.items() if a <= t <= b), NOL) for a, b in rentang]


async def arus_kas_neto(conn, tid: str, mulai: date, akhir: date) -> Decimal:
    """Kas bersih periode = Σ (masuk − keluar) arus_kas_harian (== arus kas D3; putusan MASTER T2 §7.4: tanpa rumus baru)."""
    return sum((m - k for m, k in (await arus_kas_harian(conn, tid, mulai, akhir)).values()), NOL)


async def jurnal_efektif_pertama(conn, tid: str) -> Optional[date]:
    """Tanggal jurnal EFEKTIF pertama tenant -- pembanding/tren yang berakhir sebelum ini = null (bukan "0.00")."""
    return await conn.fetchval(
        # urut tanggal + LIMIT 1: berhenti di jurnal efektif pertama (is_effective_journal per baris mahal bila memindai semua)
        "SELECT journal_date FROM journal_entries je WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)"
        " ORDER BY journal_date LIMIT 1",
        tid)


async def bulan_awal_fiskal(conn, tid: str) -> int:
    """accounting_settings.fiscal_year_start_month (bawaan 1 = tahun kalender; tanpa baris -> 1). Kolom kembar "Tenant".fiscal_year_start
    hanya ditulis onboarding dan TAK dibaca di sini (audit T2 U1)."""
    n = await conn.fetchval("SELECT fiscal_year_start_month FROM accounting_settings WHERE tenant_id = $1", tid)
    return int(n) if n and 1 <= int(n) <= 12 else 1


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


# ---- Laba-rugi BASIS KAS (D1; putusan pemilik 22 Okt + 10 Nov): SATU sumber dgn Laba Rugi PSAK basis kas = services/report_engine/basis_kas.py
# (pendapatan + HPP faktur diakui proporsional saat DIBAYAR, beban saat dibayar; per akun; laporan-fix-2). Dulu tiga SQL total di sini (tanpa HPP faktur).
async def laba_rugi_kas(conn, tid: str, mulai: date, akhir: date) -> dict:
    """-> {pendapatan, beban, laba} BASIS KAS (pola QBO). Decimal 2dp. Total SAMA dengan Laba Rugi PSAK basis kas (satu fungsi)."""
    from .report_engine import basis_kas
    return basis_kas.ringkas(await basis_kas.detail(conn, tid, mulai, akhir))
