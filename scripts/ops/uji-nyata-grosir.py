"""Uji NYATA alur GROSIR ujung-ke-ujung (5 Nov 2026, BACKEND2; brief MASTER "grosir ready" sisi server). Handler asli di atas DB
produksi, tenant kaos-biru-konveksi (non-PKP), SATU transaksi luar yang di-ROLLBACK; grapgrap-manado hanya sidik md5; kanal keluar diblokir.
Barang "Minyak uji" dibuat DI DALAM rollback: dasar Botol, 1 Dus = 12, 1 Karton = 10 Dus = 120 (simpan datar terhadap dasar).
  L1 PO 2 Karton @2.400.000 + PPN 11% -> GRN 2 Karton -> FPB dari PO/GRN (3-arah) -> posting:
     kartu +240 Botol @22.200 (5.328.000/240, PPN dikapitalisasi), hutang rupiah penuh, jurnal seimbang, nol PPN Masukan.
  L2 Daftar Harga (Botol 25.000, Dus 280.000) -> resolve -> SO 1 Dus + 5 Botol -> konfirmasi -> SJ -> SI terbit:
     keluar 17 Botol x WAC = 377.400, HPP faktur = 377.400, total SI rupiah penuh = 405.000.
  L3 Terima pembayaran: nominal bersen -> ditolak "Nominal harus rupiah penuh." nol tulis; rupiah penuh -> sisa 0.
  L4 Pindah gudang 1 Dus -> asal -12, tujuan +12 Botol, WAC tetap.
  L5 ND retur 1 Dus atas FPB -> kartu -12 Botol, Dr Hutang = total BULAT, Cr Retur = total (kapitalisasi balik), seimbang.
  L6 ROLLBACK net 0, grapgrap md5 sama, kanal keluar 0.
SABOT=sd -> satuan_dasar.hitung mengabaikan konversi (faktor 1) -> WAJIB merah. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")
KELUAR = []


def _pagar():
    import smtplib

    def catat(nama):
        def f(*a, **k):
            KELUAR.append(nama)
            raise RuntimeError(f"harness: kanal keluar {nama} diblokir")
        return f

    async def acatat(*a, **k):
        KELUAR.append("async-http")
        raise RuntimeError("harness: kanal keluar diblokir")
    smtplib.SMTP.__init__ = catat("smtp")
    smtplib.SMTP_SSL.__init__ = catat("smtp_ssl")
    for modul, kelas, attr, f in (("httpx", "Client", "send", catat("httpx")), ("httpx", "AsyncClient", "send", acatat),
                                  ("requests", "Session", "send", catat("requests")), ("aiohttp", "ClientSession", "_request", acatat)):
        try:
            setattr(getattr(__import__(modul), kelas), attr, f)
        except ImportError:
            pass


_pagar()
from fastapi import HTTPException, Response  # noqa: E402
from starlette.requests import Request  # noqa: E402

T, LAIN = "kaos-biru-konveksi", "grapgrap-manado"
VENDOR = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7"
PENANDA = "TES E2E GROSIR"
D = Decimal
SABOT = os.environ.get("SABOT", "")
TABEL = ("products", "unit_conversions", "price_lists", "price_list_items", "purchase_orders", "goods_receipts", "bills", "sales_orders",
         "invoice_fulfillments", "sales_invoices", "receive_payments", "stock_transfers", "vendor_credits", "journal_entries",
         "inventory_ledger", "warehouse_stock", "audit_logs", "idempotency_keys")


def req(uid, kunci=None, badan=None, metode="POST"):
    isi = json.dumps(badan, default=str).encode() if badan is not None else b""

    async def receive(): return {"type": "http.request", "body": isi, "more_body": False}
    h = [(b"content-type", b"application/json")] + ([(b"x-idempotency-key", kunci.encode())] if kunci else [])
    r = Request({"type": "http", "method": metode, "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}
    return r


def dt(x):
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else x


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    for t in (T, LAIN):
        assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', t), f"tenant {t} tak ada"
    from app.services import db_pool, satuan_dasar as SD
    from app.routers import (items as RI, price_lists as RPL, purchase_orders as RPO, bills as RB, sales_orders as RSO, sj_so as RSJ,
                             sales_invoices as RSI, receive_payments as RRP, stock_transfers as RST, vendor_credits as RVC)
    from app.schemas.items import CreateItemRequest as CIR
    from app.schemas.price_lists import CreatePriceListRequest as CPL, ResolveHargaRequest as RHR
    from app.schemas.purchase_orders import CreatePurchaseOrderRequest as CPO, ReceiveGoodsRequest as RGR, ConvertToBillRequest as CTB
    from app.schemas.sales_orders import CreateSalesOrderRequest as CSO, ConvertToInvoiceRequest as CTI
    from app.schemas.receive_payments import CreateReceivePaymentRequest as CRP
    from app.schemas.stock_transfers import CreateStockTransferRequest as CST
    from app.schemas.vendor_credits import CreateVendorCreditRequest as CVR
    from app.services import sj_so as SSJ
    from app.utils.tanggal_tenant import tanggal_dokumen
    if SABOT == "sd":  # kontrol merah: konversi diabaikan di penentu SATU (faktor 1, qty mentah)
        _asli = SD.hitung

        def _sab(ktx, product_id, unit, qty, mode, aksi="Pengiriman"):
            h = _asli(ktx, product_id, unit, qty, mode, aksi)
            if not h["blok"]:
                h.update(qty_dasar=D(str(qty or 0)), faktor=SD.SATU)
            return h
        SD.hitung = _sab
        print("SABOTASE sd: satuan_dasar.hitung mengabaikan konversi", flush=True)

    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    hari = await tanggal_dokumen(c, T)
    vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", uuid.UUID(VENDOR), T)
    pkp = await c.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T)
    g_utama = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_default ORDER BY created_at LIMIT 1", T)
    g_lain = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND id <> $2 AND COALESCE(is_active, true) ORDER BY created_at LIMIT 1", T, g_utama)
    hasil, cek = {}, []

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi)})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {json.dumps(info, default=str)[:1500]}"), flush=True)

    class Kon:
        def __init__(s, k): s.k = k
        def __getattr__(s, a): return getattr(s.k, a)
        def transaction(s, **kw): return s.k.transaction()
        async def close(s): return None

    class CM:
        def __init__(s, k): s.k = k
        async def __aenter__(s): return s.k
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM(Kon(c))
        async def release(s, *a): return None

    async def gp(): return Pool()

    async def gk(): return Kon(c)
    for mod in (RI, RPL, RPO, RB, RSO, RSI, RRP, RST, RVC):
        if hasattr(mod, "get_pool"):
            mod.get_pool = gp
    RSJ._pool = gp
    RI.get_db_connection = gk
    db_pool.get_db_pool = gp

    async def jalan(nama, f):
        try:
            async with c.transaction():
                st, b = 200, dt(await f())
        except HTTPException as e:
            st, b = e.status_code, {"detail": e.detail}
        except Exception as e:
            st = getattr(e, "status_code", None) or getattr(e, "status", None) or 500
            b = {"detail": getattr(e, "message", None) or f"{type(e).__name__}: {str(e)[:400]}", "code": getattr(e, "code", None)}
        hasil[nama] = {"status": st, "body": b}
        print(f"  {nama}: st={st} body={str(b)[:240]}", flush=True)
        return st, b

    def data(b):
        return ((b or {}).get("data") or {}) if isinstance(b, dict) else {}

    def pesan(b):
        d = (b or {}).get("detail") if isinstance(b, dict) else None
        return d if isinstance(d, str) else (d or {}).get("message") if isinstance(d, dict) else str(b)

    def uu(x):
        return uuid.UUID(str(x)) if x else None

    async def tabel_ada(t):
        return await c.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{t}")
    tabel = [t for t in TABEL if await tabel_ada(t)]

    async def hitung():
        return {t: await c.fetchval(f"SELECT count(*) FROM {t} WHERE tenant_id = ANY($1)", [T, LAIN]) for t in tabel}  # nosec B608 (daftar tetap)

    async def sidik():
        return await c.fetchval("""SELECT md5(
            COALESCE((SELECT string_agg(id::text||quantity_in::text||quantity_out::text, ',' ORDER BY id) FROM inventory_ledger WHERE tenant_id = $1), '')
            || COALESCE((SELECT string_agg(id::text||status||total_debit::text, ',' ORDER BY id) FROM journal_entries WHERE tenant_id = $1), '')
            || COALESCE((SELECT string_agg(item_id::text||warehouse_id::text||quantity::text, ',' ORDER BY item_id, warehouse_id) FROM warehouse_stock WHERE tenant_id = $1), '')
            || COALESCE((SELECT string_agg(id::text||COALESCE(price_list_id::text, '-'), ',' ORDER BY id) FROM customers WHERE tenant_id = $1), ''))""", LAIN)

    async def sidik_pelanggan_kaos():
        return await c.fetchval("SELECT md5(string_agg(id::text||COALESCE(price_list_id::text, '-'), ',' ORDER BY id)) FROM customers WHERE tenant_id = $1", T)
    awal, s0, sp0 = await hitung(), await sidik(), await sidik_pelanggan_kaos()
    harus("persiapan: kaos non-PKP, vendor uji, 2 gudang", pkp is False and vendor is not None and g_utama and g_lain, {"pkp": pkp, "g": [g_utama, g_lain]})

    luar = c.transaction()
    await luar.start()
    try:
        # ================= L0 barang uji (dalam rollback)
        nama = f"Minyak uji {PENANDA} {uuid.uuid4().hex[:6]}"
        st, b = await jalan("L0 buat barang", lambda: RI.create_item(req(uid), CIR(
            name=nama, item_type="goods", track_inventory=True, base_unit="Botol", sales_price=25000,
            conversions=[{"conversion_unit": "Dus", "conversion_factor": 12}, {"conversion_unit": "Karton", "conversion_factor": 120}]), Response()))
        P = await c.fetchval("SELECT id FROM products WHERE tenant_id = $1 AND nama_produk = $2", T, nama)
        konv = sorted((r["conversion_unit"], int(r["conversion_factor"])) for r in await c.fetch(
            "SELECT conversion_unit, conversion_factor FROM unit_conversions WHERE tenant_id = $1 AND product_id = $2 AND is_active", T, P)) if P else []
        harus("L0: barang Minyak uji dasar Botol, konversi datar Dus=12 / Karton=120", st == 200 and P and konv == [("Dus", 12), ("Karton", 120)],
              {"st": st, "b": b, "konv": konv})
        if not P:
            raise StopAsyncIteration

        async def stok(g):
            return D(str(await c.fetchval("SELECT COALESCE((SELECT quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 AND item_id = $3), 0)",
                                          T, g, P)))

        async def wac():
            return D(str(await c.fetchval("SELECT get_weighted_average_cost($1, $2)", T, P) or 0))

        async def kartu(src=None, jenis=None):
            q = "SELECT source_type, warehouse_id, quantity_in, quantity_out, unit_cost FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2"
            a = [T, P]
            if src:
                q += " AND source_id = $3"; a.append(src)
            return [(r["source_type"], r["warehouse_id"], D(str(r["quantity_in"])), D(str(r["quantity_out"])), D(str(r["unit_cost"])))
                    for r in await c.fetch(q + " ORDER BY created_at, id", *a)]

        async def jurnal(src):
            return [dict(r) for r in await c.fetch("""SELECT je.source_type, coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1 AND je.source_id::text = $2 AND je.status = 'POSTED' ORDER BY je.created_at, jl.line_number""", T, str(src))]

        def seimbang(j):
            return bool(j) and sum(D(str(r["debit"])) for r in j) == sum(D(str(r["credit"])) for r in j)

        def bulat(v):
            return v is not None and D(str(v)) % 1 == 0

        # ================= L1 PO -> GRN -> FPB
        print("== L1 PO 2 Karton @2.400.000 + PPN 11% -> GRN -> FPB", flush=True)
        st, b = await jalan("L1 PO", lambda: RPO.create_purchase_order(req(uid, "gr-po"), CPO(
            vendor_id=VENDOR, vendor_name=vendor["name"], po_date=hari, notes=PENANDA, tax_rate=11,
            items=[{"item_id": str(P), "description": nama, "quantity": 2, "unit": "Karton", "unit_price": 2400000}])))
        po = uu(data(b).get("id")) if st == 200 else None
        st2, b2 = await jalan("L1 kirim PO", lambda: RPO.send_purchase_order(req(uid, "gr-po-s"), po)) if po else (0, None)
        po_row = await c.fetchrow("SELECT total_amount, status FROM purchase_orders WHERE id = $1", po) if po else None
        poi = await c.fetchval("SELECT id FROM purchase_order_items WHERE po_id = $1", po) if po else None
        harus("L1a: PO 2 Karton + PPN 11% total 5.328.000, terkirim", st == st2 == 200 and po_row and D(str(po_row["total_amount"])) == D("5328000"),
              {"st": [st, st2], "po": dict(po_row) if po_row else None, "b2": b2})
        st, b = await jalan("L1 GRN", lambda: RPO.receive_goods(req(uid, "gr-grn"), po, RGR(
            receive_date=hari, warehouse_id=str(g_utama), items=[{"po_item_id": str(poi), "quantity_received": 2}]))) if poi else (0, None)
        gr = uu(data(b).get("goods_receipt_id")) if st == 200 else None
        k_gr = await kartu(gr) if gr else []
        harus("L1b: GRN 2 Karton -> kartu +240 Botol @22.200 (PPN dikapitalisasi), gudang utama, dokumen GRN tetap 2 Karton",
              gr and [(x[2], x[4]) for x in k_gr] == [(D("240"), D("22200"))] and k_gr[0][1] == g_utama
              and D(str(await c.fetchval("SELECT quantity FROM goods_receipt_items WHERE goods_receipt_id = $1", gr))) == D("2"),
              {"st": st, "b": b, "k": k_gr})
        j_gr = await jurnal(gr) if gr else []
        harus("L1c: jurnal GRN seimbang, Dr Persediaan 5.328.000", seimbang(j_gr) and sum(D(str(r["debit"])) for r in j_gr if r["account_code"] == "1-10600") == D("5328000"),
              {"j": j_gr})
        st, b = await jalan("L1 FPB dari GRN", lambda: RPO.convert_to_bill(req(uid, "gr-tb"), po, CTB(bill_date=hari)))
        bill = uu(data(b).get("bill_id")) if st == 200 else None
        st2, b2 = await jalan("L1 posting FPB", lambda: RB.post_bill(req(uid, "gr-pb"), bill, None, [])) if bill else (0, None)
        bl = await c.fetchrow("SELECT grand_total, tax_amount, subtotal, COALESCE(status_v2, status) st FROM bills WHERE id = $1", bill) if bill else None
        bi = await c.fetchrow("SELECT id, quantity, unit FROM bill_items WHERE bill_id = $1 ORDER BY line_number LIMIT 1", bill) if bill else None
        j_b = await jurnal(bill) if bill else []
        hutang = sum(D(str(r["credit"])) for r in j_b if r["account_code"].startswith("2-"))
        harus("L1d: FPB dari GRN 2 Karton (dasar diterima) terposting, total 5.328.000 rupiah penuh, Cr Hutang 5.328.000, jurnal seimbang, nol PPN Masukan",
              st == st2 == 200 and not (isinstance(b2, dict) and b2.get("success") is False) and bl and D(str(bl["grand_total"])) == D("5328000")
              and bulat(bl["grand_total"]) and bi and (D(str(bi["quantity"])), bi["unit"]) == (D("2"), "Karton") and hutang == D("5328000") and seimbang(j_b)
              and not any(str(r["memo"] or "").startswith("PPN Masukan") for r in j_b), {"st": [st, st2], "b2": str(b2)[:400], "bl": dict(bl) if bl else None, "j": j_b})
        harus("L1e: kartu tak bertambah dari FPB (GRN sudah), saldo 240 Botol, WAC 22.200",
              sum(x[2] - x[3] for x in await kartu()) == D("240") and await stok(g_utama) == D("240") and await wac() == D("22200"),
              {"k": await kartu(), "wac": await wac()})

        # ================= L2 Daftar Harga -> SO -> SJ -> SI
        print("== L2 Daftar Harga -> SO 1 Dus + 5 Botol -> SJ -> SI", flush=True)
        cust = await c.fetchrow("SELECT id, nama FROM customers WHERE tenant_id = $1 AND price_list_id IS NULL AND COALESCE(is_active, true) ORDER BY nama LIMIT 1", T)
        st, b = await jalan("L2 daftar harga", lambda: RPL.create_price_list(req(uid), CPL(
            code=f"GROSIR-{uuid.uuid4().hex[:4]}", name=f"{PENANDA} grosir", items=[
                {"item_id": str(P), "unit": "Botol", "price": 25000}, {"item_id": str(P), "unit": "Dus", "price": 280000}])))
        pl = uu(data(b).get("id")) if st in (200, 201) else None
        if pl and cust:
            await c.execute("UPDATE customers SET price_list_id = $1 WHERE id = $2 AND tenant_id = $3", pl, cust["id"], T)  # tautan pelanggan (dalam rollback)
        st2, b2 = await jalan("L2 resolve", lambda: RPL.resolve_harga(req(uid), RHR(customer_id=cust["id"], lines=[
            {"key": "dus", "item_id": P, "unit": "Dus", "quantity": 1}, {"key": "botol", "item_id": P, "unit": "Botol", "quantity": 5}]), "sales_order"))
        hr = {x["key"]: x for x in data(b2).get("lines", [])}
        harus("L2a: resolve daftar pelanggan: Dus 280.000 / Botol 25.000 sumber daftar, faktor Dus 12",
              pl and st2 == 200 and (hr.get("dus", {}).get("harga"), hr.get("botol", {}).get("harga")) == (280000.0, 25000.0)
              and hr["dus"].get("sumber") == hr["botol"].get("sumber") == "daftar" and hr["dus"].get("faktor") == 12.0, {"st": [st, st2], "b": b, "b2": b2})
        h_dus = D(str(hr.get("dus", {}).get("harga") or 280000))
        h_btl = D(str(hr.get("botol", {}).get("harga") or 25000))
        st, b = await jalan("L2 SO", lambda: RSO.create_sales_order(req(uid, "gr-so"), CSO(
            order_date=hari, customer_id=str(cust["id"]), customer_name=cust["nama"], notes=PENANDA, items=[
                {"item_id": str(P), "description": nama, "quantity": 1, "unit": "Dus", "unit_price": h_dus, "warehouse_id": str(g_utama)},
                {"item_id": str(P), "description": nama, "quantity": 5, "unit": "Botol", "unit_price": h_btl, "warehouse_id": str(g_utama)}]), Response()))
        so = uu(data(b).get("id")) if st in (200, 201) else None
        st2, b2 = await jalan("L2 konfirmasi SO", lambda: RSO.confirm_sales_order(req(uid, "gr-so-k"), str(so), Response())) if so else (0, None)
        so_row = await c.fetchrow("SELECT total_amount, status FROM sales_orders WHERE id = $1", so) if so else None
        harus("L2b: SO 1 Dus + 5 Botol dibuat + dikonfirmasi, total 405.000", st in (200, 201) and st2 == 200 and so_row
              and D(str(so_row["total_amount"])) == D("405000"), {"st": [st, st2], "b": str(b)[:300], "b2": str(b2)[:300], "so": dict(so_row) if so_row else None})
        st, b = await jalan("L2 SJ", lambda: RSJ.create_sj_so(req(uid, "gr-sj"), Response(), str(so), SSJ.SJSOIn(warehouse_id=g_utama))) if so else (0, None)
        sj = uu(data(b).get("id")) if st == 200 else None
        k_sj = await kartu(sj) if sj else []
        j_sj = await jurnal(sj) if sj else []
        harus("L2c: SJ keluar 17 Botol (12 + 5) x WAC 22.200 = 377.400, jurnal seimbang, stok utama 223",
              sj and sum(x[3] for x in k_sj) == D("17") and sum(x[3] * x[4] for x in k_sj) == D("377400") and seimbang(j_sj)
              and sum(D(str(r["debit"])) for r in j_sj) == D("377400") and await stok(g_utama) == D("223"), {"st": st, "b": str(b)[:400], "k": k_sj, "j": j_sj})
        st, b = await jalan("L2 SI terbit", lambda: RSO.convert_to_invoice(req(uid, "gr-si"), Response(), str(so), CTI(
            invoice_date=hari, post=True, apply_deposits=False)))
        inv = uu(data(b).get("invoice_id")) if st == 200 else None
        si = await c.fetchrow("SELECT total_amount, status, subtotal, tax_amount FROM sales_invoices WHERE id = $1", inv) if inv else None
        j_si = await jurnal(inv) if inv else []
        hpp = sum(D(str(r["debit"])) for r in j_si if r["account_code"].startswith("5-"))
        harus("L2d: SI terbit total 405.000 rupiah penuh, jurnal seimbang, HPP faktur = 377.400 (reklas SJ), tanpa keluar stok kedua",
              inv and si and D(str(si["total_amount"])) == D("405000") and bulat(si["total_amount"]) and seimbang(j_si) and hpp == D("377400")
              and await stok(g_utama) == D("223"), {"st": st, "b": str(b)[:400], "si": dict(si) if si else None, "j": j_si})

        # ================= L3 pembayaran
        print("== L3 Terima pembayaran", flush=True)
        kas = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id = $1 AND COALESCE(is_active, true) AND coa_id IS NOT NULL ORDER BY account_name LIMIT 1", T)
        sisa = lambda: c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = $2", T, inv)
        n_rp = lambda: c.fetchval("SELECT count(*) FROM receive_payments WHERE tenant_id = $1", T)
        r0 = await n_rp()

        def bayar(nominal, k):
            return RRP.buat_penerimaan(c, {"tenant_id": T, "user_id": uid}, CRP(
                customer_id=str(cust["id"]), payment_date=hari, bank_account_id=str(kas), total_amount=nominal, notes=PENANDA,
                allocations=[{"invoice_id": str(inv), "amount_applied": nominal}]))
        st, b = await jalan("L3 bayar bersen", lambda: bayar(D("200000.50"), "gr-rp0")) if inv else (0, None)
        harus("L3a: nominal bersen 200.000,50 -> ditolak 'Nominal harus rupiah penuh.' nol tulis", st in (400, 422) and "Nominal harus rupiah penuh." in str(pesan(b))
              and await n_rp() == r0, {"st": st, "b": b})
        st, b = await jalan("L3 bayar penuh", lambda: bayar(D("405000"), "gr-rp1")) if inv else (0, None)
        harus("L3b: bayar rupiah penuh 405.000 -> sisa faktur 0", st == 200 and inv and D(str(await sisa() or 0)) == 0, {"st": st, "b": str(b)[:400]})

        # ================= L4 pindah gudang
        print("== L4 Pindah gudang 1 Dus", flush=True)
        a0, t0, w0 = await stok(g_utama), await stok(g_lain), await wac()
        st, b = await jalan("L4 pindah", lambda: RST.create_and_complete_stock_transfer(req(uid, "gr-pg"), Response(), CST(
            from_warehouse_id=g_utama, to_warehouse_id=g_lain, notes=PENANDA, items=[{"item_id": P, "quantity_requested": 1, "unit": "Dus"}])))
        harus("L4: pindah 1 Dus -> asal -12, tujuan +12 Botol, WAC tetap 22.200", st == 200 and await stok(g_utama) == a0 - 12 and await stok(g_lain) == t0 + 12
              and await wac() == w0 == D("22200"), {"st": st, "b": str(b)[:400], "stok": [a0, await stok(g_utama), t0, await stok(g_lain)], "wac": [w0, await wac()]})

        # ================= L5 ND retur 1 Dus
        print("== L5 Retur ND 1 Dus atas FPB", flush=True)
        a1 = await stok(g_utama)
        st, b = await jalan("L5 ND buat", lambda: RVC.create_vendor_credit(req(uid, "gr-nd"), CVR(
            vendor_id=VENDOR, vendor_name=vendor["name"], vendor_credit_date=hari, original_bill_id=str(bill), reason="return", tax_rate=11, notes=PENANDA,
            items=[{"item_id": str(P), "description": nama, "quantity": 1, "unit": "Dus", "unit_price": 240000, "original_bill_item_id": str(bi["id"])}]))) if bi else (0, None)
        vc = uu(data(b).get("id")) if st in (200, 201) else None
        st2, b2 = await jalan("L5 ND posting", lambda: RVC.post_vendor_credit(req(uid, "gr-nd-p"), vc)) if vc else (0, None)
        v = await c.fetchrow("SELECT total_amount, tax_amount, pembulatan, status FROM vendor_credits WHERE id = $1", vc) if vc else None
        j_vc = await jurnal(vc) if vc else []
        k_vc = await kartu(vc) if vc else []
        dr_h = sum(D(str(r["debit"])) for r in j_vc if r["account_code"].startswith("2-"))
        harus("L5a: ND 1 Dus @240.000 + PPN 11% terposting, total 266.400 rupiah penuh (pembulatan 0), Dr Hutang 266.400, Cr Retur = total, seimbang, nol PPN Retur",
              st in (200, 201) and st2 == 200 and v and D(str(v["total_amount"])) == D("266400") and bulat(v["total_amount"]) and dr_h == D("266400") and seimbang(j_vc)
              and not any(str(r["memo"] or "").startswith("PPN Retur") for r in j_vc), {"st": [st, st2], "b": str(b)[:400], "b2": str(b2)[:400],
                                                                                      "v": dict(v) if v else None, "j": j_vc})
        harus("L5b: kartu ND -12 Botol x WAC 22.200 = 266.400 dari gudang utama", sum(x[3] for x in k_vc) == D("12") and sum(x[3] * x[4] for x in k_vc) == D("266400")
              and await stok(g_utama) == a1 - 12, {"k": k_vc, "stok": [a1, await stok(g_utama)]})
        sisa_b = await c.fetchval("SELECT outstanding FROM compute_ap_outstanding($1) WHERE bill_id = $2", T, bill) if bill else None
        harus("L5c: sisa hutang FPB = 5.328.000 - 266.400 = 5.061.600", sisa_b is not None and D(str(sisa_b)) == D("5061600"), {"sisa": sisa_b})
        harus("L5d: total saldo barang = 240 - 17 - 12 = 211 Botol (utama 199 + tujuan 12), WAC 22.200",
              await stok(g_utama) == D("199") and await stok(g_lain) == D("12") and sum(x[2] - x[3] for x in await kartu()) == D("211") and await wac() == D("22200"),
              {"utama": await stok(g_utama), "lain": await stok(g_lain), "wac": await wac()})
    except StopAsyncIteration:
        pass
    finally:
        await luar.rollback()
    harus("L6a: ROLLBACK net 0 (barang, konversi, daftar harga, PO, GRN, FPB, SO, SJ, SI, bayar, pindah, ND, jurnal, kartu, stok, audit)",
          awal == await hitung() and sp0 == await sidik_pelanggan_kaos(), {"awal": awal, "akhir": await hitung()})
    harus("L6b: grapgrap-manado read-only: sidik md5 kartu + jurnal + stok + pelanggan SAMA", s0 == await sidik())
    harus("L6c: kanal keluar 0", KELUAR == [], KELUAR)
    lulus = sum(x["ok"] for x in cek)
    print(f"SELESAI {lulus}/{len(cek)} lulus{' (SABOTASE ' + SABOT + ')' if SABOT else ''}", flush=True)
    json.dump(hasil, open(keluar, "w"), default=str)
    return 0 if lulus == len(cek) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
