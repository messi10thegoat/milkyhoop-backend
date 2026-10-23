"""UJI NYATA posting tagihan <-> GRN (sisi BACKEND, 23 Okt 2026). kaos SAJA (grapgrap tak disentuh); SEMUA di SATU transaksi luar yang SELALU
di-ROLLBACK. Flag penerimaan_barang_v1 dinyalakan HANYA di dalam transaksi itu (putusan MASTER: pembaca flag = grn_rencana.aktif(conn) lewat
koneksi tertambal, tanpa cache; tak ter-commit = bukan perubahan flag). Net 0 sesudah proses selesai TERMASUK tenant_features.
Alur (handler NYATA): PO (barang dilacak 10 @1.000 + jasa 1 @500) -> kirim -> terima 10 (GRN, Dr Persediaan / Cr GRNI 10.000) -> harga baris
PO dinaikkan 1.100 (harga faktur vendor beda) -> /to-bill (draf) -> pratinjau posting -> posting -> void.
  (a) pratinjau == jurnal tertulis (akun, debit, kredit, memo)
  (b) jurnal tagihan: Dr GRNI 10.000 + Dr Selisih Harga 1.000 (barang) + Dr beban jasa; Cr Utang = grand_total; TANPA Dr Persediaan
  (c) TANPA kartu stok BILL untuk barang ber-GRN (stok masuk sekali, saat GRN)
  (d) tautan aktif 1, grni 10.000; saldo GRNI dokumen ini (jurnal GRN + tagihan) = 0
  (e) void: tautan 'reversed', jurnal pembalik mencerminkan GRNI/PPV, saldo GRNI kembali 10.000 (barang diterima belum ditagih lagi)
  ATURAN D (23 Okt, putusan pemilik; kontrak BACKEND<->BACKEND2):
  (f) PO kedua terima 6 dari 10, tagih 10 -> posting LOLOS: Dr GRNI 6.000 (terima) + 4.000 (bagian belum diterima, B), tanpa PPV, tanpa
      Dr Persediaan, tanpa kartu stok BILL; tautan terima_dulu 1 (qty 6); pratinjau == tertulis
  (g) qty tagihan dinaikkan 11 > pesanan 10 (sintetis, savepoint) -> pratinjau blok PO_QTY_TERLAMPAUI == pesan galat posting
  (h) tautan 'tagih_dulu' sintetis atas tagihan (f) (penerimaan sesudah tagihan) -> pratinjau void blok TAGIHAN_TERCOCOK_GRN == pesan galat
      void (blok DULUAN, bukan ValueError grn_tagih.balik_tautan)
MODE JEPRET/BANDING: net 0 (jurnal, baris, stok, tagihan, GRN, tautan, tenant_features + baris flag kaos, idem).
MERAH=1 : rencana_grn disabotase (selalu kosong) -> jurnal kembali ke Persediaan -> (b)/(c)/(d) WAJIB gagal.
MERAH_BOCOR=1 : 1 baris idempotency_keys lewat koneksi terpisah yang COMMIT -> net 0 WAJIB gagal (.sh membersihkan + sisa 0)."""
import asyncio, json, os, sys
from decimal import Decimal as D
from uuid import UUID
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
FLAG = "penerimaan_barang_v1"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH, BOCOR = os.environ.get("MERAH") == "1", os.environ.get("MERAH_BOCOR") == "1"
KUNCI_BOCOR = "TES-E2E-TAGIHAN-GRN-BOCOR"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0) FROM journal_entries WHERE tenant_id = $1",
    "stok": "SELECT count(*), COALESCE(sum(quantity_in - quantity_out),0) FROM inventory_ledger WHERE tenant_id = $1",
    "tagihan": "SELECT count(*) FROM bills WHERE tenant_id = $1",
    "po": "SELECT count(*) FROM purchase_orders WHERE tenant_id = $1",
    "grn": "SELECT count(*) FROM goods_receipts WHERE tenant_id = $1",
    "tautan": "SELECT count(*) FROM goods_receipt_bill_links l JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id "
              "JOIN goods_receipts g ON g.id = i.goods_receipt_id WHERE g.tenant_id = $1",
    "tenant_features": "SELECT count(*), bool_or(feature = '" + FLAG + "') FROM tenant_features WHERE tenant_id = $1",
    "hutang": "SELECT COALESCE(sum(outstanding),0) FROM compute_ap_outstanding($1)",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
}


