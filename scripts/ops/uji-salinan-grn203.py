"""BUG-203 uji DB di SALINAN (bukan prod): handler ASLI /purchase-orders (buat, kirim, receive, receive/preview, close) + /goods-receipts atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
PO barang ber-stok 10 @1.000 -> kirim -> /receive 6 diterima + 2 DITOLAK ("rusak"):
1 GRN tersimpan: GET /goods-receipts/{id} items memuat quantity_rejected=2, reject_reason, ditolak_text · 2 stok gudang +6 (BUKAN +8), nilai GRN 6.000, hutang (compute_ap_outstanding) tak berubah
· 3 sisa PO TETAP terbuka: quantity_received=6, status partial_received, respons memuat sisa_belum_diterima (4), ditolak, petunjuk + tutup_po (pointer ke /close)
· 4 pratinjau items kosong: bawaan_semua_sisa=true + payload eksplisit; dgn items eksplisit: false · 5 tulis items kosong: 422 ITEMS_KOSONG, nol tulisan
· 6 terima sisa 4 -> status received, respons TANPA sisa/petunjuk/tutup_po · 7 PO kedua: terima 5 + tolak 1 lalu /close -> closed, stok tak berubah oleh close (tutup sisa = aksi eksplisit)."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import goods_receipts as GR  # noqa: E402
from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as SP  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


def d(x):
    return x.model_dump() if hasattr(x, "model_dump") else x


async def galat(coro):
    try:
        return d(await coro)
    except HTTPException as e:
        return e


async def main():
    pool = await PO.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory ORDER BY kode_produk LIMIT 1", tid)
            vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", tid)
            gudang = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik, barang ber-stok, vendor, gudang ada", all(x is not None for x in (uid, prod, vendor, gudang)))
            if any(x is None for x in (uid, prod, vendor, gudang)):
                continue
            await c.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, 'penerimaan_barang_v1', true) ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true", tid)
            hari = await tanggal_dokumen(c, tid)

            async def stok():
                return D(str(await c.fetchval("SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, prod["id"], gudang)))

            async def hutang():
                return D(str(await c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1) /* fungsi per-tenant: tenant_id = $1 */", tid)))

            async def buat_po():
                r = d(await PO.create_purchase_order(req(tid, uid), SP.CreatePurchaseOrderRequest(
                    vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="UJI203", notes="UJI203", items=[
                        {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "UJI203 barang", "quantity": D("10"), "unit_price": D("1000")}])))
                po = uuid.UUID(str(r["data"]["id"]))
                await PO.send_purchase_order(req(tid, uid), po)
                item = await c.fetchval("SELECT i.id FROM purchase_order_items i JOIN purchase_orders p ON p.id = i.po_id AND p.tenant_id = $2 WHERE i.po_id = $1 ORDER BY i.line_number LIMIT 1", po, tid)
                return po, item

            async def po_baris(po):
                return await c.fetchrow("SELECT p.status, i.quantity_received FROM purchase_orders p JOIN purchase_order_items i ON i.po_id = p.id WHERE p.id = $1 AND p.tenant_id = $2", po, tid)

            po, item = await buat_po()
            s0, h0 = await stok(), await hutang()
            # 4 pratinjau
            pk = d(await PO.preview_receive_goods(req(tid, uid), po, None))
            pk = pk.get("data", pk)
            pe = d(await PO.preview_receive_goods(req(tid, uid), po, SP.ReceivePreviewRequest(items=[SP.ReceiveItemRequest(po_item_id=str(item), quantity_received=D("6"))])))
            pe = pe.get("data", pe)
            cek(f"4 {tid} pratinjau items kosong: bawaan_semua_sisa=true, payload eksplisit (10)", pk["bawaan_semua_sisa"] is True and pk["payload"]["items"] and D(pk["payload"]["items"][0]["quantity_received"]) == 10, str(pk["payload"])[:200])
            cek(f"4 {tid} pratinjau items eksplisit: bawaan_semua_sisa=false", pe["bawaan_semua_sisa"] is False)
            # 5 tulis kosong
            sebelum = await po_baris(po)
            g5 = await galat(PO.receive_goods(req(tid, uid), po, SP.ReceiveGoodsRequest(receive_date=hari, items=[], warehouse_id=gudang)))
            cek(f"5 {tid} tulis items kosong: 422 ITEMS_KOSONG, nol tulisan", isinstance(g5, HTTPException) and g5.status_code == 422 and getattr(g5, "code", None) == "ITEMS_KOSONG"
                and dict(await po_baris(po)) == dict(sebelum) and await stok() == s0, str(g5))
            # terima 6 + tolak 2
            r = await galat(PO.receive_goods(req(tid, uid), po, SP.ReceiveGoodsRequest(receive_date=hari, warehouse_id=gudang, items=[
                SP.ReceiveItemRequest(po_item_id=str(item), quantity_received=D("6"), quantity_rejected=D("2"), reject_reason="rusak")])))
            dt = r["data"] if isinstance(r, dict) else {}
            cek(f"3 {tid} respons terima: sisa_belum_diterima 4, ditolak 2 'rusak', petunjuk, pointer tutup PO", isinstance(r, dict) and [x["sisa"] for x in dt.get("sisa_belum_diterima", [])] == [4.0]
                and [x["quantity_rejected"] for x in dt.get("ditolak", [])] == [2.0] and dt["ditolak"][0]["reject_reason"] == "rusak" and "tutup pesanan" in dt.get("petunjuk", "")
                and dt.get("tutup_po", {}).get("rute", "").endswith("/close") and dt["status"] == "partial_received", str(r)[:400])
            grn_id = uuid.UUID(dt["goods_receipt_id"]) if isinstance(dt, dict) and dt.get("goods_receipt_id") else None
            det = (await GR.get_goods_receipt(req(tid, uid), grn_id))["data"] if grn_id else {}
            it = (det.get("items") or [{}])[0]
            cek(f"1 {tid} GET GRN: quantity_rejected 2, reject_reason 'rusak', ditolak_text", D(str(it.get("quantity_rejected", 0))) == 2 and it.get("reject_reason") == "rusak"
                and str(it.get("ditolak_text", "")).startswith("Ditolak 2") and "rusak" in it.get("ditolak_text", ""), str(it))
            kolom = await c.fetchrow("SELECT i.quantity_rejected, i.reject_reason FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id AND g.tenant_id = $2 WHERE i.goods_receipt_id = $1", grn_id, tid)
            cek(f"1 {tid} kolom DB tersimpan (goods_receipt_items)", kolom is not None and D(str(kolom["quantity_rejected"])) == 2 and kolom["reject_reason"] == "rusak", str(dict(kolom) if kolom else None))
            cek(f"2 {tid} stok +6 (bukan +8), hutang tak berubah", await stok() == s0 + 6 and await hutang() == h0, f"stok {s0} -> {await stok()}, hutang {h0} -> {await hutang()}")
            amt = await c.fetchval("SELECT COALESCE(SUM(i.amount), 0) FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id AND g.tenant_id = $2 WHERE i.goods_receipt_id = $1", grn_id, tid)
            pb = await po_baris(po)
            cek(f"3 {tid} nilai GRN 6.000 (hanya yang diterima), PO quantity_received 6, status partial_received (sisa terbuka)", D(str(amt)) == 6000 and D(str(pb["quantity_received"])) == 6 and pb["status"] == "partial_received", f"{amt} {dict(pb)}")
            # 6 terima sisa 4
            r6 = await galat(PO.receive_goods(req(tid, uid), po, SP.ReceiveGoodsRequest(receive_date=hari, warehouse_id=gudang, items=[SP.ReceiveItemRequest(po_item_id=str(item), quantity_received=D("4"))])))
            d6 = r6["data"] if isinstance(r6, dict) else {}
            cek(f"6 {tid} terima sisa 4: status received, tanpa sisa/petunjuk/tutup_po", isinstance(r6, dict) and d6["status"] == "received" and d6["sisa_belum_diterima"] == [] and "petunjuk" not in d6 and "tutup_po" not in d6, str(r6)[:300])
            # 7 tutup sisa = aksi eksplisit
            po2, item2 = await buat_po()
            r7 = await galat(PO.receive_goods(req(tid, uid), po2, SP.ReceiveGoodsRequest(receive_date=hari, warehouse_id=gudang, items=[
                SP.ReceiveItemRequest(po_item_id=str(item2), quantity_received=D("5"), quantity_rejected=D("1"), reject_reason="cacat")])))
            s_sblm_tutup = await stok()
            ok_tutup = await galat(PO.close_purchase_order(req(tid, uid), po2))
            st2 = await po_baris(po2)
            cek(f"7 {tid} tutup sisa PO = aksi eksplisit: closed; stok tak berubah oleh close", isinstance(r7, dict) and st2["status"] == "closed" and await stok() == s_sblm_tutup, f"{r7 if not isinstance(r7, dict) else ''} {ok_tutup} {dict(st2)}"[:300])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
