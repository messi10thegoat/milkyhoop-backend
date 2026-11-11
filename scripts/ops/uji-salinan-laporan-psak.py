"""Gelombang 4 L1 -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA (master) dan BARU: GET /api/reports/psak/financial-statements
per tenant (kaos, nyawit) x rentang (YTD hari ini, bulan lalu, YTD 30 Sep) x basis (akrual, kas). Baca saja.
 L (dua pohon) medan LAMA (float): lama == baru dibulatkan sen; selisih float persis dihitung (perbaikan Decimal boleh mengubah ekor float).
 P (baru) PARITAS JURNAL: tiap baris akun (account_id) == Σ(debit−kredit) journal_lines akun itu (POSTED, tenant, rentang) dengan tanda
   sajian seksi; Σ baris akun per seksi == total seksi; hasil (laba kotor/usaha/bersih, total aset/L+E) == medan lama; nilai_tampil ==
   teks_galat.angka_laporan(nilai, presisi tenant).
 T waktu GET (p50) lama vs baru."""
import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import psak_reports as PR  # noqa: E402

try:  # laporan-fix-2 (pohon BARU saja; pohon lama = master tanpa modul ini)
    from app.services import dashboard_angka as DA  # noqa: E402
    from app.services.report_engine import basis_kas as BK  # noqa: E402,F401
    from app.services.report_engine.footing import bulat as _bulat  # noqa: E402
    FIX2 = True
except ImportError:
    FIX2 = False
    _bulat = None

# ORAKEL independen basis kas: SQL total LAMA dasbor (22 Okt, live sebelum laporan-fix-2) + HPP faktur proporsional; struktur beda dari per-akun basis_kas.
BAYAR = """bayar AS (
        SELECT a.invoice_id, a.amount_applied, rp.payment_date AS tgl
        FROM receive_payment_allocations a JOIN receive_payments rp ON rp.id = a.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
        WHERE a.status = 'active'
        UNION ALL
        SELECT cda.invoice_id, cda.amount_applied, cd.deposit_date
        FROM customer_deposit_applications cda
        JOIN customer_deposits cd ON cd.id = cda.deposit_id AND cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
        WHERE cda.tenant_id = $1 AND cda.status = 'active')"""
ORA_PEND = f"""WITH rev AS (SELECT je.source_id AS inv, SUM(jl.credit - jl.debit) AS r
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
          AND c.account_type IN ('REVENUE', 'OTHER_INCOME') AND je.source_type IN ('INVOICE_REVENUE', 'INVOICE') GROUP BY je.source_id), {BAYAR}
    SELECT COALESCE(SUM(b.amount_applied * COALESCE(rev.r, 0) / NULLIF(si.total_amount, 0)), 0)
    FROM bayar b JOIN sales_invoices si ON si.id = b.invoice_id AND si.tenant_id = $1 LEFT JOIN rev ON rev.inv = si.id WHERE b.tgl BETWEEN $2 AND $3"""
ORA_HPP = f"""WITH cogs AS (SELECT je.source_id AS inv, SUM(jl.debit - jl.credit) AS h
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
          AND c.account_type = 'COGS' AND je.source_type IN ('INVOICE_FULFILLMENT', 'DELIVERY_INVOICED') GROUP BY je.source_id), {BAYAR}
    SELECT COALESCE(SUM(b.amount_applied * COALESCE(cogs.h, 0) / NULLIF(si.total_amount, 0)), 0)
    FROM bayar b JOIN sales_invoices si ON si.id = b.invoice_id AND si.tenant_id = $1 LEFT JOIN cogs ON cogs.inv = si.id WHERE b.tgl BETWEEN $2 AND $3"""
ORA_LANGSUNG = """SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
    FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
      AND c.account_type IN ('COGS', 'EXPENSE', 'OTHER_EXPENSE') AND je.journal_date BETWEEN $2 AND $3
      AND EXISTS (SELECT 1 FROM journal_lines k JOIN chart_of_accounts kc ON kc.id = k.account_id WHERE k.journal_id = je.id AND kc.is_cash)"""
ORA_TAGIHAN = """WITH beban_tagihan AS (SELECT je.source_id AS bill, SUM(jl.debit - jl.credit) AS beban
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND je.source_type = 'BILL'
          AND c.account_type IN ('COGS', 'EXPENSE', 'OTHER_EXPENSE') GROUP BY je.source_id)
    SELECT COALESCE(SUM(a.amount_applied * COALESCE(bt.beban, 0) / NULLIF(COALESCE(b.grand_total, b.amount), 0)), 0)
    FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id AND p.tenant_id = $1 AND p.status = 'posted'
    JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $1 LEFT JOIN beban_tagihan bt ON bt.bill = b.id WHERE p.payment_date BETWEEN $2 AND $3"""

