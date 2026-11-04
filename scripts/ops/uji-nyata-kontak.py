"""Uji nyata BACA SAJA Pelanggan + Vendor B1/B2/B4 (4 Nov 2026) di kaos. MODE=LAMA (pohon utama) menulis keluaran, MODE=BARU
(worktree) membandingkan. Koneksi default_transaction_read_only=on. Tenant tak ada = GAGAL. MERAH=aksi: predikat aksi dirusak ->
BARU WAJIB merah.
  K1 daftar tanpa parameter baru: id + urutan + total == LAMA; saldo (outstanding_balance / ap_balance) == LAMA secara angka.
  K2 ?aksi=k: setiap baris punya k di aksi_semua; total == summary k_count; with_totals sum_sisa == summary total_value (filter sama).
  K3 detail: aksi ada 5 kunci (nonaktifkan|aktifkan), hapus.boleh == (DELETE akan lolos) diukur dari cek_jejak; saldo == daftar.
  K4 history: jumlah event == jumlah /activity (OWNER: nol omitted) dan urut terbaru dulu."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import asyncpg

sys.path.insert(0, "/app/backend/api_gateway")
T = "kaos-biru-konveksi"
MODE, OUT, MERAH = os.environ["MODE"], "/out", os.environ.get("MERAH", "")
gagal = []


def cek(ok, pesan, rinci=""):
    print(("PASS " if ok else "GAGAL ") + pesan + (f" | {rinci}" if rinci else ""))
    if not ok:
        gagal.append(pesan)


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                     server_settings={"default_transaction_read_only": "on"})
    from app.routers import customers as CU
    from app.routers import vendors as VE

    async def _pool():
        return pool
    CU.get_pool = _pool
    VE.get_pool = _pool
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": None, "role": "OWNER"}), headers={})
    async with pool.acquire() as c:
        cek(await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T) == 1, "tenant kaos ada")
    hasil, lama = {}, (json.load(open(f"{OUT}/lama.json")) if MODE == "BARU" else {})
    dasar = dict(skip=0, limit=100, search=None, is_active=None, sort_order="desc")
    cu = await CU.list_customers(req, tipe=None, sort_by="created_at", **dasar, **({} if MODE == "LAMA" else {"with_totals": False, "aksi": None}))
    ve = await VE.list_vendors(req, has_balance=None, is_pkp=None, has_overdue=None, sort_by="created_at", **dasar,
                               **({} if MODE == "LAMA" else {"with_totals": False, "aksi": None}))
    hasil["cu"] = {"total": cu["total"], "baris": [[i["id"], float(i["outstanding_balance"] or 0)] for i in cu["items"]]}
    hasil["ve"] = {"total": ve["total"], "baris": [[i["id"], float(i["ap_balance"] or 0)] for i in ve["items"]]}
    if MODE == "LAMA":
        json.dump(hasil, open(f"{OUT}/lama.json", "w"))
        print("LAMA ditulis"); await pool.close(); return 0
    for k in ("cu", "ve"):
        cek(hasil[k] == lama[k], f"K1 {k}: daftar tanpa parameter baru == LAMA (id, urutan, total, saldo)",
            f"{hasil[k]['total']} baris" if hasil[k] == lama[k] else f"lama {lama[k]['total']} baru {hasil[k]['total']}")
    if MERAH == "aksi":
        from app.services import kontak_daftar as KD
        KD.sql_aksi = lambda jenis, aksi: "TRUE"  # sabotase: predikat aksi tak menyaring
    for mod, jenis, aksi_daftar, fn, sm in ((CU, "customer", ("lewat_jatuh_tempo", "punya_piutang"), CU.list_customers, CU.customers_summary),
                                           (VE, "vendor", ("lewat_jatuh_tempo", "punya_hutang"), VE.list_vendors, VE.vendors_summary)):
        extra = {"tipe": None} if jenis == "customer" else {"has_balance": None, "is_pkp": None, "has_overdue": None}
        sm_extra = {"tipe": None} if jenis == "customer" else {"is_pkp": None, "has_overdue": None}
        ring = (await sm(req, search=None, is_active=None, **sm_extra))["data"]
        semua = await fn(req, sort_by="created_at", with_totals=True, aksi=None, **dasar, **extra)
        cek(semua["total"] == ring["total"] and float(semua["sum_sisa"]) == float(ring["total_value"]),
            f"K2 {jenis}: with_totals total/sum_sisa == summary", f"{semua['total']}/{semua['sum_sisa']} vs {ring['total']}/{ring['total_value']}")
        for a in aksi_daftar:
            r = await fn(req, sort_by="created_at", with_totals=True, aksi=a, **dasar, **extra)
            ok = r["total"] == ring[f"{a}_count"] and all(a in i["aksi_semua"] and i["aksi"] == i["aksi_semua"][0] for i in r["items"])
            cek(ok, f"K2 {jenis} ?aksi={a}: total == {a}_count dan baris memuat aksi", f"{r['total']} vs {ring[a + '_count']}")
        # K3 + K4 untuk 5 kontak pertama
        for i in semua["items"][:5]:
            if jenis == "customer":
                d = (await CU.get_customer(req, i["id"]))["data"]
                h = (await CU.get_customer_history(req, i["id"], limit=500))["data"]
                act = await CU.get_customer_activity(req, i["id"], limit=200, offset=0)
            else:
                from uuid import UUID
                d = (await VE.get_vendor(req, UUID(i["id"])))["data"]
                h = (await VE.get_vendor_history(req, UUID(i["id"]), limit=500))["data"]
                act = await VE.get_vendor_activity(req, UUID(i["id"]), limit=200, offset=0)
            d = d if isinstance(d, dict) else d.model_dump()
            kunci = [a["kunci"] for a in d["aksi"]]
            cek(len(kunci) == 5 and kunci[0] == "ubah" and kunci[-1] == "hapus" and float(d["saldo"]) == float(i["saldo"]),
                f"K3 {jenis} {i['name']}: detail aksi 5 + saldo == daftar", str(kunci))
            n_act = act.total if hasattr(act, "total") else act["total"]
            at = [e["at"] for e in h["events"]]
            cek(len(h["events"]) == min(n_act, 500) and at == sorted(at, reverse=True) and not h.get("omitted"),
                f"K4 {jenis} {i['name']}: history {len(h['events'])} == activity {n_act}, terbaru dulu")
    await pool.close()
    print(f"SELESAI BARU: {len(gagal)} gagal")
    return 1 if gagal else 0


sys.exit(asyncio.run(main()))
