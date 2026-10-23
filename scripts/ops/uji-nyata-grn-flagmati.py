"""UJI NYATA GRN syarat MASTER (1): flag penerimaan_barang_v1 MATI = /receive + /receive/preview BYTE-IDENTIK dengan master.
ROLLBACK-ONLY di kaos (TES E2E). Mode `dump` dijalankan di pohon LAMA (master) dan BARU (cabang) -> `banding <lama> <baru>`.
Sidik: respons pratinjau & terima (tanpa id), jumlah jurnal / baris stok / dokumen GRN yang lahir di transaksi, penghitung & status PO."""
import asyncio, inspect, json, os, re, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal as D

T = "kaos-biru-konveksi"


def bersih(x):
    """Buang medan identitas/waktu yang wajar berbeda antar-jalan."""
    if isinstance(x, dict):
        return {k: bersih(v) for k, v in x.items() if not re.search(r"(^id$|_id$|^created_at$|^updated_at$)", k)}
    if isinstance(x, list):
        return [bersih(v) for v in x]
    return x


async def dump():
    import asyncpg
    from uuid import UUID
    from starlette.requests import Request
    from app.routers import purchase_orders as RPO
    from app.schemas.purchase_orders import CreatePurchaseOrderRequest, ReceiveGoodsRequest, ReceivePreviewRequest
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["URL"])
    luar = c.transaction()
    await luar.start()
    out = {}
    try:
        class Pool:
            def acquire(s):
                class CM:
                    async def __aenter__(x): return c
                    async def __aexit__(x, *a): return False
                return CM()
        async def gp(): return Pool()
        RPO.get_pool = gp
        uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

        def req():
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
            r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}
            return r
        await c.execute("UPDATE tenant_features SET enabled = false WHERE tenant_id = $1 AND feature = 'penerimaan_barang_v1'", T)
        hari = await tanggal_dokumen(c, T)
        prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory ORDER BY kode_produk LIMIT 1", T)
        vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
        n0 = {k: await c.fetchval(q, T) for k, q in (
            ("jurnal", "SELECT count(*) FROM journal_entries WHERE tenant_id = $1"),
            ("stok", "SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1"))}
        r = await RPO.create_purchase_order(req(), CreatePurchaseOrderRequest(
            vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E GRN FLAGMATI", items=[
                {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang", "quantity": D("10"), "unit_price": D("1000")},
                {"description": "TES E2E jasa", "quantity": D("1"), "unit_price": D("500")}]))
        r = r.model_dump() if hasattr(r, "model_dump") else r
        po = UUID(str(r["data"]["id"]))
        await RPO.send_purchase_order(req(), po)
        it = [str(x["id"]) for x in await c.fetch("SELECT id FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number", po)]
        badan = [{"po_item_id": it[0], "quantity_received": D("6")}, {"po_item_id": it[1], "quantity_received": D("1")}]
        out["pratinjau"] = bersih((await RPO.preview_receive_goods(req(), po, ReceivePreviewRequest(receive_date=hari, items=badan)))["data"])
        t1 = await RPO.receive_goods(req(), po, ReceiveGoodsRequest(receive_date=hari, items=badan))
        t2 = await RPO.receive_goods(req(), po, ReceiveGoodsRequest(receive_date=hari, items=[{"po_item_id": it[0], "quantity_received": D("4")}]))
        dm = lambda x: bersih(json.loads(json.dumps(x.model_dump() if hasattr(x, "model_dump") else x, default=str)))
        out["terima_1"], out["terima_2"] = dm(t1), dm(t2)
        out["po"] = bersih({k: str(v) for k, v in dict(await c.fetchrow(
            "SELECT status, amount_received, amount_billed FROM purchase_orders WHERE id = $1", po)).items()})
        out["baris"] = [str(x["quantity_received"]) for x in await c.fetch("SELECT quantity_received FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number", po)]
        out["lahir"] = {k: await c.fetchval(q, T) - n0[k] for k, q in (
            ("jurnal", "SELECT count(*) FROM journal_entries WHERE tenant_id = $1"),
            ("stok", "SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1"))}
        ada_grn = await c.fetchval("SELECT to_regclass('goods_receipts') IS NOT NULL")
        out["lahir"]["grn"] = await c.fetchval("SELECT count(*) FROM goods_receipts WHERE purchase_order_id = $1", po) if ada_grn else 0
    finally:
        await luar.rollback()
        await c.close()
    print("HASIL " + json.dumps(out, sort_keys=True, default=str))


def banding(a, b):
    lama, baru = json.load(open(a)), json.load(open(b))
    gagal = 0
    for k in sorted(set(lama) | set(baru)):
        ok = lama.get(k) == baru.get(k)
        gagal += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + f"flag mati {k} identik dgn master" + ("" if ok else f"  lama={lama.get(k)} baru={baru.get(k)}"))
    print(f"INFO lahir (baru): {baru.get('lahir')}")
    print(f"SELESAI gagal={gagal}")
    sys.exit(1 if gagal else 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["banding"]:
        banding(sys.argv[2], sys.argv[3])
    else:
        asyncio.run(dump())
