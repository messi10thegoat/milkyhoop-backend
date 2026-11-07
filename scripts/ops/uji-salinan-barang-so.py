"""Barang & Jasa standar SO -- uji di SALINAN (klon milkydb), BACA saja: handler ASLI list_items / get_items_summary / get_items_stats /
get_item per tenant (kaos, nyawit, grapgrap-manado).
T1 paritas per kunci: summary {k}_count == daftar ?aksi=k total; {k}_value == daftar sum_nilai_stok; himpunan id daftar ?aksi=k ==
   himpunan id yang aksi_semua-nya memuat k (SQL == Python, semua halaman).
T2 satu "habis": daftar ?stock_status=out_of_stock == summary stok_habis_count == summary out_of_stock_count == stats.outOfStock;
   low_stock sama; Σ nilai: daftar tanpa saring sum_nilai_stok == summary inventory_value == stats.inventoryValue.
T3 barang tak dilacak (non_inventory/tanpa lacak/jasa) nol di pill stok + KONTROL: ada barang tak dilacak ber-stok <= 0 (bila tidak,
   cek T3 hampa -- Law 33).
T4 cari: summary?search=x {k}_count == daftar ?search=x&aksi=k total.
T5 detail: position_text + aksi_tersedia ada; [info] dampak definisi lama (tanpa syarat aktif) vs baru."""
import asyncio
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import barang_daftar as BD  # noqa: E402

HASIL = []
import os
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))  # grapgrap = izin pemilik langsung


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/items", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-barang"}
    return r


async def daftar(tid, **kw):
    a = dict(item_type=None, track_inventory=None, search=None, kategori=None, stock_status=None, status=None, sort_by="created_at",
             sort_order="desc", limit=200, offset=0, aksi=None, with_totals=True)
    a.update(kw)
    return await RI.list_items(req(tid), **a)


async def semua(tid, **kw):
    hasil, off = [], 0
    while True:
        r = await daftar(tid, offset=off, **kw)
        hasil += r.items
        off += 200
        if not r.has_more:
            return r, hasil


async def main():
    for tid in TENANT:
        ring = (await RI.get_items_summary(req(tid), search=None)).data
        st = await RI.get_items_stats(req(tid))
        r0, barang = await semua(tid)
        print(f"[info] {tid}: barang={len(barang)} contoh posisi={sorted({b.position_text for b in barang})[:5]}", flush=True)
        for k in BD.SEMUA_KUNCI:
            r, baris = await semua(tid, aksi=k)
            cek(f"{tid} {k}: summary count == daftar total", ring[f"{k}_count"] == r.total == len(baris), f"{ring[f'{k}_count']} vs {r.total}")
            cek(f"{tid} {k}: summary value == daftar sum_nilai_stok", ring[f"{k}_value"] == r.sum_nilai_stok,
                f"{ring[f'{k}_value']} vs {r.sum_nilai_stok}")
            if k == "nonaktif":
                py = {b.id for b in barang if b.status != "active"}
            else:
                py = {b.id for b in barang if k in b.aksi_semua}
            cek(f"{tid} {k}: id SQL == id aksi_semua Python", {b.id for b in baris} == py, f"sql={len(baris)} py={len(py)}")
        habis = (await daftar(tid, stock_status="out_of_stock")).total
        rendah = (await daftar(tid, stock_status="low_stock")).total
        cek(f"{tid} satu habis: stock_status == summary == stats", habis == ring["stok_habis_count"] == ring["out_of_stock_count"] == st.stock.outOfStock,
            f"{habis}/{ring['stok_habis_count']}/{ring['out_of_stock_count']}/{st.stock.outOfStock}")
        cek(f"{tid} satu rendah: stock_status == summary == stats", rendah == ring["di_bawah_titik_pesan_count"] == ring["low_stock_count"] == st.stock.lowStock,
            f"{rendah}/{ring['di_bawah_titik_pesan_count']}/{st.stock.lowStock}")
        cek(f"{tid} Σ nilai: daftar == summary == stats", r0.sum_nilai_stok == ring["inventory_value"] == st.inventoryValue,
            f"{r0.sum_nilai_stok}/{ring['inventory_value']}/{st.inventoryValue}")
        tak = [b for b in barang if not (b.item_type == "goods" and b.track_inventory)]
        cek(f"{tid} tak dilacak nol di pill stok", not [b for b in tak if {"stok_habis", "di_bawah_titik_pesan"} & set(b.aksi_semua)],
            f"tak dilacak={len(tak)}")
        print(f"[kontrol] {tid}: tak dilacak aktif ber-stok<=0 = {len([b for b in tak if b.status == 'active' and (b.current_stock or 0) <= 0])}", flush=True)
        conn = await RI.get_db_connection()
        try:
            lama = await conn.fetchval(f"""SELECT count(*) FROM products p {BD.SQL_PER_STOK} WHERE p.tenant_id = $1 AND p.deleted_at IS NULL
                AND p.item_type = 'goods' AND p.track_inventory = true AND COALESCE(per.jumlah, 0) <= 0""", tid)
            minus = await conn.fetchval(f"""SELECT count(*) FROM products p {BD.SQL_PER_STOK} WHERE p.tenant_id = $1 AND p.deleted_at IS NULL
                AND {BD.sql_aksi('stok_habis')} AND COALESCE(per.jumlah, 0) < 0""", tid)
        finally:
            await conn.close()
        print(f"[dampak] {tid}: stock_status=out_of_stock lama (tanpa syarat aktif)={lama} -> baru={habis}; habis karena MINUS={minus}", flush=True)
        kata = next((b.name.split()[0] for b in barang if b.name and len(b.name.split()[0]) >= 3), None)
        if kata:
            rc = (await RI.get_items_summary(req(tid), search=kata)).data
            for k in BD.SEMUA_KUNCI:
                t = (await daftar(tid, search=kata, aksi=k)).total
                cek(f"{tid} cari '{kata}' {k}: summary == daftar", rc[f"{k}_count"] == t, f"{rc[f'{k}_count']} vs {t}")
        contoh = next((b for b in barang if b.aksi_semua), barang[0] if barang else None)
        if contoh:
            d = (await RI.get_item(req(tid), contoh.id)).data
            cek(f"{tid} detail: position_text + aksi_tersedia", d["position_text"] == contoh.position_text and set(d["aksi_tersedia"]) == {"nonaktifkan", "aktifkan", "sesuaikan_stok"},
                f"{d['position_text']} | {d['aksi_semua']}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
