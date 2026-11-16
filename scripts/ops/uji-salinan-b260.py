"""BUG-260 BE + V502 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): handler ASLI GET /items, GET /items/merek atas klon, tenant kaos + nyawit.
A skema: products.merek TEXT nullable (V502 di salinan), indeks parsial ada, semua baris lama NULL
B sort_by=bisa_dijual: halaman-halaman berurutan (limit kecil) == urutan penuh independen (SB.hitung semua barang tersaring), asc dan desc; barang tak dilacak di ujung; total/has_more benar
C teks baris: stok_fisik_text/bisa_dijual_text == BD.* atas current_stock/bisa_dijual baris itu; None untuk barang tak dilacak
D merek: UPDATE di SALINAN (3 barang), ?merek= total == SQL, GET /items/merek == SQL GROUP BY
E WAKTU sort_by=bisa_dijual (p50/p95 dari 15 panggilan) di data asli klon; lalu skala ~2000 barang ber-stok (salin baris products di SALINAN) -- bila skala gagal dibuat, dicetak SKALA GAGAL dan rc=1 (MASTER mewajibkan ukur)."""
import asyncio
import inspect
import statistics
import sys
import time
import uuid

from fastapi.params import Query as _Q
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import barang_daftar as BD  # noqa: E402
from app.services import stok_baris as SB  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def daftar(rq, **kw):
    """Panggil handler asli; parameter Query() tak diisi -> nilai bawaannya (bukan objek Query)."""
    p = {}
    for n, v in inspect.signature(RI.list_items).parameters.items():
        if n == "request":
            continue
        p[n] = v.default.default if isinstance(v.default, _Q) else (None if v.default is inspect._empty else v.default)
    p.update(kw)
    return await RI.list_items(rq, **p)


async def semua_urut(c, tid, arah):
    """Urutan penuh independen: SB.hitung atas semua barang tak terhapus; dilacak dulu menurut bisa_dijual, tak dilacak di ujung; seri id turun."""
    rows = await c.fetch("/* tenant_id */ SELECT id, item_type, track_inventory, status FROM products WHERE tenant_id = $1 AND deleted_at IS NULL", tid)
    dil = [r["id"] for r in rows if BD.dilacak(dict(r))]
    sb = (await SB.hitung(c, tid, dil, await tanggal_dokumen(c, tid)))["barang"] if dil else {}
    nilai = {str(r["id"]): (sb.get(str(r["id"])) or {}).get("bisa_dijual") for r in rows}
    ids = sorted((r["id"] for r in rows), key=str, reverse=True)
    ada = sorted((i for i in ids if nilai[str(i)] is not None), key=lambda i: nilai[str(i)], reverse=(arah == "desc"))
    return [str(i) for i in ada + [i for i in ids if nilai[str(i)] is None]], nilai


async def semua_halaman(rq, arah, limit):
    out, off = [], 0
    while True:
        r = await daftar(rq, sort_by="bisa_dijual", sort_order=arah, limit=limit, offset=off)
        out += [str(i.id) for i in r.items]
        if not r.has_more:
            return out, r.total
        off += limit


async def waktu(rq, n=15, **kw):
    t = []
    for _ in range(n):
        a = time.perf_counter()
        await daftar(rq, sort_by="bisa_dijual", sort_order="desc", limit=50, offset=0, **kw)
        t.append(time.perf_counter() - a)
    t.sort()
    return statistics.median(t), t[max(0, int(len(t) * 0.95) - 1)]


