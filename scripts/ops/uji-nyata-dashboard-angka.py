"""AUDIT angka dashboard (T0, 22 Okt 2026, baca-saja): GET /api/dashboard/all (handler nyata, koneksi READ ONLY) vs hitung ulang INDEPENDEN
dari journal_lines (effective, POSTED) untuk grapgrap + kaos, periode BULAN INI dan YTD. Izin dashboard di-stub 'boleh' (yang diaudit = angka).
Keluaran = tabel per kunci (endpoint | independen | selisih) + contoh dokumen untuk selisih. Tidak ada tulisan."""
import asyncio, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
EFF = "je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)"


def D(v): return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"))


def baris(k, ep, ind, catat=""):
    s = (D(ep) - D(ind)) if ep is not None else None
    tanda = "SAMA" if s == 0 else ("BEDA" if s is not None else "NULL")
    print(f"  {tanda:4} {k:44} endpoint={ep!s:>16} independen={D(ind)!s:>16} selisih={s!s:>14} {catat}", flush=True)


async def main():
    from app.routers import dashboard as DB
    # SEMUA koneksi dipaksa READ ONLY di sisi server (default_transaction_read_only) -> tulisan apa pun = galat; widget /all paralel
    # tetap mendapat koneksi sendiri seperti di produksi.
    ro = {"default_transaction_read_only": "on"}
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=8, server_settings=ro)
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings=ro)
    tx = c.transaction(readonly=True); await tx.start()
    async def gp(): return pool
    DB.get_db_pool = gp
    async def ya(*a, **k): return True
    async def kosong(*a, **k): return []
    DB.boleh_baca = ya; DB.widget_boleh = ya; DB.saring_bagian_summary = kosong
    try:
        for t in TEN:
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', t)
            hari = await c.fetchval("SELECT tanggal_bisnis($1)", t)
            for nama, mulai in (("BULAN", hari.replace(day=1)), ("YTD", hari.replace(month=1, day=1))):
                async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
                r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
                r.state.user = {"tenant_id": t, "user_id": str(uid), "role": "OWNER"}
                a = await DB.get_dashboard_all(r, start_date=mulai.isoformat(), end_date=hari.isoformat(), basis="accrual",
                                               cf_start_date=None, cf_end_date=None, exp_start_date=None, exp_end_date=None,
                                               expense_limit=20, sales_start_date=mulai.isoformat(), sales_end_date=hari.isoformat())
                ac = await DB.get_dashboard_all(r, start_date=mulai.isoformat(), end_date=hari.isoformat(), basis="cash",
                                                cf_start_date=None, cf_end_date=None, exp_start_date=None, exp_end_date=None,
                                                expense_limit=20, sales_start_date=None, sales_end_date=None)
                print(f"\n=== {t} {nama} {mulai}..{hari}  gagal-widget={[k for k, v in a.items() if v is None]}", flush=True)
                s = a["summary"]
                q = lambda tipe, tanda: c.fetchval(  # noqa: E731
                    f"""SELECT COALESCE(SUM({tanda}), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE {EFF} AND coa.account_type = ANY($2::text[])
                        AND je.journal_date BETWEEN $3 AND $4""", t, tipe, mulai, hari)
                rev = await q(["REVENUE"], "jl.credit - jl.debit")
                oth = await q(["OTHER_INCOME"], "jl.credit - jl.debit")
                exp = await q(["EXPENSE", "COGS", "OTHER_EXPENSE"], "jl.debit - jl.credit")
                lr = s["laba_rugi"]
                baris("laba_rugi.pendapatan (akrual)", lr["pendapatan"], rev, f"(+OTHER_INCOME {D(oth)} tak dihitung endpoint)")
                baris("laba_rugi.pengeluaran (akrual)", lr["pengeluaran"], exp)
                baris("laba_rugi.profit (akrual) vs rev+oth-exp", lr["profit"], rev + oth - exp)
                # basis kas: kas masuk dari pelanggan / kas keluar ke vendor+beban dalam periode (definisi independen sederhana)
                kas_in_ar = await c.fetchval(
                    f"""SELECT COALESCE(SUM(jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE {EFF} AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
                        AND je.source_type IN ('PAYMENT_RECEIVED','RECEIVE_PAYMENT','DEPOSIT_APPLICATION','CUSTOMER_DEPOSIT')
                        AND je.journal_date BETWEEN $2 AND $3""", t, mulai, hari)
                lrc = ac["summary"]["laba_rugi"]
                baris("laba_rugi.pendapatan (KAS)", lrc["pendapatan"], kas_in_ar, "(independen = kredit Piutang dari penerimaan; indikatif)")
                baris("laba_rugi.pengeluaran (KAS)", lrc["pengeluaran"], 0, "(lihat sumber: PAYMENT_MADE/CASH_EXPENSE ke akun beban)")
                # AR / AP / Kas
                gl_ar = await c.fetchval(f"""SELECT COALESCE(SUM(jl.debit - jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE {EFF} AND coa.account_type = 'RECEIVABLE'""", t)
                gl_ap = await c.fetchval(f"""SELECT COALESCE(SUM(jl.credit - jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE {EFF} AND coa.account_type = 'PAYABLE'""", t)
                kas = await c.fetchval(f"""SELECT COALESCE(SUM(jl.debit - jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE {EFF} AND coa.is_cash""", t)
                ar_f = await c.fetchrow("SELECT COALESCE(SUM(outstanding),0) s, COUNT(*) FILTER (WHERE outstanding <= 0) nol, COUNT(*) n, "
                                        "COUNT(*) FILTER (WHERE invoice_id IS NULL) kredit FROM compute_ar_outstanding($1)", t)
                ap_f = await c.fetchrow("SELECT COALESCE(SUM(outstanding),0) s, COUNT(*) FILTER (WHERE outstanding <= 0) nol, COUNT(*) n, "
                                        "COUNT(*) FILTER (WHERE bill_id IS NULL) kredit FROM compute_ap_outstanding($1)", t)
                baris("piutang.total vs GL Piutang", s["piutang"]["total"], gl_ar, f"(fungsi baris={ar_f['n']}, outstanding<=0={ar_f['nol']}, tanpa faktur={ar_f['kredit']})")
                baris("hutang.total vs GL Hutang", s["hutang"]["total"], gl_ap, f"(fungsi baris={ap_f['n']}, outstanding<=0={ap_f['nol']}, tanpa tagihan={ap_f['kredit']})")
                baris("kas_bank.total vs GL is_cash", s["kas_bank"]["total"], kas)
                aging_ar = sum(s["piutang"][k] for k in ("current", "overdue_1_30", "overdue_31_60", "overdue_61_90", "overdue_90_plus"))
                baris("piutang Σ aging == total", aging_ar, s["piutang"]["total"])
                aging_ap = sum(s["hutang"][k] for k in ("current", "overdue_1_30", "overdue_31_60", "overdue_61_90", "overdue_90_plus"))
                baris("hutang Σ aging == total", aging_ap, s["hutang"]["total"])
                od_ar = await c.fetchrow("SELECT COUNT(*) n, COALESCE(SUM(outstanding),0) s FROM compute_ar_outstanding($1) WHERE outstanding > 0 AND due_date < $2", t, hari)
                od_ap = await c.fetchrow("SELECT COUNT(*) n, COALESCE(SUM(outstanding),0) s FROM compute_ap_outstanding($1) WHERE outstanding > 0 AND due_date < $2", t, hari)
                baris("piutang.jatuh_tempo (jumlah) vs >0 & lewat", s["piutang"]["jatuh_tempo"], od_ar["n"])
                baris("hutang.jatuh_tempo (jumlah) vs >0 & lewat", s["hutang"]["jatuh_tempo"], od_ap["n"])
                oi, ob = a.get("overdueInvoices") or {}, a.get("overdueBills") or {}
                baris("overdueInvoices.total vs >0 & lewat", oi.get("total_outstanding"), od_ar["s"], f"(count ep={oi.get('count')} ind={od_ar['n']})")
                baris("overdueBills.total vs >0 & lewat", ob.get("total_outstanding"), od_ap["s"], f"(count ep={ob.get('count')} ind={od_ap['n']})")
                print(f"  INFO hutang.nearest_supplier={s['hutang'].get('nearest_supplier')} nearest_days={s['hutang'].get('nearest_days')}", flush=True)
                # Penjualan periode
                st = a["salesToday"]
                baris("salesToday.total_revenue (periode) vs REVENUE", st["total_revenue"], rev)
                # Arus kas (bulan berjalan, kunci cashFlow)
                cf = a.get("cashFlow") or {}
                print(f"  INFO cashFlow kunci={list(cf)[:8]}", flush=True)
                # Top pengeluaran vs pengeluaran P&L
                ex = a.get("expenses") or {}
                baris("expenses.total (kode 5-/6-) vs P&L pengeluaran", ex.get("total"), exp,
                      "(top-20; beda = akun beban di luar kode 5-/6- atau COGS/OTHER di kode lain)")
                # KPI
                k = s["kpi"]
                print(f"  INFO kpi dso={k['dso']} dpo={k['dpo']} (DPO memakai pengeluaran total, bukan pembelian)", flush=True)
                print(f"  INFO change_pct piutang={s['piutang']['change_pct']} prev_total={s['piutang']['prev_total']} (prev = GL Piutang; kini = compute_ar_summary)", flush=True)
    finally:
        await tx.rollback(); await c.close(); await pool.close()

asyncio.run(main())
