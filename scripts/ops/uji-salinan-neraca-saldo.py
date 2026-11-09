"""Gelombang 4 L3 -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA dan BARU: GET /api/reports/trial-balance (+ /full) untuk
kaos + nyawit. Angka SENGAJA berubah (dulu baris VOID/DRAFT/masa depan ikut); yang dibuktikan (syarat MASTER 10 Nov):
 S  selisih lama − baru (Σ debit & Σ kredit) == Σ baris yang lama salah hitung PERSIS: baris jurnal non-POSTED / tanggal > as_of pada
    akun AKTIF tenant (dulu ikut) dikurangi baris POSTED <= as_of pada akun NONAKTIF (dulu dibuang; BL-07).
 P  baru per akun == Σ(debit, kredit) journal_lines POSTED <= as_of (buku besar), semua akun bersaldo, termasuk nonaktif.
 F  baru == /trial-balance/full (closing, periode fiskal yang berakhir pada as_of; PERSIS per sen -- tiket kecil: /full tak lagi int()).
 N  akun nonaktif bersaldo ikut: klon BARU menonaktifkan satu akun bersaldo -> tetap tampil, total tetap seimbang.
 T  as_of dan as_of_date sama-sama dihormati; tanpa keduanya = tanggal bisnis tenant.
 Z  /trial-balance/full lama vs baru SEL PER SEL (periode terbuka bawaan + periode fiskal terakhir yang lewat): tiap sel uang yang berubah
    didaftar (akun, kolom, lama, baru); syarat MASTER: |baru − lama| < 1 dan lama == baru dipotong ke nol (persis efek int() lama).
 X  teks: tipe_label/saldo_normal_label = SATU peta; debit/kredit_tampil = angka_laporan; status_text konsisten dengan selisih."""
import asyncio
import inspect
import json
import os
import sys
from datetime import date
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import reports as RP  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = "as_of_date" in inspect.signature(RP.get_trial_balance).parameters
CEK = []
NOL = Decimal("0")


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:500]])


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "role": "OWNER"}
    return r


def d(x):
    return Decimal(str(x or 0))


async def panggil(tid, **kw):
    h = await RP.get_trial_balance(req(tid), **kw)
    return json.loads(h.model_dump_json()) if hasattr(h, "model_dump_json") else h


async def tanggal_uji(conn, tid):
    """(akhir periode fiskal terakhir yang sudah lewat, untuk F) + tanggal bisnis hari ini."""
    from app.utils.tanggal_tenant import tanggal_dokumen
    hari = await tanggal_dokumen(conn, tid)
    p = await conn.fetchrow("""SELECT id, end_date FROM fiscal_periods WHERE tenant_id = $1 AND end_date < $2
                               ORDER BY end_date DESC LIMIT 1""", tid, hari)
    return hari, p


async def salah_lama(conn, tid, per):
    """Σ baris yang dihitung lama tapi bukan buku (non-POSTED / masa depan, akun aktif) dan Σ yang dibuang lama (POSTED, akun nonaktif)."""
    lebih = await conn.fetchrow(
        """SELECT COALESCE(SUM(jl.debit), 0) d, COALESCE(SUM(jl.credit), 0) k, count(*) n
           FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
           WHERE coa.tenant_id = $1 AND coa.is_active AND (je.tenant_id <> $1 OR je.status <> 'POSTED' OR je.journal_date > $2)""", tid, per)
    kurang = await conn.fetchrow(
        """SELECT COALESCE(SUM(jl.debit), 0) d, COALESCE(SUM(jl.credit), 0) k, count(*) n
           FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
           WHERE coa.tenant_id = $1 AND je.tenant_id = $1 AND NOT coa.is_active AND je.status = 'POSTED' AND je.journal_date <= $2""", tid, per)
    return lebih, kurang


async def buku(conn, tid, per):
    rows = await conn.fetch(
        """SELECT jl.account_id::text a, SUM(jl.debit) d, SUM(jl.credit) k FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
           WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.journal_date <= $2 GROUP BY 1
           HAVING SUM(jl.debit) <> 0 OR SUM(jl.credit) <> 0""", tid, per)
    return {r["a"]: (d(r["d"]), d(r["k"])) for r in rows}


