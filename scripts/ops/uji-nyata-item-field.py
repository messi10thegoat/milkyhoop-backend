"""UJI NYATA W-20261013-ITEMFIELD (kaos, SEMUA ROLLBACK): handler ASLI create_item/update_item dengan badan nyata
form klasik, CW Item lengkap, dan chat -> setiap kunci yang dikirim TERSIMPAN; pajak salah arah 422; harga bertingkat
berisi 422; 409 seragam; PUT ubah tipe jasa -> non_inventory TIDAK 500 (SQL berkutip). Kontrol merah: kode master.
Jalankan di kontainer sekali-pakai dengan kode :ro; DATABASE_URL dari gateway berjalan."""
import asyncio, json, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
USER = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
PPN_OUT = "4b629845-2dc1-451a-8d8f-3f932873c6ba"
PPN_IN = "865730e8-027c-40c5-a621-57d6784c531f"
WH = "ba2a8f6d-fc58-4498-a080-40aa7fe88343"
KOLOM = {"name": "nama_produk", "description": "deskripsi"}
TAK_DISIMPAN = {"price_levels", "quantity_pricing", "quantity_pricing_enabled", "default_bin_id", "is_matrix_parent",
                "opening_stock", "opening_stock_rate", "opening_stock_date"}
HASIL = []


def cek(n, ok, info=""):
    HASIL.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f" | {info}" if info else ""), flush=True)


class Conn:
    def __init__(self, c): self.c = c
    def __getattr__(self, k): return getattr(self.c, k)
    async def close(self): pass


class _Batal(Exception):
    pass


async def sp(c, fn):
    out = {}
    try:
        async with c.transaction():
            out["v"] = await fn()
            raise _Batal()
    except _Batal:
        pass
    return out["v"]


