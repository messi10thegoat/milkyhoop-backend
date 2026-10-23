"""UJI NYATA GRN UJUNG-KE-UJUNG (MASTER 23 Okt, gerbang V454): rantai PENUH lewat handler NYATA, ROLLBACK-ONLY di kaos (TES E2E).
Mode: baru [merah|merah_fifo]
  PO barang ber-stok 10 @1.000 -> kirim -> GRN-1 6, GRN-2 4 (handler /receive, flag nyala HANYA di transaksi) -> harga PO dinaikkan 1.100 ->
  /to-bill SEBAGIAN 8 -> posting NYATA (bills_service.post_bill): tautan FIFO GRN-1 6 + GRN-2 2, Dr GRNI 8.000, Dr Selisih 800, tanpa kartu
  stok BILL -> bayar sebagian 4.000 (X-Idempotency-Key, replay = pembayaran sama) + bayar sisa (lunas) -> void pembayaran (dua-duanya) ->
  void tagihan (tautan reversed) -> void GRN-2 & GRN-1 -> NET 0: GRNI GL & dokumen = garis dasar, hutang compute_ap_outstanding = garis
  dasar, stok gudang = awal, qty diterima PO 0. Penjaga GRNI sehat di SETIAP langkah.
Kontrol merah (WAJIB FAIL): merah = penentu GRN tagihan disabotase (tagihan kembali Dr Persediaan + inbound -> GRNI tak bersih, stok dobel);
merah_fifo = GRN-2 dibuat seolah lebih dulu (tautan FIFO salah urut). Penjaga #2 (grn_penjaga.periksa_tagihan) WAJIB merah di `merah`.
  (c) satuan tagihan beda tanpa konversi -> BLOK SATUAN_TAK_TERKONVERSI (produk multi-satuan dibuat di transaksi rollback)."""
