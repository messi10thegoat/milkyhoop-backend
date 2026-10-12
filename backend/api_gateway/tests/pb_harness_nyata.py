import asyncio, os, json, sys
from decimal import Decimal
from uuid import UUID
import asyncpg
os.environ.setdefault("OPENAI_API_KEY", "boneka")
from fastapi import HTTPException
from backend.api_gateway.app.services import pemindahan_barang as S

TID = "kaos-biru-konveksi"
OK, GAGAL = [], []

def cek(nama, cond, info=""):
    (OK if cond else GAGAL).append(nama)
    print(("  OK   " if cond else "  GAGAL ") + nama + (f"  [{info}]" if info else ""))

async def snap(conn):
    r = await conn.fetchrow("""SELECT (SELECT count(*) FROM journal_entries) je, (SELECT count(*) FROM journal_lines) jl,
        (SELECT count(*) FROM inventory_ledger) il, (SELECT count(*) FROM warehouse_stock) ws,
        (SELECT count(*) FROM stock_transfers) st, (SELECT count(*) FROM stock_transfer_items) sti,
        (SELECT coalesce(sum(total_debit),0) FROM journal_entries) debit""")
    return dict(r)

async def stok(conn, wh, p):
    return Decimal(str(await conn.fetchval("SELECT COALESCE((SELECT quantity FROM warehouse_stock WHERE tenant_id=$1 AND warehouse_id=$2 AND item_id=$3),0)", TID, wh, p)))

async def wac(conn, p):
    return Decimal(str(await conn.fetchval("SELECT get_weighted_average_cost($1,$2)", TID, p)))

async def nilai_flow(conn, p):   # Σ nilai masuk - keluar (Inv-3 per barang)
    return Decimal(str(await conn.fetchval("SELECT COALESCE(SUM(quantity_in*unit_cost - quantity_out*unit_cost),0) FROM inventory_ledger WHERE tenant_id=$1 AND product_id=$2", TID, p)))

async def arm(conn):
    a2 = await conn.fetchval("""SELECT count(*) FROM (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id=$1 AND il.source_type='RELOKASI_GUDANG'
        GROUP BY il.product_id, il.movement_date HAVING abs(sum(il.quantity_in)-sum(il.quantity_out))>0.0001
        OR abs(sum(CASE WHEN il.quantity_in>0 THEN il.total_cost ELSE -il.total_cost END))>0.01) x""", TID)
    a3 = await conn.fetchval("""SELECT count(*) FROM (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id=$1
        AND il.source_type IN ('RELOKASI_GUDANG','RELOKASI_GUDANG_VOID') GROUP BY il.source_id, il.product_id
        HAVING abs(sum(il.quantity_in)-sum(il.quantity_out))>0.0001
        OR abs(sum(CASE WHEN il.quantity_in>0 THEN il.total_cost ELSE -il.total_cost END))>0.01) x""", TID)
    return a2, a3

