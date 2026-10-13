"""UJI NYATA W-20261013-SJSO-SUDAHFAKTUR (Law 1/16, 13, 24, 25) -- kaos, scratch DB dari backup terbaru, SATU tx luar ROLLBACK, per skenario
savepoint. Handler ASLI (sj_so router + terbitkan_faktur + fulfill_invoice). MODE master (kontrol merah) memakai kode lama via overlay file.
S1: faktur POSTED -> SJ-so DITOLAK -> /fulfill normal: stok keluar 1x, HPP 1x, Check 23 selisih 0.
S2: SJ-so dulu -> faktur -> alokasi benar -> /fulfill sisa: total keluar = qty faktur tepat, HPP total = qty x WAC."""
import asyncio, json, os, sys
from decimal import Decimal as D
from uuid import UUID
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException, Response
from starlette.requests import Request
from backend.api_gateway.app.services import sj_so as S
from backend.api_gateway.app.routers import sj_so as R
from backend.api_gateway.app.routers import sales_invoices as SI

TID = "kaos-biru-konveksi"
OK, GAGAL = [], []


def cek(nama, cond, info=""):
    (OK if cond else GAGAL).append(nama)
    print(("  OK   " if cond else "  GAGAL ") + nama + (f"  [{info}]" if info else ""), flush=True)


class Batal(Exception):
    pass


async def snap(c):
    return dict(await c.fetchrow("""SELECT (SELECT count(*) FROM journal_entries) je, (SELECT count(*) FROM journal_lines) jl,
        (SELECT count(*) FROM inventory_ledger) il, (SELECT count(*) FROM invoice_fulfillments) f,
        (SELECT count(*) FROM sj_faktur_alokasi) a, (SELECT count(*) FROM idempotency_keys) ik"""))


async def check23(c):
    akun = await c.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='GOODS_DELIVERED_NOT_INVOICED'", TID)
    saldo = D(str(await c.fetchval("""SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND jl.account_id = $2""", TID, akun)))
    turunan = D(str(await c.fetchval("""SELECT COALESCE(SUM(ifi.total_cost - COALESCE(a.c, 0)), 0) FROM invoice_fulfillments f
        JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
        LEFT JOIN LATERAL (SELECT SUM(x.total_cost) AS c FROM sj_faktur_alokasi x WHERE x.fulfillment_item_id = ifi.id AND x.tenant_id = f.tenant_id AND x.voided_at IS NULL) a ON true
        WHERE f.tenant_id = $1 AND f.asal = 'so' AND f.status <> 'voided' AND f.voided_at IS NULL AND f.journal_id IS NOT NULL""", TID)))
    return saldo, turunan


async def drift(c):
    a = await c.fetchval("SELECT COALESCE(drift::text,'') FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = $1", TID)
    b = await c.fetchval("SELECT COALESCE(drift::text,'') FROM verify_deferred_revenue_reconciliation_all() WHERE tenant_id = $1", TID)
    return a, b


