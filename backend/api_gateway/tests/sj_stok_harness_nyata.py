import asyncio, os, re, sys
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException, Response
from starlette.requests import Request
from backend.api_gateway.app.services import sj_so as S
from backend.api_gateway.app.routers import sj_so as R
from backend.api_gateway.app.routers import sales_invoices as SI
from backend.api_gateway.app.routers import credit_notes as CN

OK, GAGAL = [], []
D = Decimal

def cek(nama, cond, info=""):
    (OK if cond else GAGAL).append(nama)
    print(("  OK   " if cond else "  GAGAL ") + nama + (f"  [{info}]" if info else ""))

async def snap(conn):
    return dict(await conn.fetchrow("""SELECT (SELECT count(*) FROM journal_entries) je, (SELECT count(*) FROM journal_lines) jl,
        (SELECT count(*) FROM inventory_ledger) il, (SELECT count(*) FROM warehouse_stock) ws,
        (SELECT count(*) FROM invoice_fulfillments) f, (SELECT count(*) FROM sj_faktur_alokasi) a,
        (SELECT count(*) FROM audit_logs) au, (SELECT count(*) FROM idempotency_keys) ik,
        (SELECT coalesce(sum(total_debit),0) FROM journal_entries) debit"""))

# SQL Check 23 -- DISALIN dari monitoring/accounting_health_check.sh (dicocokkan oleh tes unit/uji): saldo jurnal vs sub-ledger SJ
async def check23(conn, tid):
    akun = await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='GOODS_DELIVERED_NOT_INVOICED'", tid)
    saldo = D(str(await conn.fetchval("""SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND jl.account_id = $2""", tid, akun)))
    turunan = D(str(await conn.fetchval("""SELECT COALESCE(SUM(ifi.total_cost - COALESCE(a.c, 0)), 0) FROM invoice_fulfillments f
        JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
        LEFT JOIN LATERAL (SELECT SUM(x.total_cost) AS c FROM sj_faktur_alokasi x WHERE x.fulfillment_item_id = ifi.id AND x.tenant_id = f.tenant_id AND x.voided_at IS NULL) a ON true
        WHERE f.tenant_id = $1 AND f.asal = 'so' AND f.status <> 'voided' AND f.voided_at IS NULL AND f.journal_id IS NOT NULL""", tid)))
    return saldo, turunan, akun

async def drift(conn, tid):
    a = await conn.fetchval("SELECT COALESCE(drift::text,'') FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = $1", tid)
    b = await conn.fetchval("SELECT COALESCE(drift::text,'') FROM verify_deferred_revenue_reconciliation_all() WHERE tenant_id = $1", tid)
    return a, b