async def periksa_baru(conn, tid, k, h, per, presisi):
    from app.services import teks_galat as tg
    from app.services.report_engine import baris as B
    bk = await buku(conn, tid, per)
    akun = {a["account_id"]: a for a in h["accounts"]}
    cek(f"P {k} himpunan akun == buku besar", set(akun) == set(bk), (set(akun) ^ set(bk)))
    for aid, (bd, bkr) in bk.items():
        a = akun.get(aid)
        if a:
            cek(f"P {k} akun {a['account_code']} == jurnal", (d(a["total_debit"]), d(a["total_credit"])) == (bd, bkr), (a, bd, bkr))
            neto = bd - bkr
            cek(f"X {k} kolom saldo {a['account_code']}", (d(a["debit_saldo"]), d(a["kredit_saldo"])) == ((neto, NOL) if neto > 0 else (NOL, -neto)), a)
            cek(f"X {k} tampil {a['account_code']}", a["debit_tampil"] == ("" if d(a["debit_saldo"]) == 0 else tg.angka_laporan(a["debit_saldo"], presisi))
                and a["kredit_tampil"] == ("" if d(a["kredit_saldo"]) == 0 else tg.angka_laporan(a["kredit_saldo"], presisi)), a)
            cek(f"X {k} label {a['account_code']}", a["tipe_label"] == B.tipe_label(a["account_type"])
                and a["saldo_normal_label"] == B.saldo_normal_label(a["normal_balance"]), a)
    sel = d(h["selisih"])
    cek(f"X {k} status_text", h["status_text"] == ("Seimbang" if sel == 0 else f"Tidak seimbang — selisih {tg.angka_laporan(abs(sel), presisi)}"), h["status_text"])
    cek(f"X {k} seimbang", sel == 0 and h["is_balanced"], (sel, h["is_balanced"]))
    cek(f"X {k} per_tanggal", h["per_tanggal"] == per.isoformat() and h["as_of_date"] == per.isoformat(), h["per_tanggal"])


KOLOM_FULL = ("opening_debit", "opening_credit", "period_debit", "period_credit", "closing_debit", "closing_credit")


async def sel_full(tid, pid):
    """Semua sel uang /full (akun bukan kepala + ringkasan) -> {kunci: str(nilai)}; dijalankan di KEDUA pohon."""
    f = await RP.get_trial_balance_full(req(tid), period_id=pid, show_zero_balance=False, group_by_type=True)
    f = f["data"] if isinstance(f, dict) else f.data.model_dump()
    sel = {f"{tid}|{pid}|{a['account_code']}|{k}": str(a[k]) for a in f["accounts"] if not a["is_header"] for k in KOLOM_FULL}
    sel |= {f"{tid}|{pid}|RINGKASAN|{k}": str(v) for k, v in f["summary"].items() if k.startswith("total_")}
    return sel


async def main():
    from app.services.uang_presisi import presisi_tenant
    pool = await RP.get_pool()
    out = {"pohon": "baru" if BARU else "lama", "total": {}}
    async with pool.acquire() as conn:
        for tid in TENANT:
            hari, p = await tanggal_uji(conn, tid)
            for pid in (None, str(p["id"]) if p else None):
                out.setdefault("full", {}).update(await sel_full(tid, pid))
            presisi = await presisi_tenant(conn, tid)
            daftar = [hari] + ([p["end_date"]] if p else [])
            for per in daftar:
                k = f"{tid}|{per}"
                h = await panggil(tid, as_of=per)
                out["total"][k] = [h["total_debit"], h["total_credit"]]
                if not BARU:
                    out.setdefault("salah", {})[k] = [[str(x) for x in r.values()] for r in await salah_lama(conn, tid, per)]
                    continue
                await periksa_baru(conn, tid, k, h, per, presisi)
                h2 = await panggil(tid, as_of_date=per)
                cek(f"T {k} as_of_date == as_of", h2 == h, "beda")
            if BARU:
                h0 = await panggil(tid)
                cek(f"T {tid} bawaan = tanggal bisnis", h0["per_tanggal"] == hari.isoformat(), (h0["per_tanggal"], hari))
                if p:  # F: /full closing pada periode fiskal yang berakhir p.end_date
                    h = await panggil(tid, as_of=p["end_date"])
                    f = (await RP.get_trial_balance_full(req(tid), period_id=str(p["id"]), show_zero_balance=False, group_by_type=True))
                    f = f["data"] if isinstance(f, dict) else f.data.model_dump()
                    fa = {x["id"]: x for x in f["accounts"] if not x["is_header"]}
                    for a in h["accounts"]:
                        x = fa.get(a["account_id"])
                        cek(f"F {tid}|{p['end_date']} {a['account_code']} == /full", x is not None and
                            (d(x["closing_debit"]), d(x["closing_credit"])) == (d(a["total_debit"]), d(a["total_credit"])), (a, x))
                        if x:
                            cek(f"X {tid} /full label {a['account_code']}", (x["tipe_label"], x["saldo_normal_label"]) == (a["tipe_label"], a["saldo_normal_label"]), x)
                # N: nonaktifkan satu akun bersaldo DI KLON (tulis hanya di salinan) -> tetap tampil, total tetap
                a = h0["accounts"][0] if h0["accounts"] else None
                if a:
                    await conn.execute("UPDATE chart_of_accounts SET is_active = false WHERE id = $1::uuid AND tenant_id = $2", a["account_id"], tid)
                    hn = await panggil(tid)
                    await conn.execute("UPDATE chart_of_accounts SET is_active = true WHERE id = $1::uuid AND tenant_id = $2", a["account_id"], tid)
                    cek(f"N {tid} akun nonaktif bersaldo ikut", any(x["account_id"] == a["account_id"] for x in hn["accounts"])
                        and hn["total_debit"] == h0["total_debit"] and hn["status_text"] == "Seimbang", hn["status_text"])
    out["cek"] = CEK
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:40]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; laporan {len(out['total'])}", flush=True)


asyncio.run(main())