import asyncio, os, sys
from decimal import Decimal as D
from uuid import UUID, uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
FLAG = "penerimaan_barang_v1"
MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import bill_payments as RBP, bills as RB, goods_receipts as RGR, purchase_orders as RPO
    from app.schemas.bill_payments import VoidBillPaymentRequest
    from app.schemas.purchase_orders import ConvertToBillRequest, CreatePurchaseOrderRequest, ReceiveGoodsRequest
    from app.services import bills_service as BS, grn_penjaga as GP, tagihan_aksi as TA
    from app.services.role_resolver import AccountRole, resolve_account_id_by_role
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["URL"])

    class Kon:  # transaksi READ ONLY pratinjau = savepoint biasa di transaksi luar
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (RPO, RB, RGR, RBP):
        if hasattr(m, "get_pool"):
            m.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    svc = BS.BillsService(P())
    if MERAH == "merah":
        async def kosong(*a, **kw): return {"rencana": {}, "blocks": [], "akun": None}
        TA.rencana_grn = kosong
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req(kunci=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}
        return r

    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        await c.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, $2, true) "
                        "ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true", T, FLAG)
        prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory ORDER BY kode_produk LIMIT 1", T)
        vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
        gudang = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)
        bank = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)
        stok = lambda: c.fetchval("SELECT COALESCE(quantity, 0) FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 AND item_id = $3",
                                  T, gudang, prod["id"])
        ap = lambda: c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1)", T)
        stok0, ap0, p0 = D(str(await stok() or 0)), D(str(await ap())), await GP.periksa(c, T)

        async def sehat(langkah):
            p = await GP.periksa(c, T)
            cek(f"{langkah}: penjaga GRNI sehat ({p['saldo_gl']} == {p['terbuka_dokumen']})", p["sehat"], p)
            p2 = await GP.periksa_tagihan(c, T)
            cek(f"{langkah}: penjaga #2 tagihan sehat (ganda {p2['ganda']}, tanpa_tautan {p2['tanpa_tautan']})", p2["sehat"], p2)
            return p

        r = await RPO.create_purchase_order(req(), CreatePurchaseOrderRequest(
            vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E GRN E2E", notes="TES E2E (rollback)", items=[
                {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang", "quantity": D("10"), "unit_price": D("1000")}]))
        r = r.model_dump() if hasattr(r, "model_dump") else r
        po = UUID(str(r["data"]["id"]))
        await RPO.send_purchase_order(req(), po)
        it = (await c.fetchval("SELECT id FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number LIMIT 1", po))
        g = []
        for q in ("6", "4"):
            x = await RPO.receive_goods(req(), po, ReceiveGoodsRequest(receive_date=hari, items=[{"po_item_id": str(it), "quantity_received": D(q)}],
                                                                       warehouse_id=gudang))
            x = x.model_dump() if hasattr(x, "model_dump") else x
            g.append(UUID(x["data"]["goods_receipt_id"]))
        if MERAH == "merah_fifo":
            await c.execute("UPDATE goods_receipts SET receipt_date = receipt_date - 1 WHERE id = $1", g[1])
        await sehat("GRN 6 + 4")
        cek("GRN: stok +10", D(str(await stok())) == stok0 + 10, await stok())
        await c.execute("UPDATE purchase_order_items SET unit_price = 1100, subtotal = quantity * 1100, total = quantity * 1100 WHERE id = $1", it)
        await RPO.convert_to_bill(req(), po, ConvertToBillRequest(bill_date=hari, notes="TES E2E GRN E2E", items=[{"po_item_id": str(it), "quantity_to_bill": D("8")}]))
        bid = await c.fetchval("SELECT bi.bill_id FROM bill_items bi WHERE bi.purchase_order_item_id = $1", it)
        h = await svc.post_bill(T, bid, uid)
        cek("posting tagihan sebagian (8) berhasil", h["success"], h.get("message"))
        tl = await c.fetch("""SELECT i.goods_receipt_id, l.quantity, l.grni_amount, l.variance_amount FROM goods_receipt_bill_links l
                              JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id JOIN bill_items bi ON bi.id = l.bill_item_id
                              WHERE bi.bill_id = $1 AND l.status = 'active' ORDER BY i.goods_receipt_id = $2 DESC""", bid, g[0])
        cek("(a) tautan FIFO NYATA: GRN-1 6 + GRN-2 2, GRNI 8.000, selisih 800",
            [(x["goods_receipt_id"], D(x["quantity"])) for x in tl] == [(g[0], D("6")), (g[1], D("2"))]
            and sum(D(x["grni_amount"]) for x in tl) == 8000 and sum(D(x["variance_amount"]) for x in tl) == 800, [dict(x) for x in tl])
        n_bill_stok = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'BILL' AND source_id = $2", T, bid)
        cek("(a) tanpa kartu stok BILL (stok masuk sekali saat GRN), stok tetap +10", n_bill_stok == 0 and D(str(await stok())) == stok0 + 10,
            (n_bill_stok, await stok()))
        p = await sehat("posting")
        cek("posting: GRNI terbuka = garis dasar + 2.000", p["terbuka_dokumen"] == p0["terbuka_dokumen"] + 2000, (p0, p))
        tagih = D(str(await c.fetchval("SELECT amount FROM bills WHERE id = $1", bid)))
        k1, k2 = f"TES-E2E-GRN-BAYAR-{uuid4()}", f"TES-E2E-GRN-BAYAR-{uuid4()}"
        badan1 = {"amount": 4000, "payment_date": hari, "payment_method": "transfer", "bank_account_id": bank}
        ctx = {"tenant_id": T, "user_id": str(uid)}
        b1 = await svc.record_payment(T, bid, badan1, uid, idem={"ctx": ctx, "kunci": k1, "isi": {**badan1, "payment_date": str(hari), "bank_account_id": str(bank)}})
        b1u = await svc.record_payment(T, bid, badan1, uid, idem={"ctx": ctx, "kunci": k1, "isi": {**badan1, "payment_date": str(hari), "bank_account_id": str(bank)}})
        id1 = (b1.get("data") or {}).get("id")
        cek("(d) bayar sebagian 4.000 + replay kunci sama = pembayaran SAMA (Law 14)", b1.get("success") and id1 and (b1u.get("data") or {}).get("id") == id1,
            (b1.get("message"), id1, (b1u.get("data") or {}).get("id")))
        sisa = int(tagih) - 4000
        badan2 = {"amount": sisa, "payment_date": hari, "payment_method": "transfer", "bank_account_id": bank}
        b2 = await svc.record_payment(T, bid, badan2, uid, idem={"ctx": ctx, "kunci": k2, "isi": {**badan2, "payment_date": str(hari), "bank_account_id": str(bank)}})
        id2 = (b2.get("data") or {}).get("id")
        cek("(d) bayar sisa -> lunas", b2.get("success") and (b2.get("data") or {}).get("bill_status") in ("paid", "Lunas", "lunas"), (b2.get("message"), b2.get("data")))
        await sehat("lunas")
        cek("(d) hutang lunas = garis dasar", D(str(await ap())) == ap0, (await ap(), ap0))
        for pid in (id2, id1):
            await RBP.void_bill_payment(req(), pid, VoidBillPaymentRequest(void_reason="TES E2E GRN E2E"))
        cek("(d) void 2 pembayaran: hutang kembali = garis dasar + tagihan", D(str(await ap())) == ap0 + tagih, (await ap(), ap0, tagih))
        await sehat("void pembayaran")
        hv = await svc.void_bill(T, bid, {"reason": "TES E2E GRN E2E"}, uid)
        st = await c.fetchval("""SELECT string_agg(DISTINCT l.status, ',') FROM goods_receipt_bill_links l JOIN bill_items bi ON bi.id = l.bill_item_id
                                 WHERE bi.bill_id = $1""", bid)
        cek("(b) void tagihan berhasil, tautan reversed", hv.get("success") and st == "reversed", (hv.get("message"), st))
        p = await sehat("void tagihan")
        cek("void tagihan: GRNI terbuka = garis dasar + 10.000", p["terbuka_dokumen"] == p0["terbuka_dokumen"] + 10000, p)
        for x in (g[1], g[0]):
            await RGR.void_goods_receipt(req(), x, RGR.VoidGoodsReceiptRequest(reason="TES E2E GRN E2E"))
        p = await sehat("void GRN")
        # (c) satuan tagihan beda dgn satuan PO pada baris ber-GRN: BLOK SATUAN_TAK_TERKONVERSI (pratinjau == galat posting), bukan tebakan.
        #     Produk multi-satuan + konversi dibuat DI DALAM transaksi rollback (data master TES E2E, tak pernah ter-commit).
        await c.execute("UPDATE products SET base_unit = COALESCE(base_unit, satuan, 'pcs') WHERE id = $1", prod["id"])
        r3 = await RPO.create_purchase_order(req(), CreatePurchaseOrderRequest(
            vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E GRN E2E", items=[
                {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang lusin", "quantity": D("24"),
                 "unit": "pcs", "unit_price": D("1000")}]))
        r3 = r3.model_dump() if hasattr(r3, "model_dump") else r3
        po3 = UUID(str(r3["data"]["id"]))
        await RPO.send_purchase_order(req(), po3)
        it3 = await c.fetchval("SELECT id FROM purchase_order_items WHERE po_id = $1", po3)
        await RPO.receive_goods(req(), po3, ReceiveGoodsRequest(receive_date=hari, items=[{"po_item_id": str(it3), "quantity_received": D("24")}],
                                                                warehouse_id=gudang))
        await RPO.convert_to_bill(req(), po3, ConvertToBillRequest(bill_date=hari, notes="TES E2E GRN E2E lusin"))
        bid3 = await c.fetchval("SELECT bill_id FROM bill_items WHERE purchase_order_item_id = $1", it3)
        await c.execute("UPDATE bill_items SET unit = 'lusin', quantity = 2 WHERE bill_id = $1", bid3)
        pv3 = (await RB.preview_post_bill(req(), bid3))["data"]
        kode3 = [b["code"] for b in pv3.get("blocks", [])]
        h3 = await svc.post_bill(T, bid3, uid)
        cek("(c) satuan lusin vs pcs tanpa konversi: pratinjau blok SATUAN_TAK_TERKONVERSI == galat posting, tagihan tak terposting",
            "SATUAN_TAK_TERKONVERSI" in kode3 and not h3.get("success")
            and await c.fetchval("SELECT journal_id FROM bills WHERE id = $1", bid3) is None, (kode3, h3.get("message")))
        qty = D(str(await c.fetchval("SELECT quantity_received FROM purchase_order_items WHERE id = $1", it)))
        cek("(b) NET 0: GRNI GL & dokumen = garis dasar, hutang = garis dasar, stok = awal, qty diterima PO 0",
            p["saldo_gl"] == p0["saldo_gl"] and p["terbuka_dokumen"] == p0["terbuka_dokumen"] and D(str(await ap())) == ap0
            and D(str(await stok())) == stok0 and qty == 0, (p0, p, await ap(), ap0, await stok(), stok0, qty))
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM purchase_orders WHERE tenant_id = $1 AND ref_no = 'TES E2E GRN E2E'", T)
        sisa_f = await c.fetchval("SELECT count(*) FROM tenant_features WHERE feature = $1", FLAG)
        cek("rollback: nol PO TES E2E, flag tetap 0 baris", sisa == 0 and sisa_f == 0, (sisa, sisa_f))
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
