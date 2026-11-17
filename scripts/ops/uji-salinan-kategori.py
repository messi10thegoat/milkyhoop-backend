"""KATEGORI-BE (18 Nov 2026) -- uji di SALINAN (V505 diterapkan ke klon): akun bawaan per kategori disalin ke barang BARU (BUG-282); pratinjau == penulis; barang lama tak berubah;
aksi_tersedia gabung/pindahkan_barang di daftar. Handler ASLI. Tulis HANYA di salinan (nama DB dicek)."""
import asyncio
import json
import os
import sys
import uuid

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from fastapi import HTTPException  # noqa: E402
from app.routers import items as IT  # noqa: E402
from app.schemas.items import CreateCategoryRequest, CreateItemRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])
    print(("[ok] " if ok else "[GAGAL] ") + nama + ("" if ok else " :: " + str(info)[:300]), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-kategori"}
    return r


def iid(x):
    d = x.data if hasattr(x, "data") else x["data"]
    return uuid.UUID(str(d["id"]))


async def akun_tenant(conn, tid, tipe, kata=None):
    sql = "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_type = ANY($2::text[]) AND is_active AND NOT is_header"
    if kata:
        sql += " AND name ILIKE '%' || $3 || '%'"
    sql += " ORDER BY account_code LIMIT 1"
    return await conn.fetchval(sql, tid, tipe, *([kata] if kata else []))


async def barang(conn, tid, pid):
    return await conn.fetchrow("SELECT sales_account_id, purchase_account_id, inventory_account_id, cogs_account_id FROM products WHERE tenant_id = $1 AND id = $2", tid, pid)


async def satu(conn, tid):
    jual, pers = await akun_tenant(conn, tid, ["REVENUE"]), await akun_tenant(conn, tid, ["ASSET"], "persediaan")
    hpp, beli = await akun_tenant(conn, tid, ["COGS"]), await akun_tenant(conn, tid, ["EXPENSE"])
    if not all((jual, pers, hpp, beli)):
        print(f"[info] {tid}: CoA tak lengkap (jual/persediaan/hpp/beli) -> dilewati", flush=True)
        return
    jual, pers, hpp, beli = str(jual), str(pers), str(hpp), str(beli)
    lain = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id <> $1 AND account_type = 'REVENUE' AND is_active LIMIT 1", tid)
    r = req(tid)
    nama = f"UJI-Kat-{uuid.uuid4().hex[:6]}"
    c = await IT.create_category(r, CreateCategoryRequest(name=nama))
    kid = str(c.id)
    d0 = (await IT.get_category_detail(r, uuid.UUID(kid)))["data"]
    cek(f"{tid} detail kategori baru: akun kosong + teks + aksi gabung/pindahkan_barang", d0["akun_text"] == "Tanpa akun bawaan" and all(v is None for v in d0["akun"].values())
        and {"gabung", "pindahkan_barang", "atur_akun"} <= set(d0["aksi_tersedia"]) and d0["aksi_tersedia"]["pindahkan_barang"]["tersedia"] is False, d0)

    # pratinjau: tipe salah / akun tenant lain / sah
    p = (await IT.preview_akun_kategori(r, uuid.UUID(kid), {"penjualan": hpp}))["data"]
    cek(f"{tid} pratinjau tipe salah -> AKUN_TIPE_SALAH field penjualan", not p["ok"] and p["blocks"][0]["code"] == "AKUN_TIPE_SALAH" and p["blocks"][0]["field"] == "penjualan", p)
    if lain:
        p = (await IT.preview_akun_kategori(r, uuid.UUID(kid), {"penjualan": str(lain)}))["data"]
        cek(f"{tid} pratinjau akun tenant LAIN -> AKUN_TAK_DITEMUKAN", not p["ok"] and p["blocks"][0]["code"] == "AKUN_TAK_DITEMUKAN", p)
    semua = {"penjualan": jual, "persediaan": pers, "hpp": hpp, "pembelian": beli}
    p = (await IT.preview_akun_kategori(r, uuid.UUID(kid), semua))["data"]
    cek(f"{tid} pratinjau sah: 4 perubahan + peringatan barang lama", p["ok"] and len(p["rencana"]["perubahan"]) == 4 and "tidak berubah" in p["peringatan"][0], p)
    try:
        await IT.set_akun_kategori(r, uuid.UUID(kid), {"penjualan": hpp})
        cek(f"{tid} tulis tipe salah ditolak", False, "lolos")
    except HTTPException as e:
        cek(f"{tid} tulis tipe salah ditolak 409 kode sama dgn pratinjau", e.status_code == 409 and e.detail["code"] == "AKUN_TIPE_SALAH", e.detail)

    # barang SEBELUM akun kategori diisi
    b_lama = await IT.create_item(r, CreateItemRequest(name=f"{nama} lama", base_unit="Pcs", item_type="goods", track_inventory=True, kategori=nama))
    lama_id = iid(b_lama)
    ref = await barang(conn, tid, lama_id)

    w = await IT.set_akun_kategori(r, uuid.UUID(kid), semua)
    d1 = (await IT.get_category_detail(r, uuid.UUID(kid)))["data"]
    cek(f"{tid} tulis: detail memuat 4 akun + teks", w["success"] and all(d1["akun"][j] for j in semua) and d1["akun_text"].startswith("Penjualan "), d1)
    ev = await conn.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND \"eventType\" = 'ITEM_CATEGORY_ACCOUNTS_SET' AND entity_id = $2::uuid", tid, kid)
    cek(f"{tid} audit ITEM_CATEGORY_ACCOUNTS_SET tercatat", ev == 1, ev)
    try:
        await IT.set_akun_kategori(r, uuid.UUID(kid), semua)
        cek(f"{tid} tulis ulang sama -> TIDAK_ADA_PERUBAHAN", False, "lolos")
    except HTTPException as e:
        cek(f"{tid} tulis ulang sama -> 409 TIDAK_ADA_PERUBAHAN", e.status_code == 409 and e.detail["code"] == "TIDAK_ADA_PERUBAHAN", e.detail)

    # barang BARU: salin; pratinjau Buat Barang == tulis
    pv = (await IT.preview_create_item(r, {"name": f"{nama} baru", "base_unit": "Pcs", "item_type": "goods", "track_inventory": True, "kategori": nama}))["data"]
    cek(f"{tid} pratinjau Buat Barang: akun_dari_kategori + teks", pv["ok"] and pv["rencana"]["akun_dari_kategori"] and "mengikuti kategori" in pv["rencana"]["akun_dari_kategori_text"], pv)
    n1 = await IT.create_item(r, CreateItemRequest(name=f"{nama} baru", base_unit="Pcs", item_type="goods", track_inventory=True, kategori=nama))
    id1 = iid(n1)
    row = await barang(conn, tid, id1)
    cek(f"{tid} barang BARU menyalin 4 akun kategori", [str(row[k]) for k in ("sales_account_id", "inventory_account_id", "cogs_account_id", "purchase_account_id")] == [jual, pers, hpp, beli], dict(row))
    # eksplisit menang
    n2 = await IT.create_item(r, CreateItemRequest(name=f"{nama} eksplisit", base_unit="Pcs", item_type="goods", track_inventory=True, kategori=nama, sales_account_id=beli))
    row2 = await barang(conn, tid, iid(n2))
    cek(f"{tid} isian eksplisit menang atas kategori (penjualan), sisanya disalin", str(row2["sales_account_id"]) == beli and str(row2["cogs_account_id"]) == hpp, dict(row2))
    # jasa: tanpa persediaan/HPP dari kategori
    n3 = await IT.create_item(r, CreateItemRequest(name=f"{nama} jasa", base_unit="Jam", item_type="service", track_inventory=False, kategori=nama))
    row3 = await barang(conn, tid, iid(n3))
    cek(f"{tid} jasa: persediaan/HPP tidak disalin, penjualan disalin", row3["inventory_account_id"] is None and row3["cogs_account_id"] is None and str(row3["sales_account_id"]) == jual, dict(row3))

    # ubah akun kategori: barang lama + baru TAK berubah
    await IT.set_akun_kategori(r, uuid.UUID(kid), {"penjualan": None, "hpp": None})
    cek(f"{tid} ubah akun kategori: barang yang sudah ada tak berubah", dict(await barang(conn, tid, id1)) == dict(row) and dict(await barang(conn, tid, lama_id)) == dict(ref), "berubah")
    # kategori tanpa akun = perilaku lama (sama dgn barang tanpa kategori)
    n4 = await IT.create_item(r, CreateItemRequest(name=f"{nama} polos", base_unit="Pcs", item_type="goods", track_inventory=True))
    n5 = await IT.create_item(r, CreateItemRequest(name=f"{nama} polos2", base_unit="Pcs", item_type="goods", track_inventory=True, kategori=nama))
    r4 = await barang(conn, tid, iid(n4))
    r5 = await barang(conn, tid, iid(n5))
    cek(f"{tid} penjualan/HPP dikosongkan di kategori: barang baru memakai bawaan peran seperti tanpa kategori (HPP)", r5["cogs_account_id"] == r4["cogs_account_id"] and r5["sales_account_id"] is None, (dict(r4), dict(r5)))

    # daftar objek: kategori + akun + aksi gabung/pindahkan_barang
    dl = await IT.list_categories(r, bentuk="objek", search=nama, aksi=None, tingkat=None, induk_id=None, limit=50, offset=0)
    it = [x for x in dl["items"] if x["id"] == kid]
    cek(f"{tid} daftar objek: akun_text + aksi pindahkan_barang tersedia (ada barang) + gabung", len(it) == 1 and it[0]["akun"]["persediaan"] and it[0]["aksi_tersedia"]["pindahkan_barang"]["tersedia"] and it[0]["aksi_tersedia"]["gabung"]["tersedia"], it)


async def utama():
    pool = await IT.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_kat_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            await satu(conn, tid)
    json.dump({"CEK": CEK}, open(os.environ["KELUAR"], "w"), indent=1)
    gagal = [c for c in CEK if not c[1]]
    print(f"[info] {len(CEK)} cek, {len(gagal)} gagal", flush=True)
    sys.exit(1 if gagal or not CEK else 0)

asyncio.run(utama())
