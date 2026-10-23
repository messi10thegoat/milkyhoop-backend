"""UJI NYATA GRN SERAGAM (23 Okt 2026): rute NYATA, ROLLBACK-ONLY di kaos (flag penerimaan_barang_v1 nyala sejak V454). Mode: baru [merah|merah_grni]
PO 10 @1.000 -> GRN 6 -> daftar/detail/summary/documents/history -> tagihan 4 (terima_dulu) -> sebagian ditagih -> pratinjau batal GRN DIBLOK
(akun kode+nama, sebelum/sesudah) -> void tagihan -> belum ditagih -> void GRN -> batal, GRNI 0. Paritas:
  (p1) baris daftar ?aksi=belum_ditagih == baris yang aksi-nya belum_ditagih (SATU predikat), summary count == total ?aksi=
  (p2) Σ grni_open_amount GRN posted tenant == penjaga #1 terbuka_dokumen + tagihan_tertunda (sumber independen: grn_penjaga)
  (p3) detail == baris daftar untuk medan turunan
Kontrol merah (WAJIB FAIL): merah = GD.medan disabotase (aksi selalu None) -> p1 gagal; merah_grni = rumus grni_open disabotase (+1) -> p2 gagal."""
import asyncio, os, sys
from decimal import Decimal as D
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import goods_receipts as RGR, purchase_orders as RPO
    from app.schemas.purchase_orders import ConvertToBillRequest, CreatePurchaseOrderRequest, ReceiveGoodsRequest
    from app.services import bills_service as BS, grn_daftar as GD, grn_penjaga as GP
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    if not await c.fetchval("SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = 'penerimaan_barang_v1' AND enabled", T):
        sys.exit("FLAG GRN kaos MATI -- harness ini mengukur keadaan pasca V454")

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (RPO, RGR):
        m.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    if MERAH == "merah":
        asli = GD.medan
        GD.medan = lambda r: {**asli(r), "aksi": None, "aksi_semua": []}
    if MERAH == "merah_grni":
        asli_g = GD.sql_grni_terbuka
        GD.sql_grni_terbuka = lambda g="g": f"({asli_g(g)} + 1)"
        RGR.SQL_HEADER = RGR.SQL_HEADER.replace(asli_g("g"), f"({asli_g('g')} + 1)")
    import app.services.dashboard_izin as DI  # PolicyEngine tak hidup di kontainer sekali-pakai -> peran OWNER = semua modul boleh dibaca
    async def boleh(request, modul): return True
    DI.boleh_baca = boleh
    svc = BS.BillsService(P())
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req(q=b""):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": q}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}
        return r

    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory ORDER BY kode_produk LIMIT 1", T)
        vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
        gudang = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)

        async def daftar(**kw):
            d = await RGR.list_goods_receipts(req(), purchase_order_id=kw.get("po"), status=None, aksi=kw.get("aksi"), limit=200, offset=0)
            return d["data"]

        async def paritas(langkah, po_id):
            semua = (await daftar())["items"]
            aks = await daftar(aksi="belum_ditagih")
            sm = (await RGR.goods_receipts_summary(req(), purchase_order_id=None))["data"]
            id_aksi = sorted(x["id"] for x in semua if x["aksi"] == "belum_ditagih")
            cek(f"{langkah} (p1) ?aksi=belum_ditagih == baris ber-aksi == summary count",
                sorted(x["id"] for x in aks["items"]) == id_aksi and sm["belum_ditagih_count"] == aks["total"] == len(id_aksi),
                (len(id_aksi), aks["total"], sm))
            sig = sum((D(x["grni_open_amount"]) for x in semua if x["status"] == "posted"), D(0))
            p = await GP.periksa(c, T)
            cek(f"{langkah} (p2) Σ grni_open_amount == penjaga #1 terbuka_dokumen + tertunda ({sig})",
                sig == p["terbuka_dokumen"] + p["tagihan_tertunda"], (sig, p))
            nilai_aksi = sum((D(x["grni_open_amount"]) for x in semua if x["aksi"] == "belum_ditagih"), D(0))
            cek(f"{langkah} summary nilai == Σ grni baris ber-aksi", D(sm["belum_ditagih_value"]) == nilai_aksi, (sm, nilai_aksi))

        r = await RPO.create_purchase_order(req(), CreatePurchaseOrderRequest(
            vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E GRN SERAGAM", notes="TES E2E (rollback)", items=[
                {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang", "quantity": D("10"), "unit_price": D("1000")}]))
        r = r.model_dump() if hasattr(r, "model_dump") else r
        po = UUID(str(r["data"]["id"]))
        await RPO.send_purchase_order(req(), po)
        it = await c.fetchval("SELECT id FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number LIMIT 1", po)
        x = await RPO.receive_goods(req(), po, ReceiveGoodsRequest(receive_date=hari, items=[{"po_item_id": str(it), "quantity_received": D("6")}],
                                                                   warehouse_id=gudang))
        x = x.model_dump() if hasattr(x, "model_dump") else x
        g = UUID(x["data"]["goods_receipt_id"])
        baris = [y for y in (await daftar(po=po))["items"] if y["id"] == str(g)]
        det = (await RGR.get_goods_receipt(req(), g))["data"]
        cek("GRN 6: baris belum_ditagih, status_text 'Belum ditagih', grni_open 6000.00, aksi belum_ditagih",
            baris and baris[0]["billing_state"] == "belum_ditagih" and baris[0]["status_text"] == "Belum ditagih"
            and baris[0]["grni_open_amount"] == "6000.00" and baris[0]["aksi"] == "belum_ditagih", baris)
        cek("(p3) detail == baris (medan turunan) + bills[] kosong",
            all(det[kk] == baris[0][kk] for kk in ("billing_state", "status_text", "grni_open_amount", "aksi")) and det["bills"] == [], det)
        await paritas("GRN 6", po)
        dok = await RGR.get_goods_receipt_documents(req(), g)
        cek("documents: anchor goods_receipt, default grn, kelompok po + grn",
            dok["anchor"]["kind"] == "goods_receipt" and dok["default"] == {"group": "grn", "index": 0}
            and [k_["key"] for k_ in dok["groups"]] == ["po", "grn"] and dok["anchor"]["balance"] == "6000.00", dok)
        await RPO.convert_to_bill(req(), po, ConvertToBillRequest(bill_date=hari, notes="TES E2E GRN SERAGAM",
                                                                   items=[{"po_item_id": str(it), "quantity_to_bill": D("4")}]))
        bid = await c.fetchval("SELECT bill_id FROM bill_items WHERE purchase_order_item_id = $1", it)
        h = await svc.post_bill(T, bid, uid)
        det = (await RGR.get_goods_receipt(req(), g))["data"]
        cek("tagihan 4: sebagian_ditagih, grni_open 2000.00, bills[] 1 terima_dulu qty 4 (received 4000.00, billed 4000.00, variance 0.00)",
            h.get("success") and det["billing_state"] == "sebagian_ditagih" and det["grni_open_amount"] == "2000.00"
            and [(b["kind"], D(b["quantity"]), b["received_amount"], b["billed_amount"], b["variance"]) for b in det["bills"]]
            == [("terima_dulu", D("4"), "4000.00", "4000.00", "0.00")], (h.get("message"), det))
        dok = await RGR.get_goods_receipt_documents(req(), g)
        cek("documents: + kelompok faktur (tagihan bertaut)", [k_["key"] for k_ in dok["groups"]] == ["po", "grn", "faktur"]
            and dok["groups"][2]["docs"][0]["id"] == str(bid), dok["groups"])
        his = (await RGR.get_goods_receipt_history(req(), g, limit=200))["data"]
        jenis = sorted(e["jenis"] for e in his["events"])
        cek("history: GRN_DITERIMA + GRN_DITAGIH (bentuk events/total/omitted)",
            "GRN_DITERIMA" in jenis and "GRN_DITAGIH" in jenis and his["total"] == len(his["events"]) and his["omitted"] == [], his)
        await paritas("tagihan 4", po)
        pv = (await RGR.preview_void_goods_receipt(req(), g, RGR.VoidGoodsReceiptPreviewRequest(reason="TES E2E")))["data"]
        cek("pratinjau batal DIBLOK GRN_SUDAH_DITAGIH + jurnal pembalik ber-kode/nama akun + sebelum/sesudah",
            not pv["ok"] and [b["code"] for b in pv["blocks"]] == ["GRN_SUDAH_DITAGIH"]
            and all(j["account_code"] and j["account_name"] for j in pv["reversal_journal"]) and len(pv["reversal_journal"]) == 2
            and pv["before"] == {"status_text": "Sebagian ditagih", "grni_open_amount": "2000.00"}
            and pv["after"] == {"status_text": "Dibatalkan", "grni_open_amount": "0.00"}, pv)
        await svc.void_bill(T, bid, {"reason": "TES E2E GRN SERAGAM"}, uid)
        det = (await RGR.get_goods_receipt(req(), g))["data"]
        cek("void tagihan: kembali belum_ditagih, grni_open 6000.00, bills[] kosong",
            det["billing_state"] == "belum_ditagih" and det["grni_open_amount"] == "6000.00" and det["bills"] == [], det)
        await paritas("void tagihan", po)
        await RGR.void_goods_receipt(req(), g, RGR.VoidGoodsReceiptRequest(reason="TES E2E GRN SERAGAM"))
        det = (await RGR.get_goods_receipt(req(), g))["data"]
        dok = await RGR.get_goods_receipt_documents(req(), g)
        his = (await RGR.get_goods_receipt_history(req(), g, limit=200))["data"]
        cek("void GRN: batal, 'Dibatalkan', grni_open 0.00, aksi None; anchor balance null; history GRN_DIBATALKAN",
            det["billing_state"] == "batal" and det["status_text"] == "Dibatalkan" and det["grni_open_amount"] == "0.00" and det["aksi"] is None
            and dok["anchor"]["balance"] is None and "GRN_DIBATALKAN" in [e["jenis"] for e in his["events"]], (det, dok["anchor"]))
        await paritas("void GRN", po)
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM purchase_orders WHERE tenant_id = $1 AND ref_no = 'TES E2E GRN SERAGAM'", T)
        cek("rollback: nol PO TES E2E", sisa == 0, sisa)
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