async def main():
    conn = await asyncpg.connect(os.environ["URL"])
    uid = await conn.fetchval("SELECT created_by FROM stock_transfers LIMIT 1") or await conn.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", TID)
    ctx = {"tenant_id": TID, "user_id": str(uid) if uid else None}
    awal = await snap(conn)
    tr = conn.transaction(); await tr.start()
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TID)
    try:
        print("== 0. keadaan")
        cek("tabel stock_transfers kosong (prasyarat V412)", awal["st"] == 0, str(awal["st"]))
        await conn.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1,'pemindahan_barang',true) ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled=true", TID)
        asal = await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id=$1 AND is_default ORDER BY created_at LIMIT 1", TID)
        tujuan = await conn.fetchval("""INSERT INTO warehouses (tenant_id, code, name, is_active) VALUES ($1,'TES-E2E-B','TES E2E Gudang B',true) RETURNING id""", TID)
        cand = await conn.fetch("""SELECT ws.item_id, ws.quantity, p.nama_produk FROM warehouse_stock ws JOIN products p ON p.id=ws.item_id
            WHERE ws.tenant_id=$1 AND ws.warehouse_id=$2 AND ws.quantity>=3 AND p.track_inventory AND NOT COALESCE(p.track_batches,false)
              AND NOT COALESCE(p.track_serial,false) ORDER BY ws.quantity DESC""", TID, asal)
        cek("ada barang kandidat ber-stok (harness tak hampa)", len(cand) > 0, f"{len(cand)} kandidat")
        P1 = cand[0]["item_id"]; NAMA = cand[0]["nama_produk"]
        w0, s_asal0, s_tuj0, v0 = await wac(conn, P1), await stok(conn, asal, P1), await stok(conn, tujuan, P1), await nilai_flow(conn, P1)
        cek("WAC awal > 0", w0 > 0, f"{NAMA} WAC={w0} stok_asal={s_asal0}")
        so = await conn.fetchval("SELECT id FROM sales_orders WHERE tenant_id=$1 AND status NOT IN ('draft','cancelled','completed') ORDER BY created_at DESC LIMIT 1", TID)
        cek("ada SO berjalan untuk uji fase", so is not None)
        pre = await snap(conn)
        a2, a3 = await arm(conn)
        print("== 1. draf -> selesai (SO tertaut)")
        body = S.PemindahanIn(from_warehouse_id=asal, to_warehouse_id=tujuan, sales_order_id=so,
                              items=[S.BarangIn(item_id=P1, quantity_requested=Decimal("2"))])
        tid_pb = await S.buat_draf(conn, ctx, body)
        cek("draf tak menyentuh kartu/stok/jurnal", (await snap(conn))["il"] == pre["il"] and await stok(conn, asal, P1) == s_asal0)
        r = await S.selesaikan(conn, ctx, tid_pb)
        cek("selesai (bukan ulang)", r["ulang"] is False)
        sesudah = await snap(conn)
        cek("kartu +2 baris", sesudah["il"] == pre["il"] + 2, f"{pre['il']} -> {sesudah['il']}")
        cek("jurnal NOL berubah (je/jl/total debit)", (sesudah["je"], sesudah["jl"], sesudah["debit"]) == (pre["je"], pre["jl"], pre["debit"]))
        cek("stok asal -2", await stok(conn, asal, P1) == s_asal0 - 2)
        cek("stok tujuan +2", await stok(conn, tujuan, P1) == s_tuj0 + 2)
        cek("WAC TIDAK berubah (P6)", await wac(conn, P1) == w0, f"{w0} -> {await wac(conn, P1)}")
        cek("nilai kartu produk neto nol (Inv-3 per barang)", await nilai_flow(conn, P1) == v0)
        cek("Σ stok produk antargudang tetap", (await stok(conn, asal, P1)) + (await stok(conn, tujuan, P1)) == s_asal0 + s_tuj0)
        fase = await conn.fetchrow("SELECT fase_lokasi, fase_gudang_id FROM sales_orders WHERE id=$1", so)
        cek("fase SO = tersedia @ tujuan", fase["fase_lokasi"] == "tersedia" and fase["fase_gudang_id"] == tujuan)
        led = await conn.fetch("SELECT movement_type, source_type, journal_id, unit_cost, average_cost, quantity_in, quantity_out FROM inventory_ledger WHERE source_id=$1 ORDER BY created_at", tid_pb)
        cek("kartu: keluar+masuk, journal_id NULL, avg=unit=WAC", len(led) == 2 and all(x["journal_id"] is None and x["average_cost"] == x["unit_cost"] == w0 for x in led))
        a2b, a3b = await arm(conn)
        cek("lengan Check 21 (per-hari, per-dokumen) tetap 0 pelanggaran", (a2b, a3b) == (a2, a3), f"{(a2,a3)} -> {(a2b,a3b)}")
        hdr = await conn.fetchrow("SELECT status, total_items, total_quantity, total_value FROM stock_transfers WHERE id=$1", tid_pb)
        cek("header completed + total diisi trigger", hdr["status"] == "completed" and hdr["total_items"] == 1 and hdr["total_quantity"] == 2 and hdr["total_value"] == (w0*2).quantize(Decimal("0.01")), str(dict(hdr)))
        print("== 2. idempoten")
        r2 = await S.selesaikan(conn, ctx, tid_pb)
        cek("selesaikan ulang = ulang, tanpa tulis", r2["ulang"] is True and (await snap(conn))["il"] == sesudah["il"])
        print("== 3. penolakan")
        b2 = S.PemindahanIn(from_warehouse_id=asal, to_warehouse_id=tujuan, items=[S.BarangIn(item_id=P1, quantity_requested=s_asal0 + 1000)])
        t2 = await S.buat_draf(conn, ctx, b2)
        sn = await snap(conn)
        try:
            await S.selesaikan(conn, ctx, t2); ok = False
        except HTTPException as e:
            ok = e.status_code == 409 and e.detail["code"] == "PB_STOK_TIDAK_CUKUP"
        cek("stok kurang -> 409 PB_STOK_TIDAK_CUKUP, nol baris", ok and (await snap(conn))["il"] == sn["il"])
        print("== 4. void")
        sv = await snap(conn)
        v = await S.void(conn, ctx, tid_pb, "uji harness")
        cek("void (bukan ulang)", v["ulang"] is False)
        cek("kartu +2 pembalik", (await snap(conn))["il"] == sv["il"] + 2)
        cek("stok kembali (asal, tujuan)", await stok(conn, asal, P1) == s_asal0 and await stok(conn, tujuan, P1) == s_tuj0)
        cek("WAC tetap setelah void", await wac(conn, P1) == w0)
        cek("nilai kartu produk tetap neto nol setelah void", await nilai_flow(conn, P1) == v0)
        a2c, a3c = await arm(conn)
        cek("lengan Check 21 tetap 0 setelah void", (a2c, a3c) == (a2, a3), f"{(a2c,a3c)}")
        fase2 = await conn.fetchrow("SELECT fase_lokasi, fase_gudang_id FROM sales_orders WHERE id=$1", so)
        cek("fase SO dikosongkan setelah void", fase2["fase_lokasi"] is None and fase2["fase_gudang_id"] is None)
        cek("jurnal tetap NOL berubah", (await snap(conn))["je"] == pre["je"])
        try:
            async with conn.transaction():      # savepoint: galat yang diharapkan tak membatalkan transaksi harness
                await conn.execute("UPDATE stock_transfers SET shipped_date = shipped_date + 1 WHERE id=$1", tid_pb)
            bek = False
            print("    [probe] UPDATE lolos tanpa galat")
        except Exception as e:
            bek = "Cannot modify" in str(e)
            print("    [probe exc]", type(e).__name__, str(e)[:160])
        cek("voided/completed dibekukan trigger (ubah tanggal kirim ditolak)", bek)
        print("== 5. SABOTASE: lengan 3 hidup (void sepihak)")
        await conn.execute("""INSERT INTO inventory_ledger (tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,
            quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
            SELECT tenant_id,product_id,product_code,product_name,'RELOKASI_GUDANG_VOID',movement_date,'RELOKASI_GUDANG_VOID',gen_random_uuid(),'UJI',0,1,quantity_balance,unit_cost,unit_cost,average_cost,warehouse_id
            FROM inventory_ledger WHERE source_id=$1 AND movement_type='RELOKASI_KELUAR' LIMIT 1""", tid_pb)
        a2d, a3d = await arm(conn)
        cek("lengan 3 MERAH pada void sepihak", a3d > a3c and a2d == a2c, f"{(a2d,a3d)}")

        print("== 6. v2: pratinjau == tulis, atomik, idempotensi, riwayat, ringkasan, pencarian (lewat HANDLER rute)")
        from types import SimpleNamespace
        from starlette.requests import Request
        from fastapi import Response
        from backend.api_gateway.app.routers import stock_transfers as R
        from backend.api_gateway.app.schemas.stock_transfers import CreateStockTransferRequest, VoidTransferRequest

        class _CM:
            async def __aenter__(self_): return conn
            async def __aexit__(self_, *a): return False
        class _Pool:
            def acquire(self_): return _CM()
        async def _gp(): return _Pool()
        R.get_pool = _gp

        def req(kunci=None):
            hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
            r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""})
            r.state.user = {"tenant_id": TID, "user_id": ctx["user_id"]}
            return r
        sebelum = await snap(conn)
        st_asal_b, st_tuj_b = await stok(conn, asal, P1), await stok(conn, tujuan, P1)   # sabotase #5 menggeser 1 unit: bandingkan relatif
        # (a) pratinjau baru: nol tulis, angka == tulis
        isi = S.PratinjauIn(from_warehouse_id=asal, to_warehouse_id=tujuan, sales_order_id=so,
                            items=[S.BarangPratinjau(item_id=P1, quantity_requested=Decimal("3"))])
        pv = await R.preview_stock_transfer(req(), isi)
        cek("pratinjau: ok, nol tulis", pv["data"]["ok"] and (await snap(conn)) == sebelum)
        ln = pv["data"]["lines"][0]
        cek("pratinjau: sales_order.fase_sesudah tersedia @ tujuan", pv["data"]["sales_order"]["fase_sesudah"]["fase_lokasi"] == "tersedia")
        # (b) atomik create-and-complete + idempotensi
        body = CreateStockTransferRequest(from_warehouse_id=asal, to_warehouse_id=tujuan, transfer_date=None, sales_order_id=so,
                                          items=[{"item_id": P1, "item_name": "x", "quantity_requested": Decimal("3")}])
        r1 = await R.create_and_complete_stock_transfer(req("kunci-uji-1"), Response(), body)
        d1 = r1.data if hasattr(r1, "data") else r1["data"]
        nomor1 = d1.transfer_number if hasattr(d1, "transfer_number") else d1["transfer_number"]
        led1 = await conn.fetch("SELECT unit_cost, total_cost, quantity_out, quantity_in FROM inventory_ledger WHERE source_number=$1 ORDER BY movement_type", nomor1)
        cek("atomik: status completed + 2 kartu", len(led1) == 2)
        cek("angka pratinjau == angka tulis (nilai)", Decimal(str(ln["value"])) == led1[0]["total_cost"] and Decimal(str(ln["unit_cost"])) == led1[0]["unit_cost"])
        resp2 = Response()
        r2 = await R.create_and_complete_stock_transfer(req("kunci-uji-1"), resp2, body)
        n2 = (r2.data.transfer_number if hasattr(r2, "data") else r2["data"]["transfer_number"])
        cek("idempotensi: kunci sama = respons asli, tak ada transfer kedua", n2 == nomor1 and
            await conn.fetchval("SELECT count(*) FROM stock_transfers WHERE tenant_id=$1 AND transfer_number=$2", TID, nomor1) == 1)
        cek("idempotensi: header X-Idempotent-Replay", resp2.headers.get("X-Idempotent-Replay") == "true")
        body_beda = CreateStockTransferRequest(from_warehouse_id=asal, to_warehouse_id=tujuan,
                                               items=[{"item_id": P1, "item_name": "x", "quantity_requested": Decimal("1")}])
        try:
            await R.create_and_complete_stock_transfer(req("kunci-uji-1"), Response(), body_beda); bk = False
        except HTTPException as e:
            bk = e.status_code == 409 and e.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
        cek("idempotensi: kunci sama isi beda -> 409 IDEMPOTENCY_KEY_REUSED", bk)
        # (c) atomik gagal -> tak ada draf yatim (savepoint)
        cnt0 = await conn.fetchval("SELECT count(*) FROM stock_transfers WHERE tenant_id=$1", TID)
        seq0 = await conn.fetchval("SELECT last_number FROM stock_transfer_sequences WHERE tenant_id=$1 ORDER BY year_month DESC LIMIT 1", TID)
        besar = CreateStockTransferRequest(from_warehouse_id=asal, to_warehouse_id=tujuan,
                                           items=[{"item_id": P1, "item_name": "x", "quantity_requested": Decimal("999999")}])
        try:
            async with conn.transaction():
                await R.create_and_complete_stock_transfer(req(), Response(), besar)
            gagal = False
        except HTTPException as e:
            gagal = e.detail["code"] == "PB_STOK_TIDAK_CUKUP"
        cek("atomik gagal -> 409 PB_STOK_TIDAK_CUKUP", gagal)
        cek("atomik gagal -> NOL draf yatim + nomor urut ikut rollback",
            await conn.fetchval("SELECT count(*) FROM stock_transfers WHERE tenant_id=$1", TID) == cnt0 and
            await conn.fetchval("SELECT last_number FROM stock_transfer_sequences WHERE tenant_id=$1 ORDER BY year_month DESC LIMIT 1", TID) == seq0)
        # (d) pratinjau void: stok sebelum/sesudah, lalu riwayat + ringkasan + pencarian
        idpb = await conn.fetchval("SELECT id FROM stock_transfers WHERE tenant_id=$1 AND transfer_number=$2", TID, nomor1)
        pvv = (await R.preview_void_stock_transfer(req(), idpb, R.AlasanIn(reason="uji")))["data"]
        l = pvv["lines"][0]
        cek("pratinjau void: ok + sebelum/sesudah per gudang", pvv["ok"] and l["from_after"] == l["from_before"] - 3 and l["to_after"] == l["to_before"] + 3)
        pvc = (await R.preview_complete_stock_transfer(req(), idpb))["data"]
        cek("pratinjau complete pada yang sudah selesai: ulang=true", pvc["ok"] and pvc["ulang"] is True)
        from backend.api_gateway.app.services.so_riwayat import riwayat_pemindahan
        async def semua(m): return True
        h = await riwayat_pemindahan(conn, TID, idpb, semua, 200)
        jenis = [e["jenis"] for e in h["events"]]
        cek("riwayat: dibuat + selesai tercatat dengan aktor", "PEMINDAHAN_BARANG_SELESAI" in jenis and "PEMINDAHAN_BARANG_DIBUAT" in jenis and h["total"] >= 2, str(jenis))
        sm = (await R.summary_stock_transfers(req()))["data"]
        cek("ringkasan: selesai_bulan_ini >= 1", sm["selesai_bulan_ini"] >= 1 and sm["nilai_selesai_bulan_ini"] > 0, str(sm))
        lst = await R.list_stock_transfers(req(), skip=0, limit=50, status=None, from_warehouse_id=None, to_warehouse_id=None,
                                           date_from=None, date_to=None, search=nomor1[-4:])
        cek("pencarian nomor PB", any(x.transfer_number == nomor1 for x in lst.data))
        sono = await conn.fetchval("SELECT order_number FROM sales_orders WHERE id=$1", so)
        lst2 = await R.list_stock_transfers(req(), skip=0, limit=50, status=None, from_warehouse_id=None, to_warehouse_id=None,
                                            date_from=None, date_to=None, search=sono.lower())
        cek("pencarian nomor SO (tak peka huruf)", any(x.transfer_number == nomor1 for x in lst2.data))
        lst3 = await R.list_stock_transfers(req(), skip=0, limit=50, status=None, from_warehouse_id=None, to_warehouse_id=None,
                                            date_from=None, date_to=None, search="%")
        cek("pencarian '%' tak jadi wildcard", len(lst3.data) == 0)
        namabarang = await conn.fetchval("SELECT nama_produk FROM products WHERE id=$1", P1)
        lst4 = await R.list_stock_transfers(req(), skip=0, limit=50, status=None, from_warehouse_id=None, to_warehouse_id=None,
                                            date_from=None, date_to=None, search=namabarang[:5].upper())
        cek("pencarian nama barang", any(x.transfer_number == nomor1 for x in lst4.data))
        await R.void_stock_transfer(req(), idpb, VoidTransferRequest(reason="uji harness v2"))
        cek("void lewat handler: stok kembali ke keadaan sebelum bagian 6", await stok(conn, asal, P1) == st_asal_b and await stok(conn, tujuan, P1) == st_tuj_b)
    finally:
        await tr.rollback()
    akhir = await snap(conn)
    cek("NOL JEJAK: seluruh hitungan kembali ke awal (ROLLBACK)", akhir == awal, f"{awal} vs {akhir}")
    print(f"\nRINGKAS: {len(OK)} OK, {len(GAGAL)} GAGAL", GAGAL)
    sys.exit(1 if GAGAL else 0)

asyncio.run(main())
