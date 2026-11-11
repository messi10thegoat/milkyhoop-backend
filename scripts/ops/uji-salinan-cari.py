"""(a) cari_barang varian[] aditif + (b) race kode barang otomatis -- uji di SALINAN (klon milkydb); tulis HANYA di salinan.
GUARD: koneksi create_item (settings.get_db_config) DAN pool harus menunjuk database scratch_cari*; selain itu berhenti SEBELUM menulis.
T1 (a) induk dengan anak ukuran x warna: hasil pencarian memuat varian[{id,nama,atribut,harga}] lengkap; ukuran lama tak berubah (ganda utk 2 dimensi);
       tool LLM search_items tanpa varian; barang tanpa varian -> varian [].
T2 (b) N pembuatan barang SERENTAK (rute create_item ASLI, tanpa item_code): SEMUA berhasil dengan kode BRG- berbeda (kunci per tenant).
       Dengan POHON tanpa perbaikan, T2 HARUS gagal (kontrol merah: balapan itu nyata)."""
import asyncio
import json
import os
import sys
import time

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.milkybook.v2 import cari_barang as CB, tafsir  # noqa: E402
from app.routers import items as I  # noqa: E402
from app.schemas.items import CreateItemRequest  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
N = int(os.environ.get("N_SERENTAK", "8"))
KONTROL_MERAH = os.environ.get("KONTROL_MERAH") == "1"


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/api/items", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-cari-kode"}
    return r


async def buat_barang(tid, nama):
    b = CreateItemRequest(name=nama, item_type="goods", track_inventory=False, base_unit="Pcs", sales_price=1000, purchase_price=500)
    try:
        await I.create_item(req(tid), b)
        return 201, None
    except HTTPException as e:
        return e.status_code, e.detail


async def periksa(pool, tid):
    stamp = int(time.time())
    try:
        await t1(pool, tid, stamp)
    except Exception as e:  # noqa: BLE001
        if not KONTROL_MERAH:
            raise
        cek(f"{tid} T1 (kontrol merah: pohon tanpa varian[] gagal seperti diharapkan, lanjut ke T2)", False, repr(e)[:80])
    await t2(pool, tid, stamp)


async def t1(pool, tid, stamp):
    # ---- T1 (a): data varian via SQL langsung (cabang ini tak memuat router matriks B1)
    induk = f"UJI Cari {stamp}"
    async with pool.acquire() as c:
        pid = await c.fetchval("INSERT INTO products (tenant_id, nama_produk, satuan, base_unit, status, sales_price) VALUES ($1, $2, 'Pcs', 'Pcs', 'active', 75000) RETURNING id", tid, induk)
        for u in ("S", "M"):
            for w in ("Hitam", "Putih"):
                await c.execute("INSERT INTO products (tenant_id, nama_produk, satuan, base_unit, status, sales_price, matrix_parent_id, matrix_attributes) "
                                "VALUES ($1, $2, 'Pcs', 'Pcs', 'active', $3, $4, $5::jsonb)", tid, f"{induk} {u} {w}", 75000 if u == "S" else 80000, pid,
                                json.dumps({"ukuran": u, "warna": w}))
        tanpa = f"UJI TanpaVarian {stamp}"
        await c.execute("INSERT INTO products (tenant_id, nama_produk, satuan, base_unit, status, sales_price) VALUES ($1, $2, 'Pcs', 'Pcs', 'active', 1000)", tid, tanpa)
        hasil = [x for x in await CB.cari(c, tid, induk) if x["nama"] == induk]
        cek(f"{tid} T1 induk muncul sekali dengan varian[] 4 anak lengkap (id, nama, atribut dua dimensi, harga)",
            len(hasil) == 1 and len(hasil[0]["varian"]) == 4 and all(set(v) == {"id", "nama", "atribut", "harga"} for v in hasil[0]["varian"])
            and sorted((v["atribut"]["ukuran"], v["atribut"]["warna"], v["harga"]) for v in hasil[0]["varian"])
            == [("M", "Hitam", 80000), ("M", "Putih", 80000), ("S", "Hitam", 75000), ("S", "Putih", 75000)])
        cek(f"{tid} T1 `ukuran` lama tak berubah (2 dimensi -> ganda S,S,M,M)", [u["ukuran"] for u in hasil[0]["ukuran"]] == ["S", "S", "M", "M"])
        kosong = [x for x in await CB.cari(c, tid, tanpa) if x["nama"] == tanpa]
        cek(f"{tid} T1 barang tanpa varian -> varian [] dan ukuran []", len(kosong) == 1 and kosong[0]["varian"] == [] and kosong[0]["ukuran"] == [])
        tool = await tafsir.jalankan_tool(c, tid, "search_items", {"q": induk})
        t = [x for x in tool["items"] if x["nama"] == induk]
        cek(f"{tid} T1 tool LLM search_items tanpa varian[] (hemat token), ukuran tetap", len(t) == 1 and "varian" not in t[0] and len(t[0]["ukuran"]) == 4)



async def t2(pool, tid, stamp):
    # ---- T2 (b): balapan kode barang otomatis
    async with pool.acquire() as c:
        n0 = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk LIKE $2", tid, f"UJI Race {stamp} %")
    hasil2 = await asyncio.gather(*[buat_barang(tid, f"UJI Race {stamp} {i}") for i in range(N)])
    kode = sorted(x[0] for x in hasil2)
    async with pool.acquire() as c:
        baris = await c.fetch("SELECT item_code FROM products WHERE tenant_id = $1 AND nama_produk LIKE $2", tid, f"UJI Race {stamp} %")
    kodes = [b["item_code"] for b in baris]
    ok = kode == [201] * N and len(set(kodes)) == N and all((k or "").startswith("BRG-") for k in kodes)
    cek(f"{tid} T2 {N} pembuatan barang serentak: semua berhasil dengan kode BRG- berbeda",
        ok, f"status={dict((s, kode.count(s)) for s in set(kode))} kode_unik={len(set(kodes))}/{len(kodes)} galat={[x[1] for x in hasil2 if x[0] != 201][:1]}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db_pool = await c.fetchval("SELECT current_database()")
    lain = await I.get_db_connection()
    try:
        db_conn = await lain.fetchval("SELECT current_database()")
    finally:
        await lain.close()
    if not (db_pool.startswith("scratch_cari") and db_conn.startswith("scratch_cari")):
        print(f"TOLAK: bukan salinan (pool={db_pool} koneksi-create_item={db_conn})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS" + (" [KONTROL MERAH: pohon tanpa perbaikan]" if KONTROL_MERAH else ""), flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