async def jurnal_baris(c, jid):
    return [(r["account_code"], str(D(r["debit"]).normalize()), str(D(r["credit"]).normalize()), r["memo"]) for r in await c.fetch(
        """SELECT coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
           WHERE jl.journal_id = $1 ORDER BY jl.line_number""", jid)]


async def tulis():
    from app.routers import purchase_orders as RPO
    from app.routers import bills as RB
    from app.services import bills_service as BS
    from app.services import tagihan_aksi as TA
    from app.services.role_resolver import AccountRole, resolve_account_id_by_role
    from app.schemas.purchase_orders import CreatePurchaseOrderRequest, ReceiveGoodsRequest, ConvertToBillRequest
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["DATABASE_URL"])

    class Kon:  # transaksi READ ONLY pratinjau = savepoint biasa di transaksi luar (melihat data sintetis; penentu tetap tanpa tulis)
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    RPO.get_pool = gp; RB.get_pool = gp
    svc = BS.BillsService(P())
    if MERAH:
        async def kosong(*a, **kw): return {"rencana": {}, "blocks": [], "akun": None}
        TA.rencana_grn = kosong
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req():
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}; return r
    tx = c.transaction(); await tx.start()
    try:
        if BOCOR:
            b = await asyncpg.connect(os.environ["DATABASE_URL"])
            await b.execute("INSERT INTO idempotency_keys (key, tenant_id, expires_at) VALUES ($1, $2, NOW() + interval '1 hour')", KUNCI_BOCOR, T)
            await b.close()
        mati = os.environ.get("FLAG_MATI") == "1"
        if not mati:
            await c.execute("""INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, $2, true)
                               ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true""", T, FLAG)
        hari = await tanggal_dokumen(c, T)
        prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory ORDER BY kode_produk LIMIT 1", T)
        vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
        grni = await resolve_account_id_by_role(c, T, AccountRole.GOODS_RECEIVED_NOT_INVOICED)
        kode_grni = await c.fetchval("SELECT account_code FROM chart_of_accounts WHERE id = $1", grni)
        ppv = await c.fetchval("SELECT account_code FROM chart_of_accounts WHERE id = $1",
                               await resolve_account_id_by_role(c, T, AccountRole.PURCHASE_PRICE_VARIANCE))

        async def po_terima(qty_po, qty_terima, harga_baru):
            r = await RPO.create_purchase_order(req(), CreatePurchaseOrderRequest(
                vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, ref_no="TES E2E tagihan-GRN", items=[
                    {"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "TES E2E barang", "quantity": D(qty_po), "unit_price": D("1000")},
                    {"description": "TES E2E jasa", "quantity": D("1"), "unit_price": D("500")}]))
            r = r.model_dump() if hasattr(r, "model_dump") else r
            po = UUID(str(r["data"]["id"]))
            await RPO.send_purchase_order(req(), po)
            it = [x["id"] for x in await c.fetch("SELECT id FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number", po)]
            await RPO.receive_goods(req(), po, ReceiveGoodsRequest(receive_date=hari, items=[
                {"po_item_id": str(it[0]), "quantity_received": D(qty_terima)}, {"po_item_id": str(it[1]), "quantity_received": D("1")}]))
            if harga_baru:  # harga faktur vendor beda dgn harga terima (sintetis, rollback)
                await c.execute("UPDATE purchase_order_items SET unit_price = $2, subtotal = quantity * $2, total = quantity * $2 WHERE id = $1",
                                it[0], D(harga_baru))
            await RPO.convert_to_bill(req(), po, ConvertToBillRequest(bill_date=hari, notes="TES E2E tagihan-GRN (rollback)"))
            bid = await c.fetchval("SELECT DISTINCT bi.bill_id FROM bill_items bi WHERE bi.purchase_order_item_id = $1", it[0])
            return po, bid

        po, bid = await po_terima("10", "10", "1100")
        if mati:  # flag MATI: rekam jurnal + stok posting tagihan dari PO -> banding pohon LAMA vs BARU (byte-identik)
            await svc.post_bill(T, bid, uid)
            jid = await c.fetchval("SELECT journal_id FROM bills WHERE id = $1", bid)
            rek = {"jurnal": await jurnal_baris(c, jid),
                   "stok": [(str(D(r["quantity_in"]).normalize()), str(D(r["unit_cost"]).normalize())) for r in await c.fetch(
                       "SELECT quantity_in, unit_cost FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'BILL' AND source_id = $2", T, bid)],
                   "header": str(D(await c.fetchval("SELECT total_debit FROM journal_entries WHERE id = $1", jid)).normalize())}
            json.dump(rek, open(f"/out/{NAMA}.json", "w"), default=str)
            print(f"MATI {NAMA} {rek}", flush=True)
            return
        pv = (await RB.preview_post_bill(req(), bid))["data"]
        hasil = await svc.post_bill(T, bid, uid)
        cek(hasil["success"], "posting tagihan dari PO ber-GRN berhasil", str(hasil.get("message")))
        jid = await c.fetchval("SELECT journal_id FROM bills WHERE id = $1", bid)
        tertulis = await jurnal_baris(c, jid)
        rencana = [(x["account_code"], str(D(str(x["debit"])).normalize()), str(D(str(x["credit"])).normalize()), x["memo"]) for x in pv["journal_lines"]]
        cek(rencana == tertulis, "(a) pratinjau posting == jurnal tertulis", "" if rencana == tertulis else f"{rencana} / {tertulis}")
        akun = {x[0]: x for x in tertulis}
        persediaan = await c.fetchval("SELECT account_code FROM chart_of_accounts WHERE id = $1",
                                      await resolve_account_id_by_role(c, T, AccountRole.INVENTORY_MERCHANDISE))
        cek(akun.get(kode_grni, (0, "0"))[1] == "1E+4" and akun.get(ppv, (0, "0"))[1] == "1E+3" and persediaan not in akun,
            "(b) Dr GRNI 10.000 + Dr selisih harga 1.000, TANPA Dr Persediaan", str(tertulis))
        hdr = await c.fetchrow("SELECT total_debit, total_credit FROM journal_entries WHERE id = $1", jid)
        sd = sum((D(x[1]) for x in tertulis), D(0)); sk = sum((D(x[2]) for x in tertulis), D(0))
        cek(sd == sk == D(hdr["total_debit"]) == D(hdr["total_credit"]), "(b) Law 4: Σ debit == Σ kredit == header", f"{sd}/{sk}/{hdr['total_debit']}")
        n_stok = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'BILL' AND source_id = $2", T, bid)
        cek(n_stok == 0, "(c) tanpa kartu stok BILL untuk barang ber-GRN (stok masuk sekali)", str(n_stok))
        tl = await c.fetch("""SELECT l.status, l.grni_amount, l.variance_amount FROM goods_receipt_bill_links l JOIN bill_items bi ON bi.id = l.bill_item_id
                              WHERE bi.bill_id = $1""", bid)
        cek(len(tl) == 1 and tl[0]["status"] == "active" and D(tl[0]["grni_amount"]) == 10000 and D(tl[0]["variance_amount"]) == 1000,
            "(d) tautan aktif 1, grni 10.000, variance 1.000", str([dict(x) for x in tl]))
        sql_grni = """SELECT COALESCE(SUM(jl.credit - jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                      WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.status = 'POSTED' AND (je.source_id = $3 OR je.source_id IN
                      (SELECT id FROM goods_receipts WHERE purchase_order_id = $4))"""
        cek(D(await c.fetchval(sql_grni, T, grni, bid, po)) == 0, "(d) saldo GRNI dokumen ini (GRN + tagihan) = 0")
        hv = await svc.void_bill(T, bid, {"reason": "TES E2E tagihan-GRN"}, uid)
        cek(hv["success"], "void tagihan berhasil", str(hv.get("message")))
        st = await c.fetchval("""SELECT string_agg(l.status, ',') FROM goods_receipt_bill_links l JOIN bill_items bi ON bi.id = l.bill_item_id
                                 WHERE bi.bill_id = $1""", bid)
        cek(st == "reversed", "(e) void: tautan 'reversed'", str(st))
        sisa = D(await c.fetchval(sql_grni, T, grni, bid, po))
        cek(sisa == 10000, "(e) void: saldo GRNI kembali 10.000 (diterima belum ditagih)", str(sisa))
        # (f) ATURAN D: terima 6 dari 10, tagih 10 -> lolos, bagian belum diterima ke GRNI
        po2, bid2 = await po_terima("10", "6", None)
        # (g) PO_QTY_TERLAMPAUI: qty tagihan 11 > pesanan 10 (savepoint, dibatalkan)
        it2 = await c.fetchval("SELECT bi.id FROM bill_items bi JOIN purchase_order_items poi ON poi.id = bi.purchase_order_item_id "
                               "WHERE bi.bill_id = $1 AND poi.item_id IS NOT NULL", bid2)
        sp = c.transaction(); await sp.start()
        try:
            await c.execute("UPDATE bill_items SET quantity = 11 WHERE id = $1", it2)
            pvq = (await RB.preview_post_bill(req(), bid2))["data"]
            kq = [b["code"] for b in pvq.get("blocks", [])]
            hq = await svc.post_bill(T, bid2, uid)
        finally:
            await sp.rollback()
        cek("PO_QTY_TERLAMPAUI" in kq and not hq["success"] and hq["message"] == pvq["blocks"][kq.index("PO_QTY_TERLAMPAUI")]["message"],
            "(g) tagih 11 > pesan 10: pratinjau blok PO_QTY_TERLAMPAUI == pesan galat posting", f"{kq} / {hq.get('message')}")
        pv2 = (await RB.preview_post_bill(req(), bid2))["data"]
        h2 = await svc.post_bill(T, bid2, uid)
        cek(h2["success"] and not pv2.get("blocks"), "(f) D: tagih 10 atas terima 6 LOLOS (blok 7c dicabut)", f"{pv2.get('blocks')} / {h2.get('message')}")
        jid2 = await c.fetchval("SELECT journal_id FROM bills WHERE id = $1", bid2)
        tertulis2 = await jurnal_baris(c, jid2)
        rencana2 = [(x["account_code"], str(D(str(x["debit"])).normalize()), str(D(str(x["credit"])).normalize()), x["memo"]) for x in pv2["journal_lines"]]
        cek(rencana2 == tertulis2, "(f) pratinjau posting == jurnal tertulis", "" if rencana2 == tertulis2 else f"{rencana2} / {tertulis2}")
        akun2 = {x[0]: x for x in tertulis2}
        cek(akun2.get(kode_grni, (0, "0"))[1] == "1E+4" and ppv not in akun2 and persediaan not in akun2,
            "(f) Dr GRNI 10.000 (6.000 terima + 4.000 tertunda), tanpa PPV, tanpa Dr Persediaan", str(tertulis2))
        n_stok2 = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'BILL' AND source_id = $2", T, bid2)
        tl2 = await c.fetch("""SELECT l.kind, l.quantity, l.grni_amount, l.nilai_tagih FROM goods_receipt_bill_links l JOIN bill_items bi ON bi.id = l.bill_item_id
                               WHERE bi.bill_id = $1 AND l.status = 'active'""", bid2)
        cek(n_stok2 == 0 and len(tl2) == 1 and tl2[0]["kind"] == "terima_dulu" and D(tl2[0]["quantity"]) == 6 and D(tl2[0]["grni_amount"]) == 6000,
            "(f) tanpa kartu stok BILL; tautan terima_dulu qty 6 grni 6.000", f"{n_stok2} / {[dict(x) for x in tl2]}")
        # (h) penerimaan SESUDAH tagihan (tautan tagih_dulu sintetis) -> void diblok lebih dulu
        gri = await c.fetchval("""SELECT gi.id FROM goods_receipt_items gi JOIN goods_receipts g ON g.id = gi.goods_receipt_id
                                  WHERE g.purchase_order_id = $1 AND gi.ber_stok LIMIT 1""", po2)
        await c.execute("""INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount, variance_amount, status, kind, nilai_tagih)
                           VALUES ($1, $2, 1, 1000, 0, 'active', 'tagih_dulu', 1000)""", gri, it2)
        pvv = (await RB.preview_void_bill(req(), bid2, {"reason": "TES E2E tagihan-GRN"}))["data"]
        kv = [b["code"] for b in pvv.get("blocks", [])]
        hv2 = await svc.void_bill(T, bid2, {"reason": "TES E2E tagihan-GRN"}, uid)
        cek(kv[:1] == ["TAGIHAN_TERCOCOK_GRN"] and not hv2["success"] and hv2["message"] == pvv["blocks"][0]["message"],
            "(h) void tagihan bertaut penerimaan sesudahnya: blok TAGIHAN_TERCOCOK_GRN == pesan galat void", f"{kv} / {hv2.get('message')}")
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "TULIS":
        return await tulis()
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump({kk: [str(v) for v in (await c.fetchrow(q, T)).values()] for kk, q in SQL_JEPRET.items()}, open(f"/out/{NAMA}.json", "w"))
        await c.close(); print(f"JEPRET {NAMA} ok"); return
    if os.path.exists("/out/mati_lama.json"):
        a, b = json.load(open("/out/mati_lama.json")), json.load(open("/out/mati_baru.json"))
        cek(a == b and a["stok"], "flag MATI: posting tagihan dari PO byte-identik LAMA == BARU (jurnal, header, kartu stok BILL)",
            "" if a == b else f"{a} / {b}")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk], f"NET 0 (sesudah proses selesai): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
