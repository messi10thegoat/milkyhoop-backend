"""UJI NYATA GRN (23 Okt 2026) -- ROLLBACK-ONLY di kaos ("TES E2E"): SEMUA tulisan di SATU transaksi luar yang SELALU di-ROLLBACK.
Mode: baru [merah_fifo|merah_penjaga|merah_dobel_inbound]
Alur (3-way match PO qty <-> GRN <-> tagihan, net 0):
  1. flag penerimaan_barang_v1 dinyalakan (di transaksi), PO TES E2E (barang ber-stok qty 10 @1.000 + jasa qty 1 @500) dibuat + dikirim
     lewat fungsi rute ASLI.
  2. pratinjau terima == tulis (jurnal & nilai); GRN-1 qty 6 (+ jasa 1): jurnal Dr Persediaan 6.000 / Cr GRNI 6.000, jasa tanpa jurnal (P2),
     stok gudang +6, penjaga sehat. GRN-2 qty 4: GRNI 10.000, stok +10, PO received.
  3. tagihan TES E2E (8 @1.100) atas baris PO: kontrak grn_tagih -> tautan FIFO GRN-1 6 + GRN-2 2, nilai_grni 8.000, selisih 800,
     qty_inbound 0; tautan ditulis + jurnal tagihan simulasi (Dr GRNI 8.000, Dr Selisih 800 / Cr Hutang 8.800) -> penjaga sehat (GRNI 2.000).
  4. void GRN-2 DITOLAK (GRN_SUDAH_DITAGIH); balik tautan + balik jurnal tagihan -> void GRN-2 & GRN-1 lolos -> GRNI 0, stok kembali,
     qty diterima PO 0 = net 0.
Kontrol merah (WAJIB FAIL): merah_fifo (urutan GRN dibalik), merah_penjaga (suntik baris GL GRNI 1 sen), merah_dobel_inbound (qty_inbound
dipaksa = qty tagih -> stok/persediaan dobel terdeteksi)."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request

from app.routers import goods_receipts as RGR, purchase_orders as RPO
from app.schemas.purchase_orders import CreatePurchaseOrderRequest, ReceiveGoodsRequest
from app.services import grn_penjaga as GP, grn_rencana as G, grn_tagih as GT
from app.services.role_resolver import AccountRole, resolve_account_id_by_role
from app.utils.tanggal_tenant import tanggal_dokumen

MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
T = "kaos-biru-konveksi"
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok:
        gagal.append(nama)


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    luar = c.transaction()
    await luar.start()
    try:
        await jalan(c)
    finally:
        await luar.rollback()  # ROLLBACK-ONLY: tak ada yang tersisa di buku
        sisa = await c.fetchval("SELECT count(*) FROM purchase_orders WHERE tenant_id = $1 AND po_number LIKE 'TES E2E GRN%'", T)
        cek("rollback: nol PO TES E2E GRN tersisa", sisa == 0, sisa)
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


async def jalan(c):
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    RPO.get_pool = gp
    RGR.get_pool = gp
    uid = await c.fetchval("SELECT created_by FROM purchase_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T) \
        or await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req():
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}
        return r

    hari = await tanggal_dokumen(c, T)
    await c.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, $2, true) "
                    "ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true", T, G.FLAG)
    prod = await c.fetchrow("SELECT id, kode_produk, nama_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory "
                            "ORDER BY kode_produk LIMIT 1", T)
    nonstok = await c.fetchrow("SELECT id, kode_produk, nama_produk FROM products WHERE tenant_id = $1 AND item_type = 'non_inventory' "
                               "ORDER BY kode_produk LIMIT 1", T)
    vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
    gudang = await c.fetchrow("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)
    akun_grni = await resolve_account_id_by_role(c, T, AccountRole.GOODS_RECEIVED_NOT_INVOICED)
    akun_ppv = await resolve_account_id_by_role(c, T, AccountRole.PURCHASE_PRICE_VARIANCE)
    akun_ap = await resolve_account_id_by_role(c, T, AccountRole.AP_TRADE)
    stok = lambda: c.fetchval("SELECT COALESCE(quantity, 0) FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 AND item_id = $3",
                              T, gudang["id"], prod["id"])
    stok0 = D(str(await stok() or 0))
    print(f"INFO produk {prod['kode_produk']} gudang {gudang['id']} stok awal {stok0}")

    body = CreatePurchaseOrderRequest(vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E GRN",
                                      notes="TES E2E GRN harness (rollback)", items=[
        {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang", "quantity": D("10"), "unit_price": D("1000")},
        {"description": "TES E2E jasa angkut", "quantity": D("1"), "unit_price": D("500")},
        {"item_id": str(nonstok["id"]), "item_code": nonstok["kode_produk"], "description": "TES E2E barang non-stok", "quantity": D("2"),
         "unit_price": D("250")}])
    r = await RPO.create_purchase_order(req(), body)
    r = r.model_dump() if hasattr(r, "model_dump") else r
    po_id = r["data"]["id"]
    await c.execute("UPDATE purchase_orders SET po_number = 'TES E2E GRN ' || po_number WHERE id = $1", UUID(str(po_id)))
    await RPO.send_purchase_order(req(), UUID(str(po_id)))
    items = await c.fetch("SELECT id, description FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number", UUID(str(po_id)))
    brg, jasa, nonst = str(items[0]["id"]), str(items[1]["id"]), str(items[2]["id"])
    POID = UUID(str(po_id))

    p0 = await GP.periksa(c, T)  # garis dasar sebelum GRN harness
    # pratinjau == tulis
    from app.schemas.purchase_orders import ReceivePreviewRequest
    pv = await RPO.preview_receive_goods(req(), POID, ReceivePreviewRequest(receive_date=hari, items=[
        {"po_item_id": brg, "quantity_received": D("6")}, {"po_item_id": jasa, "quantity_received": D("1")},
        {"po_item_id": nonst, "quantity_received": D("2")}], warehouse_id=gudang["id"]))
    pg = pv["data"]["goods_receipt"]
    cek("pratinjau: GRNI 6000 + jasa tanpa jurnal (P2)", pg["grni_amount"] == "6000.00"
        and [i["stocked"] for i in pg["items"]] == [True, False, False], pg)
    r1 = await RPO.receive_goods(req(), POID, ReceiveGoodsRequest(receive_date=hari, items=[
        {"po_item_id": brg, "quantity_received": D("6")}, {"po_item_id": jasa, "quantity_received": D("1")},
        {"po_item_id": nonst, "quantity_received": D("2")}], warehouse_id=gudang["id"]))
    r1 = r1.model_dump() if hasattr(r1, "model_dump") else r1
    g1 = UUID(r1["data"]["goods_receipt_id"])
    jl = await c.fetch("SELECT account_id, debit, credit FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                       UUID(r1["data"]["journal_id"]))
    cek("GRN-1: jurnal == pratinjau (Dr Persediaan 6000 / Cr GRNI 6000)",
        [(str(x["account_id"]), str(x["debit"]), str(x["credit"])) for x in jl]
        == [(j["account_id"], j["debit"], j["credit"]) for j in pg["journal"]], (jl, pg["journal"]))
    cek("GRN-1: stok gudang +6", D(str(await stok())) == stok0 + 6, await stok())
    n_led = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", T, g1)
    n_gri = await c.fetchval("SELECT count(*) FROM goods_receipt_items WHERE goods_receipt_id = $1", g1)
    cek("GRN-1: jasa + barang non_inventory TANPA inbound & tanpa jurnal (P2; kasus grapgrap): 3 baris dokumen, 1 baris stok",
        (n_gri, n_led, len(jl)) == (3, 1, 2), (n_gri, n_led, len(jl)))
    p1 = await GP.periksa(c, T)
    cek(f"GRN-1: penjaga GRNI sehat ({p1['saldo_gl']} == {p1['terbuka_dokumen']})", p1["sehat"], p1)
    r2 = await RPO.receive_goods(req(), POID, ReceiveGoodsRequest(receive_date=hari, items=[{"po_item_id": brg, "quantity_received": D("4")}],
                                                                  warehouse_id=gudang["id"]))
    r2 = r2.model_dump() if hasattr(r2, "model_dump") else r2
    g2 = UUID(r2["data"]["goods_receipt_id"])
    if MERAH == "merah_fifo":  # GRN-2 dibuat seolah lebih dulu -> FIFO harus berubah -> cek tautan WAJIB merah
        await c.execute("UPDATE goods_receipts SET receipt_date = receipt_date - 1 WHERE id = $1", g2)
    p2 = await GP.periksa(c, T)
    cek("GRN-2: GRNI 10000, stok +10, PO diterima penuh", p2["saldo_gl"] - p1["saldo_gl"] == D("4000") and D(str(await stok())) == stok0 + 10
        and r2["data"]["status"] == "received", (p2, await stok(), r2["data"]["status"]))

    # tagihan TES E2E (simulasi jalur BACKEND): 8 @1.100 atas baris barang
    bid, biid = uuid4(), uuid4()
    await c.execute("""INSERT INTO bills (id, tenant_id, invoice_number, vendor_id, vendor_name, issue_date, due_date, amount, status, status_v2,
                       created_by) VALUES ($1, $2, 'TES E2E GRN BILL', $3, $4, $5, $5, 8800, 'posted', 'posted', $6)""",
                    bid, T, vendor["id"], vendor["name"], hari, uid)
    await c.execute("""INSERT INTO bill_items (id, bill_id, description, quantity, unit_price, subtotal, discount_amount, total, line_number,
                       purchase_order_item_id, product_id) VALUES ($1, $2, 'TES E2E barang', 8, 1100, 8800, 0, 8800, 1, $3, $4)""",
                    biid, bid, UUID(brg), prod["id"])
    bi = dict(await c.fetchrow("SELECT * FROM bill_items WHERE id = $1", biid))
    rp = await GT.rencana_posting_baris(c, T, [bi])
    if MERAH == "merah_dobel_inbound":
        rp = [{**x, "qty_inbound": x["qty_tagih"], "perlu_inbound": True} for x in rp]
    harap = [(g1, D("6")), (g2, D("2"))]
    nyata = [(await c.fetchval("SELECT goods_receipt_id FROM goods_receipt_items WHERE id = $1", t["gr_item_id"]), t["qty"]) for t in rp[0]["tautan"]]
    cek("tagihan: tautan FIFO GRN-1 6 + GRN-2 2", nyata == harap, nyata)
    cek("tagihan: nilai_grni 8000, selisih 800, qty_inbound 0 (3-way: tagih <= diterima)",
        (rp[0]["nilai_grni"], rp[0]["selisih"], rp[0]["qty_inbound"]) == (D("8000.00"), D("800.00"), D("0")), rp[0])
    await GT.tulis_tautan(c, rp)
    from app.services.sj_so import _jurnal
    jb, _ = await _jurnal(c, T, uid, prefix="TESBILL", tanggal=hari, source_type="BILL", source_id=bid, deskripsi="TES E2E GRN tagihan simulasi",
                          baris=[(akun_grni, rp[0]["nilai_grni"], D("0"), "GRNI"), (akun_ppv, rp[0]["selisih"], D("0"), "Selisih"),
                                 (akun_ap, D("0"), rp[0]["nilai_grni"] + rp[0]["selisih"], "Hutang")])
    if MERAH == "merah_penjaga":
        await _jurnal(c, T, uid, prefix="TESINJ", tanggal=hari, source_type="MANUAL", source_id=bid, deskripsi="TES E2E injeksi",
                      baris=[(akun_grni, D("0"), D("0.01"), "injeksi"), (akun_ppv, D("0.01"), D("0"), "injeksi")])
    p3 = await GP.periksa(c, T)
    cek(f"tagihan: penjaga GRNI sehat, GRNI terbuka = awal + 2000 ({p3['terbuka_dokumen']})",
        p3["sehat"] and p3["terbuka_dokumen"] == p0["terbuka_dokumen"] + D("2000"), (p0, p3))
    inbound = sum((x["qty_inbound"] for x in rp), D("0"))
    cek("tagihan: tanpa inbound ganda (stok tetap +10)", inbound == 0 and D(str(await stok())) + inbound == stok0 + 10, (inbound, await stok()))

    # void GRN-2 ditolak sesudah ditagih
    pv2 = await RGR.preview_void_goods_receipt(req(), g2, RGR.VoidGoodsReceiptPreviewRequest(reason="TES E2E"))
    cek("void GRN-2 ditolak: GRN_SUDAH_DITAGIH", [b["code"] for b in pv2["data"]["blocks"]] == ["GRN_SUDAH_DITAGIH"], pv2["data"]["blocks"])
    # balik tagihan -> void lolos -> net 0
    await GT.balik_tautan(c, [biid])
    await _jurnal(c, T, uid, prefix="TESBILLR", tanggal=hari, source_type="BILL", source_id=bid, deskripsi="TES E2E pembalik tagihan",
                  baris=[(akun_ap, rp[0]["nilai_grni"] + rp[0]["selisih"], D("0"), "Hutang"), (akun_grni, D("0"), rp[0]["nilai_grni"], "GRNI"),
                         (akun_ppv, D("0"), rp[0]["selisih"], "Selisih")], reversal_of=jb, alasan="TES E2E")
    await c.execute("UPDATE journal_entries SET reversed_by_id = (SELECT id FROM journal_entries WHERE reversal_of_id = $1) WHERE id = $1", jb)
    for g in (g2, g1):
        await RGR.void_goods_receipt(req(), g, RGR.VoidGoodsReceiptRequest(reason="TES E2E pembatalan"))
    p4 = await GP.periksa(c, T)
    qty = await c.fetchval("SELECT SUM(quantity_received) FROM purchase_order_items WHERE po_id = $1", POID)
    cek(f"net 0: GRNI dokumen sama dengan awal, penjaga sehat, stok kembali, qty diterima PO 0",
        p4["sehat"] and p4["terbuka_dokumen"] == p0["terbuka_dokumen"] and D(str(await stok())) == stok0 and D(str(qty)) == 0,
        (p4, await stok(), qty))
    gl_grn = await c.fetchval("""SELECT COALESCE(SUM(jl.credit - jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                                 WHERE je.tenant_id = $1 AND je.source_id IN ($2, $3) AND jl.account_id = $4""", T, g1, g2, akun_grni)
    cek("net 0: jurnal GRN + pembalik pada GRNI = 0", D(str(gl_grn)) == 0, gl_grn)


asyncio.run(main())
