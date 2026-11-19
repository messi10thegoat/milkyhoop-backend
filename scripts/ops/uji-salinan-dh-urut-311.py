"""BUG-311 uji DB di SALINAN (bukan prod): urutan baris GET /price-lists/{id} (handler ASLI), tenant kaos + nyawit.
A urutan respons == kunci independen (nama, item_id, satuan efektif: dasar dulu, min_qty naik); dicek di SEMUA daftar
B KONTROL MERAH: salinan disemai (satuan baris ber-min_qty tertinggi dijadikan NULL = pola baris impor) -> ORDER BY LAMA harus
  melanggar kunci (alat uji hidup), kode BARU harus patuh. Hanya menulis di salinan."""
import asyncio
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402

TENANT = ("pt-kita-nyawit-bersama", "kaos-biru-konveksi")
HASIL = []
LAMA = "ORDER BY pli.item_code, p.nama_produk, pli.unit NULLS FIRST, pli.min_quantity, pli.start_date NULLS FIRST, pli.id"
DASAR = "COALESCE(NULLIF(p.base_unit, ''), p.satuan)"


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan", "role": "OWNER"}
    return r


def kunci(nama, item_id, unit, dasar, mq):
    u = unit or dasar or ""
    return (nama or "", str(item_id), u != (dasar or ""), u, float(mq))


async def urut_lama(conn, tid, pl_id):
    return [str(r["id"]) for r in await conn.fetch(
        f"SELECT pli.id FROM price_list_items pli LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = $2 WHERE pli.price_list_id = $1 {LAMA}", pl_id, tid)]


async def kunci_sql(conn, tid, pl_id):
    return {str(r["id"]): kunci(r["nama_produk"], r["item_id"], r["unit"], r["dasar"], r["min_quantity"]) for r in await conn.fetch(
        f"SELECT pli.id, pli.item_id, pli.unit, pli.min_quantity, p.nama_produk, {DASAR} AS dasar FROM price_list_items pli "
        "LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = $2 WHERE pli.price_list_id = $1", pl_id, tid)}


async def main():
    conn = await IT.get_db_connection()
    for tid in TENANT:
        n = 0
        for d in await conn.fetch("SELECT id FROM price_lists WHERE tenant_id = $1 ORDER BY created_at", tid):
            k = await kunci_sql(conn, tid, d["id"])
            ids = [i["id"] for i in (await PL.get_price_list(req(tid), d["id"]))["data"]["items"]]
            n += len(ids)
            cek(f"A {tid} {str(d['id'])[:8]}: urut", ids == sorted(ids, key=lambda x: k[x]) and len(ids) == len(k), f"{len(ids)} baris")
        cek(f"A {tid}: ada baris", n > 0, str(n))
    # B kontrol merah: semai di salinan
    tid = TENANT[0]
    grup = await conn.fetchrow(
        f"""SELECT pli.price_list_id, pli.item_id FROM price_list_items pli JOIN products p ON p.id = pli.item_id AND p.tenant_id = $1
            JOIN price_lists pl ON pl.id = pli.price_list_id AND pl.tenant_id = $1 WHERE {DASAR} IS NOT NULL
            GROUP BY pli.price_list_id, pli.item_id HAVING count(*) >= 2 AND count(DISTINCT pli.min_quantity) = count(*) LIMIT 1""", tid)
    row = None
    if grup:  # normalkan semua baris kelompok ke satuan dasar eksplisit, lalu baris min_qty tertinggi -> NULL (pola impor)
        await conn.execute(f"UPDATE price_list_items pli SET unit = {DASAR} FROM products p WHERE p.id = pli.item_id AND p.tenant_id = $3 AND pli.price_list_id = $1 AND pli.item_id = $2", grup["price_list_id"], grup["item_id"], tid)
        row = await conn.fetchrow("SELECT pli.id, pli.price_list_id FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id AND pl.tenant_id = $3 WHERE pli.price_list_id = $1 AND pli.item_id = $2 ORDER BY pli.min_quantity DESC LIMIT 1", grup["price_list_id"], grup["item_id"], tid)
    cek("B ada baris untuk disemai", row is not None)
    if row:
        await conn.execute("UPDATE price_list_items SET unit = NULL WHERE id = $1", row["id"])
        k = await kunci_sql(conn, tid, row["price_list_id"])
        lama = await urut_lama(conn, tid, row["price_list_id"])
        cek("B kontrol: ORDER BY LAMA melanggar kunci (harus merah)", lama != sorted(lama, key=lambda x: k[x]))
        baru = [i["id"] for i in (await PL.get_price_list(req(tid), row["price_list_id"]))["data"]["items"]]
        cek("B kode BARU patuh kunci pada data semaian", baru == sorted(baru, key=lambda x: k[x]))
    ok = all(HASIL) and len(HASIL) > 0
    print(f"{sum(HASIL)}/{len(HASIL)} lulus")
    sys.exit(0 if ok else 1)

asyncio.run(main())
