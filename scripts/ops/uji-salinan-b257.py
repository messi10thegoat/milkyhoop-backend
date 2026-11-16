"""BUG-257 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): services/histori_harga_pelanggan.riwayat ATAS klon, tenant kaos + nyawit. Baca-saja.
Untuk tiap pasangan (pelanggan, barang) yang punya baris faktur terposting (sampai 5 pasangan per tenant, termasuk satu dgn konversi satuan bila ada):
A total == hitungan SQL independen (faktur status NOT IN draft/void, bukan saldo awal), items == min(total, limit), urut tanggal turun
B baris pertama harga/qty == baris SQL independen terbaru (tanpa unit = satuan asli)
C exclude_doc_id = dokumen terbaru -> total turun tepat jumlah baris barang itu di dokumen tsb, dan dokumen itu tak ada di items
D konversi satuan (barang dgn unit_conversions): harga_t x qty_t == harga x qty baris asal (nilai uang tak berubah, toleransi pembulatan 0,01 x qty_t)
E tak ada draf/void di hasil: setiap doc_id di items berstatus bukan draft/void di DB
F waktu: p50/p95 dari 30 panggilan pada pasangan dgn riwayat terbanyak (target < 300 ms)"""
import asyncio
import statistics
import sys
import time
from decimal import Decimal as D

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import customers as CU  # noqa: E402
from app.services import histori_harga_pelanggan as HH  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await CU.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            pas = await c.fetch(
                "/* tenant_id */ SELECT si.customer_id, sii.item_id, count(*) AS n FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id "
                "WHERE si.tenant_id = $1 AND si.customer_id IS NOT NULL AND sii.item_id IS NOT NULL AND si.status NOT IN ('draft', 'void') AND NOT si.is_opening_balance "
                "GROUP BY 1, 2 ORDER BY n DESC LIMIT 5", tid)
            cek(f"prasyarat {tid}: ada pasangan pelanggan-barang dengan riwayat", len(pas) > 0, f"{len(pas)} pasangan")
            for p in pas:
                cus, itm, n_sql = p["customer_id"], p["item_id"], p["n"]
                tag = f"{tid} {str(cus)[:6]}/{str(itm)[:6]}"
                r = await HH.riwayat(c, tid, cus, itm, None, 10)
                cek(f"A {tag}: total {r['total']} == SQL {n_sql}, items == min(total,10), urut tanggal turun",
                    r["total"] == n_sql and len(r["items"]) == min(n_sql, 10) and [x["tanggal"] for x in r["items"]] == sorted((x["tanggal"] for x in r["items"]), reverse=True))
                sql1 = await c.fetchrow(
                    "/* tenant_id */ SELECT si.id, sii.quantity, sii.unit, sii.unit_price, si.status FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id "
                    "WHERE si.tenant_id = $1 AND si.customer_id = $2 AND sii.item_id = $3 AND si.status NOT IN ('draft', 'void') AND NOT si.is_opening_balance "
                    "ORDER BY si.invoice_date DESC, si.created_at DESC, sii.line_number DESC LIMIT 1", tid, cus, itm)
                a = r["items"][0]
                cek(f"B {tag}: baris terbaru == SQL independen (harga {a['harga']}, qty {a['qty']}, {a['unit']})",
                    a["doc_id"] == str(sql1["id"]) and a["harga"] == float(D(str(sql1["unit_price"])).quantize(D("0.01"))) and a["unit"] == sql1["unit"] and not a["dikonversi"])
                n_dok = await c.fetchval("/* tenant_id */ SELECT count(*) FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id WHERE si.tenant_id = $1 AND si.id = $2 AND sii.item_id = $3", tid, sql1["id"], itm)
                rx = await HH.riwayat(c, tid, cus, itm, None, 10, sql1["id"])
                cek(f"C {tag}: exclude_doc_id: total {rx['total']} == {n_sql} - {n_dok}, dokumen itu tak muncul", rx["total"] == n_sql - n_dok and all(x["doc_id"] != str(sql1["id"]) for x in rx["items"]))
                ids = [x["doc_id"] for x in r["items"]]
                st = await c.fetch("/* tenant_id */ SELECT id, status FROM sales_invoices WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)
                cek(f"E {tag}: tak ada draf/void di hasil", all(s["status"] not in ("draft", "void") for s in st))
                konv = await c.fetch("/* tenant_id */ SELECT conversion_unit FROM unit_conversions WHERE tenant_id = $1 AND product_id = $2 AND COALESCE(is_active, true) AND conversion_factor > 0 LIMIT 1", tid, itm)
                if konv:
                    rk = await HH.riwayat(c, tid, cus, itm, konv[0]["conversion_unit"], 10)
                    ok = True
                    for x, y in zip(r["items"], rk["items"]):
                        if y["dikonversi"]:
                            ok = ok and abs(D(str(x["harga"])) * D(str(x["qty"])) - D(str(y["harga"])) * D(str(y["qty"]))) <= D("0.01") * (D(str(y["qty"])) + 1)
                    cek(f"D {tag}: konversi ke '{konv[0]['conversion_unit']}': harga x qty tak berubah (toleransi pembulatan)", ok)
            if pas:
                cus, itm = pas[0]["customer_id"], pas[0]["item_id"]
                t = []
                for _ in range(30):
                    a0 = time.perf_counter()
                    await HH.riwayat(c, tid, cus, itm, None, 10)
                    t.append(time.perf_counter() - a0)
                t.sort()
                p50, p95 = statistics.median(t), t[28]
                cek(f"F {tid} pasangan terbanyak ({pas[0]['n']} baris): p50 {p50 * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms (target < 300 ms)", p95 < 0.3)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