async def main():
    conn = await asyncpg.connect(os.environ["URL"])
    awal = await snap(conn)
    tr = conn.transaction(); await tr.start()
    try:
        c = await conn.fetchrow("""
            SELECT si.id inv_id, si.invoice_number, si.invoice_date, si.total_amount, si.sales_order_id so_id, si.created_by uid,
                   sii.id sii_id, sii.sales_order_item_id soi_id, sii.item_id, sii.quantity, p.nama_produk
            FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id JOIN products p ON p.id = sii.item_id
            WHERE si.status = 'draft' AND si.tenant_id = 'kaos-biru-konveksi' AND p.track_inventory AND sii.sales_order_item_id IS NOT NULL
            ORDER BY si.created_at DESC LIMIT 1""")
        cek("kandidat faktur DRAF ber-stok bertautan SO ada (harness tak hampa)", c is not None)
        TID = "kaos-biru-konveksi"; uid = str(c["uid"]) if c["uid"] else None
        ctx = {"tenant_id": TID, "user_id": uid}
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TID)
        for f in ("pengiriman_sebelum_faktur", "pengiriman_stok_sebelum_faktur"):
            await conn.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1,$2,true) ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled=true", TID, f)
        gud = await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id=$1 AND is_default ORDER BY created_at LIMIT 1", TID)
        # pastikan stok & WAC (di dalam tx): kartu saldo awal bila kurang
        ada = D(str(await conn.fetchval("SELECT COALESCE((SELECT quantity FROM warehouse_stock WHERE tenant_id=$1 AND warehouse_id=$2 AND item_id=$3),0)", TID, gud, c["item_id"])))
        wac = D(str(await conn.fetchval("SELECT get_weighted_average_cost($1,$2)", TID, c["item_id"]) or 0))
        if ada < 20 or wac <= 0:
            await conn.execute("""INSERT INTO inventory_ledger (tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,
                source_id,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
                VALUES ($1,$2,'UJI',$3,'ADJUSTMENT',CURRENT_DATE,'STOCK_ADJUSTMENT',gen_random_uuid(),100,0,100,40000,4000000,40000,$4)""",
                TID, c["item_id"], c["nama_produk"], gud)
            wac = D("40000")
        wac = D(str(await conn.fetchval("SELECT get_weighted_average_cost($1,$2)", TID, c["item_id"])))
        print(f"   barang={c['nama_produk']} WAC={wac} stok_awal={ada} faktur={c['invoice_number']} qty_faktur={c['quantity']}")
        d0 = await drift(conn, TID)
        async def _gp():
            class CM:
                async def __aenter__(s): return conn
                async def __aexit__(s, *a): return False
            class P:
                def acquire(s): return CM()
            return P()
        R._pool = _gp
        def req(kunci=None):
            hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
            r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""})
            r.state.user = {"tenant_id": TID, "user_id": uid}
            return r
        Q = D("5")
        body = S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=Q)], warehouse_id=gud)
        s0 = await snap(conn)
        stok_sebelum = D(str(await conn.fetchval("SELECT COALESCE((SELECT quantity FROM warehouse_stock WHERE tenant_id=$1 AND warehouse_id=$2 AND item_id=$3),0)", TID, gud, c["item_id"])))
        print("== 1. SJ-so ber-stok: pratinjau + tulis")
        pv = (await R.preview_sj_so(req(), str(c["so_id"]), body))["data"]
        cek("pratinjau ok + angka server", pv["ok"] and pv["lines"][0]["unit_cost"] == float(wac) and pv["total_value"] == float(Q * wac), str(pv["blocks"]))
        cek("pratinjau nol tulis", (await snap(conn)) == s0)
        r1 = await R.create_sj_so(req("stok-uji-1"), Response(), str(c["so_id"]), body)
        fid = UUID(r1["data"]["id"]); jid = UUID(r1["data"]["journal_id"])
        nilai = (Q * wac).quantize(D("0.01"))
        lines = await conn.fetch("SELECT a.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts a ON a.id=jl.account_id WHERE jl.journal_id=$1 ORDER BY jl.line_number", jid)
        cek("jurnal Dr 1-10660 Barang Terkirim / Cr Persediaan = qty x WAC", [(l["account_code"], D(str(l["debit"])), D(str(l["credit"]))) for l in lines][0] == ("1-10660", nilai, D("0"))
            and D(str(lines[1]["credit"])) == nilai, str([(l["account_code"], l["debit"], l["credit"]) for l in lines]))
        je = await conn.fetchrow("SELECT status, source_type, total_debit, total_credit FROM journal_entries WHERE id=$1", jid)
        cek("jurnal POSTED, seimbang, source DELIVERY_NOTE", je["status"] == "POSTED" and je["source_type"] == "DELIVERY_NOTE" and je["total_debit"] == je["total_credit"])
        led = await conn.fetchrow("SELECT source_type, source_id, journal_id, quantity_out, unit_cost, total_cost, average_cost FROM inventory_ledger WHERE source_id=$1", fid)
        cek("kartu keluar tertaut jurnal, nilai snapshot", led["journal_id"] == jid and D(str(led["quantity_out"])) == Q and D(str(led["total_cost"])) == nilai and led["source_type"] == "DELIVERY_NOTE")
        sa, tu, akun = await check23(conn, TID)
        cek("CHECK 23: saldo Barang Terkirim == Σ SJ belum difakturkan", sa == tu == nilai, f"{sa} vs {tu}")
        stok_sesudah = D(str(await conn.fetchval("SELECT quantity FROM warehouse_stock WHERE tenant_id=$1 AND warehouse_id=$2 AND item_id=$3", TID, gud, c["item_id"])))
        cek("stok gudang turun tepat Q (lewat trigger kartu)", stok_sesudah == stok_sebelum - Q, f"{stok_sebelum} -> {stok_sesudah}")
        d1 = await drift(conn, TID)
        cek("Check 15 & 16 (drift WAC GL dan Dimuka) TIDAK bergeser oleh SJ-so", d1 == d0, f"{d0} -> {d1}")
        pend = await R.pending_invoice(req())
        dd = pend["data"]
        cek("laporan terkirim-belum-difakturkan: total = saldo buku, selisih 0", abs(dd["total_pending_value"] - float(nilai)) < 0.01 and dd["selisih"] is not None and abs(dd["selisih"]) < 0.01, str({k: dd[k] for k in ("total_pending_value","ledger_balance","selisih")}))
        print("== 2. faktur diposting -> alokasi + reklas HPP + pendapatan")
        sj0 = await snap(conn)
        post = await SI.terbitkan_faktur(conn, ctx, c["inv_id"], c["invoice_number"], c["total_amount"], c["invoice_date"], apply_deposits=False)
        al = await conn.fetchrow("SELECT quantity, total_cost, cogs_journal_id, revenue_journal_id FROM sj_faktur_alokasi WHERE invoice_item_id=$1 AND voided_at IS NULL", c["sii_id"])
        cek("alokasi ber-nilai snapshot SJ", al is not None and D(str(al["quantity"])) == Q and D(str(al["total_cost"])) == nilai)
        rk = await conn.fetch("SELECT a.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts a ON a.id=jl.account_id WHERE jl.journal_id=$1 ORDER BY jl.line_number", al["cogs_journal_id"])
        cek("reklas Dr HPP 5-10100 / Cr Barang Terkirim 1-10660 pada snapshot", rk[0]["account_code"] == "5-10100" and D(str(rk[0]["debit"])) == nilai and rk[1]["account_code"] == "1-10660" and D(str(rk[1]["credit"])) == nilai)
        sa2, tu2, _ = await check23(conn, TID)
        cek("CHECK 23 sesudah faktur: Barang Terkirim nol == nol", sa2 == tu2 == D("0"), f"{sa2} vs {tu2}")
        cek("tak ada keluar stok kedua (ledger SALE INVOICE_FULFILLMENT untuk faktur ini = 0 baris)", await conn.fetchval(
            "SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1 AND source_id=$2 AND source_type='INVOICE_FULFILLMENT'", TID, c["inv_id"]) == 0)
        it = await conn.fetchrow("SELECT fulfilled_qty, recognized_amount, allocated_amount, quantity FROM sales_invoice_items WHERE id=$1", c["sii_id"])
        cek("fulfilled_qty = Q; recognized proporsional", D(str(it["fulfilled_qty"])) == Q and D(str(it["recognized_amount"])) > 0
            and D(str(it["recognized_amount"])) == (D(str(it["allocated_amount"])) * Q / D(str(it["quantity"]))).quantize(D("0.01")),
            f"rec={it['recognized_amount']} alloc={it['allocated_amount']}")
        d2 = await drift(conn, TID)
        cek("Check 16 (Dimuka vs Σ alokasi-diakui) tak bergeser oleh alokasi", d2[1] == d0[1], f"{d0[1]} -> {d2[1]}")
        print("== 3. retur lewat alokasi (T2)")
        hasil = await CN._keluar_faktur_untuk_retur(conn, TID, c["inv_id"], c["item_id"], None)
        cek("NK retur menemukan barang keluar lewat alokasi (sisa, biaya snapshot, gudang SJ)", hasil is not None and D(str(hasil[0])) == Q and
            abs(D(str(hasil[1])) - wac) < D("0.01") and hasil[2] == gud, str(hasil))
        print("== 4. void faktur -> reklas dibalik, SJ kembali belum ditagih")
        rv = await SI._rencana_void_faktur(conn, ctx, c["inv_id"])
        cek("rencana void tanpa blok", not rv.get("blocks"), str(rv.get("blocks")))
        await SI._tulis_void_faktur(conn, ctx, c["inv_id"], rv, SimpleNamespace(reason="uji harness irisan 2"))
        sa3, tu3, _ = await check23(conn, TID)
        cek("CHECK 23 sesudah void faktur: saldo kembali = nilai SJ (SJ belum ditagih lagi)", sa3 == tu3 == nilai, f"{sa3} vs {tu3}")
        cek("alokasi dilepas, SJ-so tetap posted", await conn.fetchval("SELECT count(*) FROM sj_faktur_alokasi WHERE invoice_item_id=$1 AND voided_at IS NULL", c["sii_id"]) == 0
            and await conn.fetchval("SELECT status FROM invoice_fulfillments WHERE id=$1", fid) == "posted")
        cek("tak ada INVOICE_REVENUE faktur yang belum dibalik", await conn.fetchval(
            "SELECT count(*) FROM journal_entries WHERE tenant_id=$1 AND source_id=$2 AND source_type='INVOICE_REVENUE' AND status='POSTED' AND reversed_by_id IS NULL", TID, c["inv_id"]) == 0)
        d3 = await drift(conn, TID)
        cek("Check 15 & 16 bersih sesudah void faktur", d3 == d0, f"{d0} -> {d3}")
        print("== 5. void SJ mandiri")
        await R.void_sj(req(), str(fid), R.AlasanIn(reason="uji"))
        sa4, tu4, _ = await check23(conn, TID)
        cek("CHECK 23 sesudah void SJ: nol == nol", sa4 == tu4 == D("0"))
        cek("kartu pembalik DELIVERY_NOTE_VOID + stok kembali", await conn.fetchval(
            "SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1 AND source_id=$2 AND source_type='DELIVERY_NOTE_VOID'", TID, fid) == 1)
        d4 = await drift(conn, TID)
        cek("Check 15 & 16 bersih sesudah void SJ", d4 == d0, f"{d0} -> {d4}")
        print("== 6. KONTROL MERAH tiap source_type baru")
        # (a) jurnal Barang Terkirim tanpa SJ -> Check 23 MERAH
        akun_inv = await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='INVENTORY_MERCHANDISE'", TID)
        j = await S._jurnal(conn, TID, uid, prefix="UJI", tanggal=c["invoice_date"], source_type="DELIVERY_NOTE", source_id=fid,
                            deskripsi="UJI MERAH", baris=[(akun, D("1000"), D("0"), "m"), (akun_inv, D("0"), D("1000"), "m")])
        sa5, tu5, _ = await check23(conn, TID)
        cek("MERAH: jurnal DELIVERY_NOTE tanpa SJ -> Check 23 selisih = 1000", sa5 - tu5 == D("1000"), f"{sa5} vs {tu5}")
        akun_hpp = await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='COGS_SALES'", TID)
        await S._jurnal(conn, TID, uid, prefix="UJI", tanggal=c["invoice_date"], source_type="DELIVERY_INVOICED", source_id=c["inv_id"],
                        deskripsi="UJI MERAH reklas", baris=[(akun_hpp, D("500"), D("0"), "m"), (akun, D("0"), D("500"), "m")])
        sa6, tu6, _ = await check23(conn, TID)
        cek("MERAH: jurnal DELIVERY_INVOICED tanpa alokasi -> Check 23 menyimpang lagi (-500 dari kontrol sebelumnya)", sa6 - tu6 == D("500"), f"{sa6} vs {tu6}")
        # (b) kartu DELIVERY_NOTE tanpa jurnal -> Check 21 MERAH (daftar wajib-jurnal dibaca dari monitoring)
        mon = open(os.environ.get("MON", "/repo/monitoring/accounting_health_check.sh")).read()
        wajib = re.search(r'^STOK_WAJIB_JURNAL="(.*)"$', mon, re.M).group(1)
        cek("DELIVERY_NOTE ada di STOK_WAJIB_JURNAL (Check 21)", "'DELIVERY_NOTE'" in wajib, wajib)
        await conn.execute("""INSERT INTO inventory_ledger (tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,
            quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id) VALUES ($1,$2,'UJI','UJI','SALE',CURRENT_DATE,'DELIVERY_NOTE',gen_random_uuid(),0,1,0,1,1,1,$3)""", TID, c["item_id"], gud)
        n21 = await conn.fetchval(f"""SELECT count(*) FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.source_type IN ({wajib})
            AND NOT EXISTS (SELECT 1 FROM journal_entries je WHERE je.tenant_id = il.tenant_id AND je.status = 'POSTED' AND je.id::text IN (il.journal_id::text, il.source_id::text))
            AND il.source_id NOT IN (SELECT id FROM invoice_fulfillments WHERE tenant_id=$1 AND journal_id IS NOT NULL)""", TID)
        cek("MERAH: kartu DELIVERY_NOTE tanpa jurnal -> Check 21 >= 1", n21 >= 1, str(n21))
        # (c) kartu keluar tanpa jurnal menggeser Check 15 (GL vs on_hand x WAC)
        d6 = await drift(conn, TID)
        cek("MERAH: kartu tanpa jurnal menggeser drift WAC (Check 15)", d6[0] != d0[0], f"{d0[0]} -> {d6[0]}")
        # (d) is_effective_journal: pembalik baru tak dihitung
        cek("is_effective_journal mengenal pembalik DELIVERY_*", await conn.fetchval(
            "SELECT pg_get_functiondef('is_effective_journal(uuid)'::regprocedure) LIKE '%DELIVERY_NOTE_REVERSAL%' AND pg_get_functiondef('is_effective_journal(uuid)'::regprocedure) LIKE '%DELIVERY_INVOICED_REVERSAL%'"))
    finally:
        await tr.rollback()
    akhir = await snap(conn)
    cek("NOL JEJAK: hitungan kembali ke awal (ROLLBACK)", akhir == awal, f"{awal} vs {akhir}")
    print(f"\nRINGKAS: {len(OK)} OK, {len(GAGAL)} GAGAL", GAGAL)
    sys.exit(1 if GAGAL else 0)

asyncio.run(main())
