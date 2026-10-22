"""UJI NYATA paritas dashboard T1-BE (22 Okt 2026, BACA-SAJA): /api/dashboard/v2/summary == /api/dashboard/all == hitung ulang INDEPENDEN
dari journal_lines (POSTED + is_effective_journal), grapgrap + kaos, periode BULAN (kalender penuh) dan TAHUN (Jan–Des).
Kunci: kas total, pendapatan (REVENUE+OTHER_INCOME), beban (COGS+EXPENSE+OTHER_EXPENSE), laba, arus kas masuk/keluar (s.d. hari ini).
Plus: /all basis=cash -> errors.basis 'belum_tersedia' (bukan 0 palsu); hutang.nearest_days >= 0 atau null; errors{} ada.
SEMUA koneksi default_transaction_read_only=on. Izin dashboard di-stub 'boleh' (yang diuji = angka).
MERAH=1: filter efektif dimatikan (DA.EFEKTIF = {} -> POSTED saja, perilaku v2 lama) -> kaos kas WAJIB merah 65.218."""
import asyncio, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
MERAH = os.environ.get("MERAH") == "1"
EFF = "je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)"
gagal = []


def D(v): return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"))


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


async def main():
    from app.routers import dashboard as DB
    from app.services import dashboard_angka as DA, dashboard_v2 as V2
    if MERAH:
        DA.EFEKTIF.clear()
    ro = {"default_transaction_read_only": "on"}
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=8, server_settings=ro)
    async def gp(): return pool
    DB.get_db_pool = gp
    async def ya(*a, **k): return True
    async def kosong(*a, **k): return []
    DB.boleh_baca = ya; DB.widget_boleh = ya; DB.saring_bagian_summary = kosong
    c = await pool.acquire()
    try:
        for t in TEN:
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', t)
            hari = await c.fetchval("SELECT tanggal_bisnis($1)", t)
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
            r.state.user = {"tenant_id": t, "user_id": str(uid), "role": "OWNER"}
            kas_ind = D(await c.fetchval(f"""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE {EFF} AND coa.is_cash AND je.journal_date <= $2""", t, hari))
            for periode in ("month", "year"):
                mulai, akhir = V2.rentang_periode(periode, hari)
                v2 = await V2.ringkasan(c, t, periode, hari, "Asia/Jakarta", "x")
                a = await DB.get_dashboard_all(r, start_date=mulai.isoformat(), end_date=akhir.isoformat(), basis="accrual",
                                               cf_start_date=mulai.isoformat(), cf_end_date=hari.isoformat(), exp_start_date=None, exp_end_date=None,
                                               expense_limit=20, sales_start_date=None, sales_end_date=None)
                L = f"{t} {periode}"
                q = lambda tipe, tanda, b=None: c.fetchval(  # noqa: E731
                    f"""SELECT COALESCE(SUM({tanda}),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE {EFF} AND coa.account_type = ANY($2::text[])
                        AND je.journal_date BETWEEN $3 AND $4""", t, tipe, mulai, b or akhir)
                rev = D(await q(["REVENUE", "OTHER_INCOME"], "jl.credit-jl.debit"))
                exp = D(await q(["COGS", "EXPENSE", "OTHER_EXPENSE"], "jl.debit-jl.credit"))
                s = a["summary"]
                cek(D(v2["cash"]["total"]) == D(s["kas_bank"]["total"]) == kas_ind, f"{L} kas v2 == /all == independen",
                    f"{v2['cash']['total']} / {s['kas_bank']['total']} / {kas_ind}")
                cek(D(v2["pnl"]["revenue"]) == D(s["laba_rugi"]["pendapatan"]) == rev, f"{L} pendapatan (incl OTHER_INCOME)",
                    f"{v2['pnl']['revenue']} / {s['laba_rugi']['pendapatan']} / {rev}")
                cek(D(v2["pnl"]["expense"]) == D(s["laba_rugi"]["pengeluaran"]) == exp == D(a["expenses"]["total"]), f"{L} beban (P&L == top beban total)",
                    f"{v2['pnl']['expense']} / {s['laba_rugi']['pengeluaran']} / {exp} / {a['expenses']['total']}")
                cek(D(v2["pnl"]["profit"]) == D(s["laba_rugi"]["profit"]) == rev - exp, f"{L} laba")
                # arus kas s.d. hari ini: masuk/keluar bruto baris kas jurnal efektif non-transfer
                ak = await c.fetchrow(f"""WITH nt AS (SELECT je.id FROM journal_entries je WHERE {EFF} AND je.journal_date BETWEEN $2 AND $3
                        AND EXISTS (SELECT 1 FROM journal_lines x JOIN chart_of_accounts y ON y.id=x.account_id WHERE x.journal_id=je.id AND NOT COALESCE(y.is_cash,false)))
                    SELECT COALESCE(SUM(jl.debit) FILTER (WHERE c.is_cash),0) m, COALESCE(SUM(jl.credit) FILTER (WHERE c.is_cash),0) k
                    FROM nt JOIN journal_lines jl ON jl.journal_id=nt.id JOIN chart_of_accounts c ON c.id=jl.account_id""", t, mulai, hari)
                cf = v2["cashflow"]
                cek(D(cf["total_in"]) == D(a["cashFlow"]["kas_masuk"]) == D(ak["m"]) and D(cf["total_out"]) == D(a["cashFlow"]["kas_keluar"]) == D(ak["k"]),
                    f"{L} arus kas masuk/keluar v2 == /all == independen",
                    f"in {cf['total_in']}/{a['cashFlow']['kas_masuk']}/{D(ak['m'])} out {cf['total_out']}/{a['cashFlow']['kas_keluar']}/{D(ak['k'])}")
                nd = s["hutang"].get("nearest_days")
                cek(nd is None or nd >= 0, f"{L} hutang.nearest_days belum lewat (D7)", str(nd))
                cek(a.get("errors") == {}, f"{L} /all errors kosong (semua widget berhasil)", str(a.get("errors")))
            # D1 basis kas (putusan pemilik: pola QBO) -- hitung ulang INDEPENDEN per faktur di Python dari baris alokasi
            m0 = hari.replace(day=1)
            ac = await DB.get_dashboard_all(r, start_date=m0.isoformat(), end_date=hari.isoformat(), basis="cash", cf_start_date=None, cf_end_date=None,
                                            exp_start_date=None, exp_end_date=None, expense_limit=10, sales_start_date=None, sales_end_date=None)
            rev_inv = {x["s"]: D(x["r"]) for x in await c.fetch(f"""SELECT je.source_id s, SUM(jl.credit-jl.debit) r FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE {EFF}
                AND coa.account_type IN ('REVENUE','OTHER_INCOME') AND je.source_type IN ('INVOICE_REVENUE','INVOICE') GROUP BY 1""", t)}
            tot_inv = {x["id"]: D(x["total_amount"]) for x in await c.fetch("SELECT id, total_amount FROM sales_invoices WHERE tenant_id = $1", t)}
            al = await c.fetch("""SELECT a.invoice_id, a.amount_applied, rp.payment_date tgl FROM receive_payment_allocations a JOIN receive_payments rp
                ON rp.id=a.payment_id AND rp.tenant_id=$1 AND rp.status='posted' WHERE a.status='active'
                UNION ALL SELECT x.invoice_id, x.amount_applied, cd.deposit_date FROM customer_deposit_applications x JOIN customer_deposits cd
                ON cd.id=x.deposit_id AND cd.tenant_id=$1 AND cd.status<>'void' AND cd.journal_id IS NOT NULL WHERE x.tenant_id=$1 AND x.status='active'""", t)
            kas_rev = sum((Decimal(str(x["amount_applied"])) * rev_inv.get(x["invoice_id"], Decimal(0)) / tot_inv[x["invoice_id"]]
                           for x in al if m0 <= x["tgl"] <= hari and tot_inv.get(x["invoice_id"])), Decimal(0))
            lrc = ac["summary"]["laba_rugi"]
            cek(lrc is not None and lrc["basis"] == "cash" and int(lrc["pendapatan"]) == int(kas_rev) and ac["errors"] == {},
                f"{t} basis=cash (QBO) pendapatan bulan == hitung ulang per faktur (D1)", f"{lrc and lrc['pendapatan']} / {kas_rev:.2f}")
    finally:
        await pool.release(c); await pool.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