def req():
    r = Request({"type": "http", "method": "POST", "headers": [], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": T, "user_id": USER, "role": "OWNER", "username": "TES E2E"}
    return r


def sama(kirim, simpan):
    if kirim is None: return simpan is None
    if isinstance(kirim, bool): return simpan is kirim
    if isinstance(kirim, (int, float)): return simpan is not None and float(simpan) == float(kirim)
    return str(simpan) == str(kirim)


async def galat(coro):
    try:
        return {"ok": await coro}
    except HTTPException as e:
        return {"HTTP": e.status_code, "detail": e.detail}


async def jumlah(c):
    return {k: await c.fetchval(v, T) for k, v in {
        "produk": "SELECT md5(string_agg(id::text||nama_produk||coalesce(sales_tax_id::text,''), ',' ORDER BY id)) FROM products WHERE tenant_id=$1",
        "konv": "SELECT count(*) FROM unit_conversions WHERE tenant_id=$1",
        "aktivitas": "SELECT count(*) FROM item_activities WHERE tenant_id=$1",
        "je": "SELECT count(*) FROM journal_entries WHERE tenant_id=$1"}.items()}


async def main():
    import app.routers.items as I
    from app.schemas.items import CreateItemRequest as C, UpdateItemRequest as U
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = c.transaction(); await tr.start()
    async def gdc(): return Conn(c)
    I.get_db_connection = gdc
    sebelum = await jumlah(c)
    u = uuid.uuid4().hex[:6]
    BADAN = {
        "KLASIK": {"name": f"TES E2E klasik {u}", "item_code": f"TE-K-{u}", "sku": f"TE-K-{u}", "item_type": "goods",
                   "track_inventory": True, "base_unit": "Pcs", "barcode": f"BC{u}", "kategori": "TES", "deskripsi": "d klasik",
                   "for_sales": True, "for_purchases": True, "sales_account": "Sales", "purchase_account": "Cost of Goods Sold",
                   "sales_tax": "PPN 11% Keluaran", "sales_tax_id": PPN_OUT, "purchase_tax": "PPN 11% Masukan", "purchase_tax_id": PPN_IN,
                   "sales_price": 15000, "purchase_price": 9000, "costing_method": "weighted_average",
                   "opening_stock": None, "opening_stock_rate": None, "opening_stock_date": None, "preferred_vendor_id": None,
                   "reorder_level": 5, "warehouse_id": WH, "default_bin_id": None, "image_url": None,
                   "conversions": [{"conversion_unit": "Dus", "conversion_factor": 12, "purchase_price": 100000, "sales_price": 170000}],
                   "price_levels": None, "quantity_pricing_enabled": False, "quantity_pricing": None,
                   "track_batches": False, "track_expiry": False, "default_expiry_days": None, "is_matrix_parent": False},
        "CW_LENGKAP": {"name": f"TES E2E cw {u}", "item_type": "non_inventory", "track_inventory": False, "base_unit": "Pcs",
                       "for_sales": True, "for_purchases": True, "sales_account": "Pendapatan Penjualan", "purchase_account": "Beban Pembelian",
                       "sales_account_id": None, "purchase_account_id": None, "sales_price": 20000, "sales_tax": "PPN 11% Keluaran",
                       "sales_tax_id": PPN_OUT, "costing_method": "weighted_average", "bisa_dikirim": True, "item_code": f"TE-C-{u}",
                       "sku": f"TE-C-{u}", "barcode": None, "kategori": "TES", "deskripsi": "d cw", "purchase_price": 12000,
                       "purchase_tax": "PPN 11% Masukan", "purchase_tax_id": PPN_IN, "preferred_vendor_id": None},
        "CHAT": {"name": f"TES E2E chat {u}", "item_type": "goods", "base_unit": "Pcs", "sales_price": 5000, "sku": f"TE-H-{u}",
                 "description": "deskripsi dari chat", "kategori": "TES", "sales_tax": "PPN 11% Keluaran"},
    }
    ids = {}
    try:
        for nama, b in BADAN.items():
            r = await galat(I.create_item(req(), C(**b)))
            if "HTTP" in r:
                cek(f"1 {nama}: POST tersimpan", False, str(r)[:200]); continue
            pid = uuid.UUID(r["ok"].data["id"]); ids[nama] = pid
            row = dict(await c.fetchrow("SELECT * FROM products WHERE id=$1", pid))
            konv = await c.fetchval("SELECT count(*) FROM unit_conversions WHERE product_id=$1", pid)
            beda = []
            for k, v in b.items():
                if k in TAK_DISIMPAN or k in ("sales_account_id", "purchase_account_id"): continue
                if k == "conversions":
                    if konv != len(v): beda.append(f"conversions {len(v)}->{konv}")
                    continue
                kol = KOLOM.get(k, k)
                if kol not in row or not sama(v, row[kol]): beda.append(f"{k}: {v!r} -> {row.get(kol)!r}")
            cek(f"1 {nama}: SEMUA kunci yang dikirim tersimpan ({len(b)} kunci)", not beda, "; ".join(beda)[:300])
        k = ids.get("KLASIK")
        if k:
            nm = await c.fetchrow("SELECT sales_tax, purchase_tax FROM products WHERE id=$1", k)
            cek("2 nama pajak teks = tax_codes.name dari id", nm["sales_tax"] == "PPN 11% Keluaran" and nm["purchase_tax"] == "PPN 11% Masukan", str(dict(nm)))
        # 3) pajak salah arah / tenant lain -> 422
        r = await galat(I.create_item(req(), C(name=f"TES E2E arah {u}", base_unit="Pcs", item_type="non_inventory", sales_tax_id=PPN_IN)))
        cek("3 sales_tax_id = pajak MASUKAN -> 422 PAJAK_ITEM_TIDAK_SAH", r.get("HTTP") == 422 and r["detail"].get("code") == "PAJAK_ITEM_TIDAK_SAH", str(r)[:200])
        asing = await c.fetchval("SELECT id FROM tax_codes WHERE tenant_id <> $1 AND direction='output' LIMIT 1", T)
        r = await galat(I.create_item(req(), C(name=f"TES E2E asing {u}", base_unit="Pcs", item_type="non_inventory", sales_tax_id=str(asing))))
        cek("4 sales_tax_id milik tenant LAIN -> 422 (tidak ditemukan)", r.get("HTTP") == 422 and "tidak ditemukan" in r["detail"].get("message", ""), str(r)[:200])
        # 5) harga bertingkat berisi -> 422, tak ada baris
        n0 = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id=$1", T)
        r = await galat(I.create_item(req(), C(name=f"TES E2E tingkat {u}", base_unit="Pcs", price_levels=[{"name": "Grosir", "price": 1}])))
        cek("5 price_levels berisi -> 422 FIELD_BELUM_DIDUKUNG, nol baris", r.get("HTTP") == 422 and r["detail"].get("code") == "FIELD_BELUM_DIDUKUNG"
            and await c.fetchval("SELECT count(*) FROM products WHERE tenant_id=$1", T) == n0, str(r)[:200])
        # 6) 409 seragam
        if k:
            for nama, badan, kode, field in [
                ("nama", {"name": BADAN["KLASIK"]["name"]}, "NAMA_BARANG_DIPAKAI", "name"),
                ("sku", {"name": f"TES E2E x1 {u}", "sku": BADAN["KLASIK"]["sku"]}, "KODE_BARANG_DIPAKAI", "sku"),
                ("item_code", {"name": f"TES E2E x2 {u}", "item_code": BADAN["KLASIK"]["item_code"], "sku": f"LAIN-{u}"}, "KODE_BARANG_DIPAKAI", "item_code"),
                ("barcode", {"name": f"TES E2E x3 {u}", "barcode": BADAN["KLASIK"]["barcode"]}, "BARCODE_BARANG_DIPAKAI", "barcode")]:
                r = await galat(I.create_item(req(), C(base_unit="Pcs", **badan)))
                d = r.get("detail") if isinstance(r.get("detail"), dict) else {}
                cek(f"6 409 {nama}: code+field+terhapus+nonaktif", r.get("HTTP") == 409 and d.get("code") == kode and d.get("field") == field
                    and d.get("terhapus") is False and d.get("nonaktif") is False, str(r)[:200])
            # 7) pemegang nonaktif -> item_id
            await c.execute("UPDATE products SET status='inactive' WHERE id=$1", k)
            r = await galat(I.create_item(req(), C(name=f"TES E2E x4 {u}", base_unit="Pcs", sku=BADAN["KLASIK"]["sku"])))
            d = r.get("detail") if isinstance(r.get("detail"), dict) else {}
            cek("7 409 sku pemegang NONAKTIF -> nonaktif true + item_id", d.get("nonaktif") is True and d.get("item_id") == str(k), str(r)[:200])
            await c.execute("UPDATE products SET status='active' WHERE id=$1", k)
            # 8) PUT ubah pajak lewat id
            r = await galat(I.update_item(req(), k, U(sales_tax_id=None, purchase_tax_id=PPN_IN)))
            r2 = await galat(I.update_item(req(), ids["CW_LENGKAP"], U(sales_tax_id=PPN_OUT, purchase_tax_id=None)))
            row = await c.fetchrow("SELECT sales_tax_id, purchase_tax_id FROM products WHERE id=$1", ids["CW_LENGKAP"])
            cek("8 PUT id pajak tersimpan (null = kosongkan)", "ok" in r and "ok" in r2 and str(row["sales_tax_id"]) == PPN_OUT
                and row["purchase_tax_id"] is None, f"{r} {r2} {dict(row)}"[:300])
        # 9) PUT ubah tipe jasa -> non_inventory: dulu SQL tanpa kutip -> 500
        async def ubah_tipe():
            rj = await I.create_item(req(), C(name=f"TES E2E jasa {u}", base_unit="Jam", item_type="service", sales_price=1000))
            return await galat(I.update_item(req(), uuid.UUID(rj.data["id"]), U(item_type="non_inventory")))
        r = await sp(c, ubah_tipe)
        cek("9 PUT jasa -> non_inventory tanpa riwayat: 200, bukan 500", "ok" in r, str(r)[:200])
    finally:
        await tr.rollback()
    cek("AKHIR nol jejak (produk, konversi, aktivitas, jurnal)", await jumlah(c) == sebelum)
    await c.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