# UKUR akar selisih kartu arus kas dasbor vs laporan: empat varian per tenant (bruto/neto per jurnal x POSTED/efektif), jurnal saldo awal dikecualikan
UKUR_KAS = """WITH kj AS (
        SELECT je.id, SUM(jl.debit) FILTER (WHERE c.is_cash) AS din, SUM(jl.credit) FILTER (WHERE c.is_cash) AS dout
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND (NOT $4::boolean OR is_effective_journal(je.id)) AND je.journal_date BETWEEN $2 AND $3
          AND je.source_type <> ALL($5::text[])
          AND EXISTS (SELECT 1 FROM journal_lines k JOIN chart_of_accounts kc ON kc.id = k.account_id WHERE k.journal_id = je.id AND NOT COALESCE(kc.is_cash, false))
        GROUP BY je.id)
    SELECT COALESCE(SUM(din), 0) AS bruto_masuk, COALESCE(SUM(dout), 0) AS bruto_keluar,
           COALESCE(SUM(GREATEST(COALESCE(din, 0) - COALESCE(dout, 0), 0)), 0) AS neto_masuk,
           COALESCE(SUM(GREATEST(COALESCE(dout, 0) - COALESCE(din, 0), 0)), 0) AS neto_keluar FROM kj"""
SALDO_AWAL = ["OPENING", "OPENING_BALANCE", "BANK_OPENING_BALANCE", "VENDOR_OPENING_BALANCE"]
# ANOMALI data lama yang membuat rekonsiliasi arus kas (kas awal + aliran EFEKTIF vs kas akhir POSTED) selisih: jurnal PEMBALIK berstatus POSTED yang ASLINYA tidak POSTED
# (legacy: asli dibalik jadi VOID, melanggar Law 2). Saldo POSTED memuat pembaliknya, aliran efektif membuangnya. Hitung per jurnal (n) + neto sisi kas.
ANOM = """SELECT COALESCE(SUM(jl.debit - jl.credit), 0) AS net, COUNT(DISTINCT je.id) AS n
    FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id AND c.is_cash
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.journal_date BETWEEN $2 AND $3 AND je.reversal_of_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM journal_entries o WHERE o.id = je.reversal_of_id AND o.tenant_id = je.tenant_id AND o.status = 'POSTED')
      AND je.source_type <> ALL($4::text[])"""
ANOM_SEMUA = """SELECT COUNT(*) AS n FROM journal_entries je
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.reversal_of_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM journal_entries o WHERE o.id = je.reversal_of_id AND o.tenant_id = je.tenant_id AND o.status = 'POSTED')"""

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(PR, "_tampil")
CEK = []
# tanda sajian per seksi (nilai baris akun = −saldo bila True)
NEGASI = {"pendapatan": True, "hpp": False, "beban_usaha": False, "lain": True, "aset_lancar": False, "aset_tidak_lancar": False,
          "liabilitas_jp": True, "liabilitas_jpp": True, "ekuitas": True}


TOTAL_SEKSI = {"pendapatan": "total_pendapatan", "hpp": "total_hpp", "beban_usaha": "total_beban_usaha", "lain": "total_lain",
               "aset_lancar": "total_aset_lancar", "aset_tidak_lancar": "total_aset_tidak_lancar", "liabilitas_jp": "total_liabilitas_jp",
               "liabilitas_jpp": "total_liabilitas_jpp", "ekuitas": "total_ekuitas"}


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:500]])


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "role": "OWNER"}
    return r


def rentang():
    h = date.today()
    awal_bulan = h.replace(day=1)
    akhir_lalu = awal_bulan - timedelta(days=1)
    return [(f"{h.year}-01-01", h.isoformat()), (akhir_lalu.replace(day=1).isoformat(), akhir_lalu.isoformat()),
            (f"{h.year}-01-01", f"{h.year}-09-30")]


def lama_saja(lap: dict) -> dict:
    return {k: v for k, v in lap.items() if k not in ("judul", "periode_text", "periode_rentang_text", "basis_text", "satuan_text", "baris")}


