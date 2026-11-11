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
    return {k: v for k, v in lap.items() if k not in ("judul", "periode_text", "basis_text", "satuan_text", "baris")}


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
            cek(f"P {nama} akun {b['account_code']} == jurnal", Decimal(b["nilai"]) == harap.quantize(Decimal("0.01")),
                {"baris": b, "jurnal": str(s), "seksi": seksi})
        if b["jenis"] == "akun":
            jumlah[seksi] = jumlah.get(seksi, Decimal("0")) + Decimal(b["nilai"])
        elif b["jenis"] == "total" and seksi in NEGASI and b["kunci"] == TOTAL_SEKSI.get(seksi):  # "Total Liabilitas" bukan total seksi
            total[seksi] = Decimal(b["nilai"])
    for s, t in total.items():
        cek(f"P {nama} Σ akun seksi {s} == total", jumlah.get(s, Decimal("0")) == t, {"jumlah": str(jumlah.get(s)), "total": str(t)})


async def main():
    from app.services.uang_presisi import presisi_tenant
    pool = await PR.get_pool() if BARU else None
    out = {"pohon": "baru" if BARU else "lama", "lama": {}, "waktu": [], "gap": {}}
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
                    cek(f"H {k} laba bersih == lama", abs(float(lb["laba_bersih"]) - pl["laba_bersih"]) < 0.005, (lb["laba_bersih"], pl["laba_bersih"]))
                    nb = {b["kunci"]: Decimal(b["nilai"]) for b in bs["baris"] if b["kunci"] != "akun" and b["nilai"] is not None}
                    cek(f"H {k} total aset & L+E == lama", abs(float(nb["total_aset"]) - bs["total_aset"]) < 0.005 and
                        abs(float(nb["total_liabilitas_ekuitas"]) - bs["total_liabilitas_ekuitas"]) < 0.005, nb)
                    if basis == "accrual" and mulai == f"{akhir[:4]}-01-01":  # sebanding (laporan-fix-1: neraca = laba tahun berjalan akrual)
                        cek(f"H {k} laba tahun berjalan neraca == laba bersih L/R", nb["laba_periode"] == lb["laba_bersih"], (nb["laba_periode"], lb["laba_bersih"]))
                    cek(f"N {k} neraca BALANCE (aset == L+E; basis/period_start tak mengubah)", abs(bs["total_aset"] - bs["total_liabilitas_ekuitas"]) < 0.01 and bs["_balanced"],
                        (bs["total_aset"], bs["total_liabilitas_ekuitas"]))
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
                        not cf["_reconciled"] or abs(ak["kas_awal"] + ak["kenaikan_kas"] - ak["kas_akhir"]) < Decimal("0.02"), ak)
    out["cek"] = CEK
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:40]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; laporan {len(out['lama'])}", flush=True)


asyncio.run(main())