async def main():
    pool = await RI.get_pool()
    async with pool.acquire() as c:
        kol = await c.fetchrow("SELECT format_type(a.atttypid, a.atttypmod) AS tipe, a.attnotnull AS wajib FROM pg_catalog.pg_attribute a WHERE a.attrelid = 'products'::regclass AND a.attname = 'merek' AND NOT a.attisdropped")
        cek("A kolom products.merek ada, text, nullable", kol is not None and kol["tipe"] == "text" and not kol["wajib"])
        n_idx = await c.fetchval("SELECT count(*) FROM pg_catalog.pg_indexes WHERE tablename = 'products' AND indexname = 'idx_products_tenant_merek'")
        cek("A indeks parsial idx_products_tenant_merek ada", n_idx == 1)
        n_nn = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = ANY($1::text[]) AND merek IS NOT NULL", [KA, NY])
        cek("A semua baris lama merek NULL (tanpa backfill)", n_nn == 0, f"{n_nn} terisi")
        for tid in (KA, NY):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.name ILIKE '%owner%' LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik", uid is not None)
            if uid is None:
                continue
            rq = req(tid, uid)
            for arah in ("asc", "desc"):
                urut, nilai = await semua_urut(c, tid, arah)
                hal, total = await semua_halaman(rq, arah, 7)
                cek(f"B {tid} {arah}: halaman limit 7 == urutan penuh independen ({len(urut)} barang), total {total}", hal == urut and total == len(urut))
                dilacak_n = sum(1 for v in nilai.values() if v is not None)
                cek(f"B {tid} {arah}: barang tak dilacak di ujung ({len(urut) - dilacak_n})", all(nilai[i] is None for i in urut[dilacak_n:]))
            r = await daftar(rq, limit=200)
            ok = True
            for it in r.items:
                row = {"item_type": it.item_type, "track_inventory": it.track_inventory, "status": it.status}
                ok = ok and it.stok_fisik_text == BD.stok_fisik_text(row, it.current_stock, None if it.stok_fisik_text is None else it.stok_fisik_text.partition(" ")[2] or None)
                ok = ok and ((it.bisa_dijual_text is None) == (it.stok_fisik_text is None))
            cek(f"C {tid}: teks stok per baris konsisten, bisa_dijual_text terisi bila stok_fisik_text terisi ({len(r.items)} baris)", ok)
            # D merek di SALINAN
            ids = [x["id"] for x in await c.fetch("/* tenant_id */ SELECT id FROM products WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY created_at LIMIT 3", tid)]
            for i, m in zip(ids, ("UJI Alfa", "UJI Alfa", "UJI Beta")):
                await c.execute("/* tenant_id */ UPDATE products SET merek = $3 WHERE id = $1 AND tenant_id = $2", i, tid, m)
            fa = await daftar(rq, merek="UJI Alfa", limit=200)
            n_sql = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND merek = 'UJI Alfa'", tid)
            cek(f"D {tid}: ?merek=UJI Alfa total {fa.total} == SQL {n_sql}, semua baris bermerek itu", fa.total == n_sql and all(i.merek == "UJI Alfa" for i in fa.items) and n_sql == min(2, len(ids)))
            lm = await RI.list_merek(rq)
            sql_m = {r_["nama"]: int(r_["jumlah"]) for r_ in await c.fetch("/* tenant_id */ SELECT merek AS nama, count(*) AS jumlah FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND merek IS NOT NULL GROUP BY merek", tid)}
            cek(f"D {tid}: GET /items/merek == SQL {sql_m}", {m["nama"]: m["jumlah"] for m in lm["merek"]} == sql_m)
            p50, p95 = await waktu(rq)
            cek(f"E {tid} data asli: sort bisa_dijual p50 {p50 * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms (target < 1000 ms)", p95 < 1.0)
        # E skala ~2000 barang ber-stok di SALINAN (tenant kaos): salin baris products, ledger masuk 10 per barang salinan
        try:
            kol_p = [r_["column_name"] for r_ in await c.fetch("SELECT a.attname AS column_name FROM pg_catalog.pg_attribute a WHERE a.attrelid = 'products'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' AND a.attname NOT IN ('id','item_code','sku','barcode','search_text','nama_produk','kode_produk') ORDER BY a.attnum")]
            sumber = await c.fetch("/* tenant_id */ SELECT id FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND track_inventory = true AND item_type = 'goods' AND status = 'active' LIMIT 20", KA)
            cek("E skala: ada barang ber-stok sumber di kaos", len(sumber) > 0)
            for k in range(2000):
                src = sumber[k % len(sumber)]["id"]
                nid = uuid.uuid4()
                await c.execute(f"/* tenant_id */ INSERT INTO products (id, item_code, nama_produk, {', '.join(kol_p)}) SELECT $1, 'SYN-' || $2::text, 'SYN barang ' || $2::text, {', '.join(kol_p)} FROM products WHERE id = $3 AND tenant_id = $4", nid, str(k), src, KA)
            n_syn = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND item_code LIKE 'SYN-%'", KA)
            cek(f"E skala: {n_syn} barang sintetis dibuat di salinan", n_syn == 2000)
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.name ILIKE '%owner%' LIMIT 1", KA)
            rq = req(KA, uid)
            p50, p95 = await waktu(rq)
            cek(f"E skala ~2000+: sort bisa_dijual p50 {p50 * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms (target < 1000 ms; bila lebih: usulkan batas/strategi)", p95 < 1.0)
        except Exception as e:  # noqa: BLE001
            cek("E SKALA GAGAL dibuat (MASTER mewajibkan ukur): " + type(e).__name__, False, str(e)[:200])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