async def saldo(conn, tid, aid, mulai, akhir):
    return Decimal(str(await conn.fetchval(
        """SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
           WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND jl.account_id = $2::uuid
             AND ($3::date IS NULL OR je.journal_date >= $3::date) AND je.journal_date <= $4::date""",
        tid, aid, None if mulai is None else date.fromisoformat(mulai), date.fromisoformat(akhir)) or 0))


async def paritas(conn, tid, nama, lap, mulai, akhir, presisi):
    from app.services import teks_galat as tg
    seksi, jumlah, total = None, {}, {}
    for b in lap["baris"]:
        if b["jenis"] == "judul":
            seksi = b["kunci"]
            continue
        cek(f"P {nama} nilai_tampil", b["nilai_tampil"] == tg.angka_laporan(b["nilai"], presisi), b)
        if b["jenis"] == "akun" and b.get("account_id"):
            s = await saldo(conn, tid, b["account_id"], mulai, akhir)
            harap = -s if NEGASI.get(seksi) else s
            cek(f"P {nama} akun {b['account_code']} == jurnal", Decimal(b["nilai"]) == (_bulat(harap, presisi) if FIX2 else harap.quantize(Decimal("0.01"))),
                {"baris": b, "jurnal": str(s), "seksi": seksi})
        if b["jenis"] == "akun":
            jumlah[seksi] = jumlah.get(seksi, Decimal("0")) + Decimal(b["nilai"])
        elif b["jenis"] == "total" and seksi in NEGASI and b["kunci"] == TOTAL_SEKSI.get(seksi):  # "Total Liabilitas" bukan total seksi
            total[seksi] = Decimal(b["nilai"])
    for s, t in total.items():
        cek(f"P {nama} Σ akun seksi {s} == total", jumlah.get(s, Decimal("0")) == t, {"jumlah": str(jumlah.get(s)), "total": str(t)})


def _q(x):
    return Decimal(str(x or 0))


