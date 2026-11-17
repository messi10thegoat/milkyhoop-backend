"""BUG-259 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): services/lembar_hitung_stok.muat_lembar + render PDF ATAS klon (V502 sudah diterapkan), tenant kaos + nyawit. Baca-saja.
Per gudang tak-sistem: A bawaan: baris == SQL independen (barang goods dilacak aktif dgn >= 1 gerakan ledger di gudang itu sampai per_tanggal) dan tiap stok_buku == Σ(in-out) SQL
B sertakan_semua: baris == SQL semua barang dilacak aktif, bawaan <= semua
C per tanggal mundur (30 hari lalu): total baris bawaan <= total hari ini; stok per barang == SQL pada tanggal itu
D PDF: render_lembar_hitung_stok + tulis_pdf -> awalan %PDF, ukuran > 2000 byte (WeasyPrint nyata di image)
E waktu muat_lembar (p50/p95, 10 panggilan) pada gudang terbesar (target < 1000 ms)"""
import asyncio
import statistics
import sys
import time
from datetime import date, timedelta
from decimal import Decimal as D

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import warehouses as WH  # noqa: E402
from app.services import lembar_hitung_stok as LH  # noqa: E402
from app.services.pdf_service import get_pdf_service  # noqa: E402
from app.services.rekap_pesanan import muat_kop  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


SQL_BASE = ("/* tenant_id */ SELECT p.id, COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)) FILTER (WHERE il.movement_date <= $3), 0) AS stok, "
            "COUNT(il.id) FILTER (WHERE il.movement_date <= $3) AS n FROM products p LEFT JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = $1 AND il.warehouse_id = $2 "
            "WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND p.item_type = 'goods' AND p.track_inventory = true AND p.status = 'active' GROUP BY p.id")


async def main():
    pool = await WH.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            gud = await c.fetch("/* tenant_id */ SELECT id, name FROM warehouses WHERE tenant_id = $1 AND NOT is_system ORDER BY created_at", tid)
            cek(f"prasyarat {tid}: >= 1 gudang tak-sistem", len(gud) >= 1)
            besar, n_besar = None, -1
            hari_ini = date.today()
            for g in gud:
                gd = {"id": g["id"], "nama": g["name"]}
                sql = {str(r["id"]): (D(str(r["stok"])), r["n"]) for r in await c.fetch(SQL_BASE, tid, g["id"], hari_ini)}
                bawaan = {k: v[0] for k, v in sql.items() if v[1] > 0}
                a = await LH.muat_lembar(c, tid, gd, hari_ini)
                got = {b["item_id"]: D(str(b["stok_buku"])) for b in a["baris"]}
                cek(f"A {tid} {g['name']}: bawaan {a['total_baris']} baris == SQL {len(bawaan)}, stok sama", got == bawaan)
                b = await LH.muat_lembar(c, tid, gd, hari_ini, sertakan_semua=True)
                cek(f"B {tid} {g['name']}: sertakan_semua {b['total_baris']} == SQL semua barang dilacak aktif {len(sql)}; bawaan <= semua", b["total_baris"] == len(sql) and a["total_baris"] <= b["total_baris"])
                lalu = hari_ini - timedelta(days=30)
                sql_l = {str(r["id"]): (D(str(r["stok"])), r["n"]) for r in await c.fetch(SQL_BASE, tid, g["id"], lalu)}
                cl = await LH.muat_lembar(c, tid, gd, lalu)
                cek(f"C {tid} {g['name']}: per {lalu}: baris {cl['total_baris']} == SQL, stok sama", {x["item_id"]: D(str(x["stok_buku"])) for x in cl["baris"]} == {k: v[0] for k, v in sql_l.items() if v[1] > 0})
                if a["total_baris"] > n_besar:
                    besar, n_besar = g, a["total_baris"]
            if besar is not None:
                gd = {"id": besar["id"], "nama": besar["name"]}
                lembar = await LH.muat_lembar(c, tid, gd, hari_ini)
                pdf = get_pdf_service().tulis_pdf(get_pdf_service().render_lembar_hitung_stok(lembar, await muat_kop(c, tid)))
                cek(f"D {tid} {besar['name']}: PDF {len(pdf)} byte, awalan %PDF", pdf[:4] == b"%PDF" and len(pdf) > 2000)
                t = []
                for _ in range(10):
                    a0 = time.perf_counter()
                    await LH.muat_lembar(c, tid, gd, hari_ini)
                    t.append(time.perf_counter() - a0)
                t.sort()
                cek(f"E {tid} {besar['name']} ({n_besar} baris): p50 {statistics.median(t) * 1000:.0f} ms, p95 {t[8] * 1000:.0f} ms (target < 1000 ms)", t[8] < 1.0)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