async def main():
    conn = await asyncpg.connect(os.environ["URL"])
    awal = await snap(conn)
    tr = conn.transaction(); await tr.start()
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TID)
        for f in ("pengiriman_sebelum_faktur", "pengiriman_stok_sebelum_faktur"):
            await conn.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1,$2,true) ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled=true", TID, f)
        c = await conn.fetchrow("""
            SELECT si.id inv_id, si.invoice_number, si.invoice_date, si.total_amount, si.sales_order_id so_id, si.created_by uid,
                   sii.id sii_id, sii.sales_order_item_id soi_id, sii.item_id, sii.quantity, p.nama_produk
            FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id JOIN products p ON p.id = sii.item_id
            WHERE si.status = 'draft' AND si.tenant_id = $1 AND p.track_inventory AND sii.sales_order_item_id IS NOT NULL
              AND (SELECT count(*) FROM sales_invoice_items x WHERE x.invoice_id = si.id) = 1
            ORDER BY si.created_at DESC LIMIT 1""", TID)
        cek("kandidat faktur DRAF ber-stok 1 baris bertautan SO ada (harness tak hampa)", c is not None)
        uid = str(c["uid"]) if c["uid"] else None; ctx = {"tenant_id": TID, "user_id": uid}
        gud = await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id=$1 AND is_default ORDER BY created_at LIMIT 1", TID)
        await conn.execute("""INSERT INTO inventory_ledger (tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,
            source_id,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
            VALUES ($1,$2,'UJI',$3,'ADJUSTMENT',CURRENT_DATE,'STOCK_ADJUSTMENT',gen_random_uuid(),500,0,500,40000,20000000,40000,$4)""", TID, c["item_id"], c["nama_produk"], gud)
        wac = D(str(await conn.fetchval("SELECT get_weighted_average_cost($1,$2)", TID, c["item_id"])))
        qty = D(str(c["quantity"]))
        akun_hpp = await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='COGS_SALES'", TID)
        print(f"   barang={c['nama_produk']} WAC={wac} qty_faktur={qty} faktur={c['invoice_number']}")

        async def _gp():
            class CM:
                async def __aenter__(s): return conn
                async def __aexit__(s, *a): return False
            class P:
                def acquire(s): return CM()
            return P()
        R._pool = _gp; SI.get_pool = _gp

        def req(k=None, body=None):
            hdr = [(b"x-idempotency-key", k.encode())] if k else []
            raw = json.dumps(body).encode() if body is not None else b""
            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}
            r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
            r.state.user = {"tenant_id": TID, "user_id": uid}
            return r

        async def keluar():
            return D(str(await conn.fetchval(
                "SELECT COALESCE(SUM(quantity_out),0) FROM inventory_ledger WHERE tenant_id=$1 AND product_id=$2 AND movement_type='SALE'", TID, c["item_id"])))

        async def hpp_faktur():
            return D(str(await conn.fetchval("""SELECT COALESCE(SUM(jl.debit - jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                WHERE je.tenant_id=$1 AND je.status='POSTED' AND is_effective_journal(je.id) AND jl.account_id=$2
                  AND (je.source_id = $3 OR je.source_id IN (SELECT id FROM invoice_fulfillments WHERE tenant_id=$1 AND invoice_id=$3))""",
                TID, akun_hpp, c["inv_id"])))

        d0 = await drift(conn)
        k0 = await keluar()

        async def fulfill(q):
            body = {"warehouse_id": str(gud), "items": [{"invoice_item_id": str(c["sii_id"]), "quantity": float(q)}]}
            return await SI.fulfill_invoice(req(body=body), c["inv_id"])

        # ---------------- S1
        print("== S1. faktur POSTED -> SJ-so ditolak -> /fulfill normal")
        try:
            async with conn.transaction():
                await SI.terbitkan_faktur(conn, ctx, c["inv_id"], c["invoice_number"], c["total_amount"], c["invoice_date"], apply_deposits=False)
                body = S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=D("5"))], warehouse_id=gud)
                pv = (await R.preview_sj_so(req(), str(c["so_id"]), body))["data"]
                blok = [b for b in pv["blocks"] if b["code"] == "SJ_SO_BARIS_SUDAH_DIFAKTUR"]
                cek("S1a pratinjau items: ok=False + blok SJ_SO_BARIS_SUDAH_DIFAKTUR bermedan baris", pv["ok"] is False and len(blok) == 1
                    and blok[0]["sales_order_item_id"] == str(c["soi_id"]), str(pv["blocks"])[:200])
                cek("S1b blok membawa invoice_id + invoice_number", bool(blok) and blok[0].get("faktur") and
                    blok[0]["faktur"][0]["invoice_id"] == str(c["inv_id"]) and blok[0]["faktur"][0]["invoice_number"] == c["invoice_number"]
                    and blok[0]["invoice_number"] == c["invoice_number"])
                pv0 = (await R.preview_sj_so(req(), str(c["so_id"]), S.SJSOIn()))["data"]
                print("CONTOH_JSON_DILEWATI", json.dumps([d for d in pv0["dilewati"] if d["sales_order_item_id"] == str(c["soi_id"])], default=str))
                print("CONTOH_JSON_BLOK", json.dumps(blok, default=str))
                dl = [d for d in pv0["dilewati"] if d["sales_order_item_id"] == str(c["soi_id"])]
                cek("S1c mode tanpa items: baris itu di dilewati (alasan+faktur), bukan di lines", len(dl) == 1 and dl[0]["alasan"] == "SJ_SO_BARIS_SUDAH_DIFAKTUR"
                    and dl[0]["faktur"][0]["invoice_number"] == c["invoice_number"] and dl[0]["invoice_id"] == str(c["inv_id"])
                    and all(l["sales_order_item_id"] != str(c["soi_id"]) for l in pv0["lines"]), str(pv0["dilewati"])[:200])
                s0 = await snap(conn)
                try:
                    await R.create_sj_so(req("sjdf-1"), Response(), str(c["so_id"]), body)
                    cek("S1d create SJ-so ditolak 409", False, "LOLOS (cacat)")
                except HTTPException as e:
                    d = e.detail if isinstance(e.detail, dict) else {}
                    cek("S1d create SJ-so ditolak 409 SJ_SO_BARIS_SUDAH_DIFAKTUR", e.status_code == 409 and d.get("code") == "SJ_SO_BARIS_SUDAH_DIFAKTUR", str(e.detail)[:120])
                cek("S1e penolakan = NOL tulis (jurnal/kartu/SJ/alokasi/idempotensi)", await snap(conn) == s0)
                await fulfill(qty)
                cek("S1f stok keluar TEPAT 1x qty faktur", (await keluar()) - k0 == qty, f"{(await keluar()) - k0} vs {qty}")
                cek("S1g HPP 1x (qty x WAC)", await hpp_faktur() == (qty * wac).quantize(D("0.01")), f"{await hpp_faktur()} vs {(qty * wac).quantize(D('0.01'))}")
                sa, tu = await check23(conn)
                cek("S1h CHECK 23 selisih 0 (saldo Barang Terkirim == SJ belum difakturkan)", sa == tu, f"{sa} vs {tu}")
                cek("S1i Check 15 & 16 tak bergeser", await drift(conn) == d0, f"{d0} -> {await drift(conn)}")
                raise Batal()
        except Batal:
            pass

        # ---------------- S2
        print("== S2. SJ-so dulu -> faktur -> alokasi -> /fulfill sisa")
        Q = D("5")
        try:
            async with conn.transaction():
                body = S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=Q)], warehouse_id=gud)
                pv = (await R.preview_sj_so(req(), str(c["so_id"]), body))["data"]
                cek("S2a SJ-so SEBELUM faktur: diterima (ok)", pv["ok"], str(pv["blocks"])[:150])
                print("CONTOH_JSON_WAREHOUSE", json.dumps(pv["warehouse"], default=str))
                print("CONTOH_JSON_LINE", json.dumps(pv["lines"][0], default=str) if pv["lines"] else None)
                r1 = await R.create_sj_so(req("sjdf-2"), Response(), str(c["so_id"]), body)
                nilai = (Q * wac).quantize(D("0.01"))
                sa, tu = await check23(conn)
                cek("S2b Check 23: saldo == turunan == nilai SJ", sa == tu == nilai, f"{sa} vs {tu}")
                await SI.terbitkan_faktur(conn, ctx, c["inv_id"], c["invoice_number"], c["total_amount"], c["invoice_date"], apply_deposits=False)
                al = await conn.fetchrow("SELECT COALESCE(SUM(quantity),0) q, COALESCE(SUM(total_cost),0) v FROM sj_faktur_alokasi WHERE invoice_item_id=$1 AND voided_at IS NULL", c["sii_id"])
                cek("S2c alokasi tertaut ke faktur: qty Q, nilai snapshot SJ", D(str(al["q"])) == Q and D(str(al["v"])) == nilai, str(dict(al)))
                sa, tu = await check23(conn)
                cek("S2d Check 23 sesudah faktur: nol == nol", sa == tu == D("0"), f"{sa} vs {tu}")
                pv2 = (await R.preview_sj_so(req(), str(c["so_id"]), S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=D("1"))], warehouse_id=gud)))["data"]
                cek("S2e SJ-so susulan sesudah faktur DITOLAK (seluruh baris difaktur)", any(b["code"] == "SJ_SO_BARIS_SUDAH_DIFAKTUR" for b in pv2["blocks"]), str(pv2["blocks"])[:150])
                await fulfill(qty - Q)
                cek("S2f total stok keluar = qty faktur TEPAT (Q via SJ + sisa via /fulfill)", (await keluar()) - k0 == qty, f"{(await keluar()) - k0} vs {qty}")
                cek("S2g HPP total = qty x WAC (reklas Q + fulfill sisa), tanpa ganda", await hpp_faktur() == (qty * wac).quantize(D("0.01")),
                    f"{await hpp_faktur()} vs {(qty * wac).quantize(D('0.01'))}")
                sa, tu = await check23(conn)
                cek("S2h CHECK 23 selisih 0 akhir", sa == tu == D("0"), f"{sa} vs {tu}")
                cek("S2i Check 15 & 16 tak bergeser", await drift(conn) == d0, f"{d0} -> {await drift(conn)}")
                raise Batal()
        except Batal:
            pass
    finally:
        await tr.rollback()
    akhir = await snap(conn)
    cek("NOL JEJAK: hitungan kembali ke awal (ROLLBACK)", akhir == awal, f"{awal} vs {akhir}")
    print(f"\nRINGKAS: {len(OK)} OK, {len(GAGAL)} GAGAL", GAGAL)
    sys.exit(1 if GAGAL else 0)

asyncio.run(main())