async def fix2(conn, tid, k, basis, mulai, akhir, presisi, pl, bs, cf, nb, lb, tol_pl, ak):
    """laporan-fix-2: (b) total tampil == Σ baris tampil di rantai L/R + neraca seimbang di tampilan; basis kas = dasbor (SATU sumber) == orakel lama
    (pendapatan) dan orakel lama + HPP faktur (beban); ukur arus kas dasbor vs laporan + baris uang muka (c)(d)."""
    d1, d2 = date.fromisoformat(mulai), date.fromisoformat(akhir)
    cek(f"B {k} L/R laba bersih tampil == Σ rantai tampil", lb["laba_bersih"] == lb["total_pendapatan"] - lb["total_hpp"] - lb["total_beban_usaha"] + lb["total_lain"],
        lb)
    cek(f"B {k} neraca tampil seimbang (total aset == total L+E)", nb["total_aset"] == nb["total_liabilitas_ekuitas"], (nb["total_aset"], nb["total_liabilitas_ekuitas"]))
    if basis == "cash":
        kas = await DA.laba_rugi_kas(conn, tid, d1, d2)
        n = sum(1 for b in pl["baris"] if b["jenis"] == "akun") + 1
        pend = _q(pl["pendapatan"]["total"]) + _q(pl["pendapatan_lain"]["total"])
        beban = _q(pl["hpp"]["total"]) + _q(pl["beban_usaha"]["total"]) + _q(pl["beban_lain"]["total"])
        cek(f"K {k} L/R PSAK kas == dasbor laba_rugi_kas (pendapatan, beban, laba)", abs(pend - kas["pendapatan"]) < Decimal("0.02") and abs(beban - kas["beban"]) <
            Decimal("0.02") and abs(_q(pl["laba_bersih"]) - kas["laba"]) < Decimal("0.02"), (pend, beban, pl["laba_bersih"], kas))
        o_p = _q(await conn.fetchval(ORA_PEND, tid, d1, d2))
        o_h = _q(await conn.fetchval(ORA_HPP, tid, d1, d2))
        o_b = _q(await conn.fetchval(ORA_LANGSUNG, tid, d1, d2)) + _q(await conn.fetchval(ORA_TAGIHAN, tid, d1, d2))
        tol = Decimal("0.005") * n + Decimal("0.01")
        cek(f"K {k} pendapatan == orakel dasbor LAMA", abs(kas["pendapatan"] - o_p) <= tol, (kas["pendapatan"], o_p))
        cek(f"K {k} beban == orakel LAMA + HPP faktur proporsional", abs(kas["beban"] - (o_b + o_h)) <= tol, (kas["beban"], o_b, o_h))
        print(f"[ukur] {k} kas: pendapatan {kas['pendapatan']} (orakel lama {o_p}) | HPP faktur proporsional {o_h} | beban lama {o_b} | beban baru {kas['beban']}", flush=True)
    if basis == "accrual" and mulai == f"{akhir[:4]}-01-01":
        ph = await DA.arus_kas_harian(conn, tid, d1, d2)
        masuk, keluar = sum((v[0] for v in ph.values()), Decimal(0)), sum((v[1] for v in ph.values()), Decimal(0))
        pos = sum((_q(b["nilai"]) for b in cf["baris"] if b["kunci"] == "butir" and _q(b["nilai"]) > 0), Decimal(0))
        neg = -sum((_q(b["nilai"]) for b in cf["baris"] if b["kunci"] == "butir" and _q(b["nilai"]) < 0), Decimal(0))
        butir = [(b["label"], b["nilai"]) for b in cf["baris"] if b["kunci"] == "butir"]
        print(f"[ukur] {tid} arus kas YTD: dasbor masuk/keluar {masuk}/{keluar} | laporan Σ butir + / − {pos}/{neg} | selisih {masuk - pos}/{keluar - neg}", flush=True)
        print(f"[ukur] {tid} butir arus kas: {butir}", flush=True)
        for efektif in (False, True):
            u = await conn.fetchrow(UKUR_KAS, tid, d1, d2, efektif, SALDO_AWAL)
            print(f"[ukur2] {tid} YTD {'EFEKTIF' if efektif else 'POSTED '}: bruto masuk/keluar {u['bruto_masuk']}/{u['bruto_keluar']} | neto-per-jurnal masuk/keluar "
                  f"{u['neto_masuk']}/{u['neto_keluar']}", flush=True)
        print(f"[ukur2] {tid} YTD dasbor {masuk}/{keluar} | laporan Σ butir {pos}/{neg}", flush=True)
        # SATU definisi (laporan-fix-2): Σ butir masuk/keluar laporan == kartu dasbor (kedua efektif, butir dipecah per arah) -- TANPA pengecualian tenant
        items = [v for a in ("operasi", "investasi", "pendanaan") for v in cf[a]["items"]]
        pos_f = sum((_q(v["amount"]) for v in items if v["amount"] > 0), Decimal(0))
        neg_f = -sum((_q(v["amount"]) for v in items if v["amount"] < 0), Decimal(0))
        tolk = Decimal("0.005") * len(items) + Decimal("0.02")
        cek(f"A {tid} arus kas Σ butir masuk/keluar == dasbor", abs(pos_f - masuk) <= tolk and abs(neg_f - keluar) <= tolk, (pos_f, neg_f, masuk, keluar))
        # rekonsiliasi: selisih (kas akhir - kas awal - aliran) harus == neto kas jurnal pembalik-tanpa-asli (anomali data lama); tanpa anomali -> 0
        an = await conn.fetchrow(ANOM, tid, d1, d2, SALDO_AWAL)
        resid = _q(cf["kas_akhir"]) - _q(cf["kas_awal"]) - _q(cf["kenaikan_kas_bersih"])
        cek(f"A {tid} selisih rekonsiliasi arus kas == jurnal pembalik-tanpa-asli", abs(resid - an["net"]) <= Decimal("0.02"), (resid, an["net"], an["n"]))
        print(f"[ukur3] {tid} YTD: selisih rekonsiliasi {resid} | jurnal pembalik-POSTED-tanpa-asli bersisi kas di periode {an['n']} (neto {an['net']}) | "
              f"semua periode {await conn.fetchval(ANOM_SEMUA, tid)}", flush=True)


