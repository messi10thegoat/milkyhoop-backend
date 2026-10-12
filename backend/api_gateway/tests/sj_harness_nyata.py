import asyncio, os, sys
from decimal import Decimal
from uuid import UUID
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException, Response
from starlette.requests import Request
from backend.api_gateway.app.services import sj_so as S
from backend.api_gateway.app.routers import sj_so as R

OK, GAGAL = [], []

def cek(nama, cond, info=""):
    (OK if cond else GAGAL).append(nama)
    print(("  OK   " if cond else "  GAGAL ") + nama + (f"  [{info}]" if info else ""))

async def snap(conn):
    return dict(await conn.fetchrow("""SELECT (SELECT count(*) FROM journal_entries) je, (SELECT count(*) FROM journal_lines) jl,
        (SELECT count(*) FROM inventory_ledger) il, (SELECT count(*) FROM warehouse_stock) ws,
        (SELECT coalesce(sum(quantity),0) FROM warehouse_stock) wsq,
        (SELECT count(*) FROM invoice_fulfillments) f, (SELECT count(*) FROM invoice_fulfillment_items) fi,
        (SELECT count(*) FROM sj_faktur_alokasi) a, (SELECT count(*) FROM audit_logs) au"""))

async def main():
    conn = await asyncpg.connect(os.environ["URL"])
    awal = await snap(conn)
    tr = conn.transaction(); await tr.start()
    try:
        c = await conn.fetchrow("""
            SELECT so.id so_id, so.tenant_id tid, so.created_by uid, soi.id soi_id, soi.quantity q, sii.id sii_id, si.id inv_id,
                   so.shipped_qty, so.status, COALESCE(sii.fulfilled_qty,0) ful
            FROM sales_orders so JOIN sales_order_items soi ON soi.sales_order_id=so.id
            LEFT JOIN products p ON p.id=soi.item_id AND p.tenant_id=so.tenant_id
            JOIN sales_invoice_items sii ON sii.sales_order_item_id=soi.id
            JOIN sales_invoices si ON si.id=sii.invoice_id AND si.status NOT IN ('draft','void')
            WHERE COALESCE(soi.perlu_kirim,p.track_inventory,false) AND NOT COALESCE(p.track_inventory,false)
              AND so.status NOT IN ('draft','cancelled','completed') AND soi.quantity - COALESCE(sii.fulfilled_qty,0) >= 2
              AND so.tenant_id = 'grapgrap-manado'
              AND NOT EXISTS (SELECT 1 FROM invoice_fulfillments f WHERE f.sales_order_id = so.id)
            ORDER BY so.created_at DESC LIMIT 1""")
        cek("kandidat SO non-stok berfaktur ada (harness tak hampa)", c is not None)
        TID = c["tid"]; so_id = c["so_id"]; uid = str(c["uid"]) if c["uid"] else None
        ctx = {"tenant_id": TID, "user_id": uid}
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TID)
        await conn.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1,'pengiriman_sebelum_faktur',true) ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled=true", TID)
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
        qty_kirim = Decimal("2")
        body = S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=qty_kirim)])
        s0 = await snap(conn)
        ship0, st0 = Decimal(str(c["shipped_qty"] or 0)), c["status"]
        print("== 1. pratinjau + tulis")
        pv = await R.preview_sj_so(req(), str(so_id), body)
        cek("pratinjau ok, nol tulis", pv["data"]["ok"] and (await snap(conn)) == s0, str(pv["data"]["blocks"]))
        r1 = await R.create_sj_so(req("sj-uji-1"), Response(), str(so_id), body)
        fid = UUID(r1["data"]["id"])
        s1 = await snap(conn)
        cek("header asal 'so', invoice_id NULL", await conn.fetchval(
            "SELECT asal = 'so' AND invoice_id IS NULL AND sales_order_id = $2 FROM invoice_fulfillments WHERE id = $1", fid, so_id))
        cek("item: invoice_item_id NULL + sales_order_item_id", await conn.fetchval(
            "SELECT count(*) FROM invoice_fulfillment_items WHERE fulfillment_id = $1 AND invoice_item_id IS NULL AND sales_order_item_id = $2",
            fid, c["soi_id"]) == 1)
        cek("NOL buku: jurnal/kartu/stok tak berubah", (s1["je"], s1["jl"], s1["il"], s1["ws"], s1["wsq"]) == (s0["je"], s0["jl"], s0["il"], s0["ws"], s0["wsq"]))
        cek("tepat +1 SJ +1 baris, +audit", (s1["f"], s1["fi"]) == (s0["f"] + 1, s0["fi"] + 1) and s1["au"] > s0["au"])
        so1 = await conn.fetchrow("SELECT shipped_qty, status FROM sales_orders WHERE id=$1", so_id)
        cek("trigger SO: shipped_qty naik sebesar qty", Decimal(str(so1["shipped_qty"])) == ship0 + qty_kirim, f"{ship0} -> {so1['shipped_qty']} status {st0}->{so1['status']}")
        rec = await conn.fetch("SELECT * FROM verify_so_shipped_reconciliation($1) WHERE so_id = $2", TID, so_id)
        cek("penjaga harian rekonsiliasi shipped: SO ini selaras", len(rec) == 0)
        print("== 2. idempotensi")
        resp2 = Response()
        r2 = await R.create_sj_so(req("sj-uji-1"), resp2, str(so_id), body)
        cek("kunci sama = respons asli, tak ada SJ kedua", r2["data"]["id"] == r1["data"]["id"] and (await snap(conn))["f"] == s1["f"])
        cek("header X-Idempotent-Replay", resp2.headers.get("X-Idempotent-Replay") == "true")
        try:
            await R.create_sj_so(req("sj-uji-1"), Response(), str(so_id),
                                 S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=Decimal("1"))])); bk = False
        except HTTPException as e:
            bk = e.status_code == 409 and e.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
        cek("isi beda -> 409 IDEMPOTENCY_KEY_REUSED", bk)
        print("== 3. qty melebihi sisa + stok")
        try:
            async with conn.transaction():
                await R.create_sj_so(req(), Response(), str(so_id),
                                     S.SJSOIn(items=[S.BarisIn(sales_order_item_id=c["soi_id"], quantity=Decimal("99999"))]))
            bl = False
        except HTTPException as e:
            bl = e.detail["code"] == "SJ_SO_QTY_MELEBIHI_SISA"
        cek("qty melebihi sisa -> 409", bl)
        print("== 4. alokasi ke faktur (INSERT-only) tanpa hitung ganda SO")
        total = await S.alokasikan_saat_faktur(conn, TID, uid, c["inv_id"])
        cek("alokasi = qty SJ (<= kebutuhan faktur)", total == qty_kirim, str(total))
        ful = await conn.fetchval("SELECT fulfilled_qty FROM sales_invoice_items WHERE id=$1", c["sii_id"])
        cek("fulfilled_qty baris faktur naik sebesar alokasi", Decimal(str(ful)) == Decimal(str(c["ful"])) + qty_kirim)
        so2 = await conn.fetchrow("SELECT shipped_qty FROM sales_orders WHERE id=$1", so_id)
        cek("shipped_qty SO TIDAK dihitung ganda sesudah alokasi", Decimal(str(so2["shipped_qty"])) == ship0 + qty_kirim)
        s2 = await snap(conn)
        cek("alokasi: +1 baris, jurnal/kartu tetap", s2["a"] == s1["a"] + 1 and (s2["je"], s2["il"]) == (s1["je"], s1["il"]))
        again = await S.alokasikan_saat_faktur(conn, TID, uid, c["inv_id"])
        cek("alokasi kedua kali = 0 (tak ada sisa SJ)", again == 0)
        try:
            async with conn.transaction():
                await conn.execute("INSERT INTO sj_faktur_alokasi (tenant_id, fulfillment_item_id, invoice_item_id, quantity) "
                                   "SELECT $1, i.id, $2, 1000000 FROM invoice_fulfillment_items i WHERE i.fulfillment_id=$3", TID, c["sii_id"], fid)
            pg = False
        except Exception as e:
            pg = "melebihi" in str(e)
        cek("predikat DB: alokasi > qty ditolak trigger", pg)
        print("== 5. void SJ")
        try:
            async with conn.transaction():
                await R.void_sj(req(), str(fid), R.AlasanIn(reason="uji"))
            vb = False
        except HTTPException as e:
            vb = e.detail["code"] == "SJ_SUDAH_DITAGIH"
        cek("void ditolak selama ada alokasi aktif", vb)
        n = await S.lepas_alokasi_faktur(conn, TID, c["inv_id"], "uji")
        cek("lepas alokasi (void faktur) menandai batal", n == 1)
        await R.void_sj(req(), str(fid), R.AlasanIn(reason="salah kirim"))
        so3 = await conn.fetchrow("SELECT shipped_qty, status FROM sales_orders WHERE id=$1", so_id)
        cek("void SJ: shipped_qty kembali", Decimal(str(so3["shipped_qty"])) == ship0, f"{so3['shipped_qty']} {so3['status']}")
        rv = await R.void_sj(req(), str(fid), R.AlasanIn(reason="ulang"))
        cek("void idempoten", rv["ulang"] is True)
        s3 = await snap(conn)
        cek("buku tetap NOL berubah sepanjang alur", (s3["je"], s3["jl"], s3["il"], s3["ws"], s3["wsq"]) == (awal["je"], awal["jl"], awal["il"], awal["ws"], awal["wsq"]))
    finally:
        await tr.rollback()
    akhir = await snap(conn)
    cek("NOL JEJAK: hitungan kembali ke awal (ROLLBACK)", akhir == awal, f"{awal} vs {akhir}")
    print(f"\nRINGKAS: {len(OK)} OK, {len(GAGAL)} GAGAL", GAGAL)
    sys.exit(1 if GAGAL else 0)

asyncio.run(main())