async def main():
    from app.services.uang_presisi import presisi_tenant
    pool = await PR.get_pool() if BARU else None
    out = {"pohon": "baru" if BARU else "lama", "lama": {}, "waktu": [], "gap": {}, "fix2": FIX2}
    for tid in TENANT:
        for mulai, akhir in rentang():
            for basis in ("accrual", "cash"):
                k = f"{tid}|{mulai}|{akhir}|{basis}"
                t0 = time.perf_counter()
                r = await PR.get_financial_statements(req(tid), as_of=akhir, period_start=mulai, basis=basis)
                out["waktu"].append((time.perf_counter() - t0) * 1000)
                out["lama"][k] = {n: lama_saja(r[n]) for n in ("balance_sheet", "income_statement", "cash_flow")} | \
                    {"validation_errors": r["validation_errors"]}
                bsl = r["balance_sheet"]
                out["gap"][k] = str(Decimal(str(bsl["total_aset"])).quantize(Decimal("0.01")) - Decimal(str(bsl["total_liabilitas_ekuitas"])).quantize(Decimal("0.01")))
                if not BARU:
                    continue
                async with pool.acquire() as conn:
                    presisi = await presisi_tenant(conn, tid)
                    bs, pl, cf = r["balance_sheet"], r["income_statement"], r["cash_flow"]
                    lb = {b["kunci"]: Decimal(b["nilai"]) for b in pl["baris"] if b["jenis"] in ("hasil", "total")}
                    satuan = Decimal("0.5") if presisi == 0 else Decimal("0.005")   # footing: tiap baris akun bergeser <= setengah satuan
                    tol_pl = satuan * sum(1 for b in pl["baris"] if b["jenis"] == "akun") + Decimal("0.01")
                    tol_bs = satuan * sum(1 for b in bs["baris"] if b["jenis"] == "akun") + Decimal("0.01")
                    cek(f"H {k} laba bersih == lama (± pembulatan per baris)", abs(lb["laba_bersih"] - Decimal(str(pl["laba_bersih"]))) <= tol_pl,
                        (lb["laba_bersih"], pl["laba_bersih"]))
                    nb = {b["kunci"]: Decimal(b["nilai"]) for b in bs["baris"] if b["kunci"] != "akun" and b["nilai"] is not None}
                    cek(f"H {k} total aset & L+E == lama (± pembulatan per baris)", abs(nb["total_aset"] - Decimal(str(bs["total_aset"]))) <= tol_bs and
                        abs(nb["total_liabilitas_ekuitas"] - Decimal(str(bs["total_liabilitas_ekuitas"]))) <= tol_bs, nb)
                    if basis == "accrual" and mulai == f"{akhir[:4]}-01-01":  # sebanding (laporan-fix-1: neraca = laba tahun berjalan akrual)
                        cek(f"H {k} laba tahun berjalan neraca == laba bersih L/R", abs(nb["laba_periode"] - lb["laba_bersih"]) <= tol_pl + tol_bs, (nb["laba_periode"], lb["laba_bersih"]))
                    cek(f"N {k} neraca BALANCE (aset == L+E; basis/period_start tak mengubah)", abs(bs["total_aset"] - bs["total_liabilitas_ekuitas"]) < 0.01 and bs["_balanced"],
                        (bs["total_aset"], bs["total_liabilitas_ekuitas"]))
                    if "laba_ditahan_lalu" in bs["ekuitas"]:  # hanya pohon BARU (pohon lama = master tanpa medan ini)
                      cek(f"N {k} ekuitas = buku + laba lalu + laba tahun berjalan", abs(bs["ekuitas"]["laba_ditahan_lalu"] + bs["ekuitas"]["laba_periode"] - (bs["ekuitas"]["total"] - sum(
                        x["balance"] for s in ("modal_disetor", "saldo_laba", "lainnya") for x in bs["ekuitas"][s]["akun"]))) < 0.02, bs["ekuitas"])
                    if basis == "accrual":  # basis kas memakai saringan jurnal kas (bukan Σ saldo akun langsung)
                        await paritas(conn, tid, f"{k} L/R", pl, mulai, akhir, presisi)
                        await paritas(conn, tid, f"{k} Neraca", bs, None, akhir, presisi)
                    for b in cf["baris"]:
                        if b["nilai"] is not None:
                            from app.services import teks_galat as tg
                            cek(f"P {k} A/K nilai_tampil", b["nilai_tampil"] == tg.angka_laporan(b["nilai"], presisi), b)
                    ak = {b["kunci"]: Decimal(b["nilai"]) for b in cf["baris"] if b["nilai"] is not None and b["kunci"] != "butir"}
                    cek(f"H {k} A/K kas awal + kenaikan == kas akhir (bila rekonsiliasi lama benar)",
                        not cf["_reconciled"] or abs(ak["kas_awal"] + ak["kenaikan_kas"] - ak["kas_akhir"]) < (Decimal("1.6") if presisi == 0 else Decimal("0.03")), ak)
                    if FIX2:
                        await fix2(conn, tid, k, basis, mulai, akhir, presisi, pl, bs, cf, nb, lb, tol_pl, ak)
    out["cek"] = CEK
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:40]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; laporan {len(out['lama'])}", flush=True)


asyncio.run(main())
