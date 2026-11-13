"""K9 uji DB di SALINAN (bukan prod): handler ASLI kartu stok barang (history) + Perbedaan Persediaan Terkirim atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A kartu stok: filter gudang (semua baris di gudang itu, warehouse_name benar), saldo awal == Σ sebelum tanggal mulai (SQL independen), saldo berjalan baris terbaru == saldo akhir ringkasan, tanpa filter ringkasan null
B Perbedaan Terkirim: PB in_transit (disiapkan di klon) -> tertunda penuh; sesudah penerimaan sebagian di LEDGER (RELOKASI_TERIMA) tertunda berkurang; lunas -> hilang dari laporan"""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from fastapi import Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.routers import stock_transfers as ST  # noqa: E402
from app.schemas.stock_transfers import CreateStockTransferRequest, StockTransferItemCreate  # noqa: E402

HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def main():
    pool = await ST.get_pool()
    async with pool.acquire() as c:
        for tid in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            rq = req(tid, uid)
            # A kartu stok
            k = await c.fetchrow("/* tenant_id */ SELECT product_id, warehouse_id, count(*) AS n FROM inventory_ledger WHERE tenant_id = $1 AND warehouse_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= 3 ORDER BY count(*) DESC LIMIT 1", tid)
            if uid is None or k is None:
                print(f"CATATAN A {tid}: tak ada pemilik/barang berjejak >= 3 gerak di satu gudang (klon); kartu stok tak terukur", flush=True)
            else:
                pid, gid = k["product_id"], k["warehouse_id"]
                nama_g = await c.fetchval("/* tenant_id */ SELECT name FROM warehouses WHERE tenant_id = $1 AND id = $2", tid, gid)
                tgl = await c.fetchval("/* tenant_id */ SELECT movement_date FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3 ORDER BY movement_date, created_at, id OFFSET $4 LIMIT 1", tid, pid, gid, k["n"] // 2)
                sebelum = D(str(await c.fetchval("/* tenant_id */ SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3 AND movement_date < $4", tid, pid, gid, tgl)))
                sesudah = await c.fetchrow("/* tenant_id */ SELECT COALESCE(SUM(quantity_in), 0) AS m, COALESCE(SUM(quantity_out), 0) AS k, count(*) AS n FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3 AND movement_date >= $4", tid, pid, gid, tgl)
                r = await IT.get_item_history(rq, pid, page=1, limit=200, warehouse_id=gid, date_from=tgl, date_to=None)
                data = r["data"]
                cek(f"A {tid} filter gudang+tanggal: {len(data)} baris == SQL independen ({sesudah['n']}), semua di gudang '{nama_g}', warehouse_name benar",
                    len(data) == sesudah["n"] and all(x["warehouse_id"] == str(gid) and x["warehouse_name"] == nama_g for x in data))
                akhir = sebelum + D(str(sesudah["m"])) - D(str(sesudah["k"]))
                rg = r["ringkasan"]
                cek(f"A {tid} saldo awal == Σ sebelum {tgl} ({sebelum}); ringkasan masuk/keluar/akhir == SQL independen", r["saldo_awal"] == sebelum and rg["total_masuk"] == D(str(sesudah["m"]))
                    and rg["total_keluar"] == D(str(sesudah["k"])) and rg["saldo_akhir"] == akhir, f"{rg}")
                cek(f"A {tid} saldo berjalan baris TERBARU == saldo akhir ({akhir}); baris terlama = saldo awal + gerak", D(str(data[0]["quantity_balance"])) == akhir
                    and D(str(data[-1]["quantity_balance"])) == sebelum + D(str(data[-1]["quantity_in"])) - D(str(data[-1]["quantity_out"])),
                    f"terbaru={data[0]['quantity_balance']} terlama={data[-1]['quantity_balance']}")
                semua = await IT.get_item_history(rq, pid, page=1, limit=5, warehouse_id=None, date_from=None, date_to=None)
                cek(f"A {tid} tanpa filter: perilaku lama (ringkasan null, saldo awal 0), kolom warehouse_name ada", semua["ringkasan"] is None and semua["saldo_awal"] == 0 and all("warehouse_name" in x for x in semua["data"]))
            # B Perbedaan Terkirim
            gud = await c.fetch("/* tenant_id */ SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, created_at LIMIT 2", tid)
            brg = await c.fetchrow("/* tenant_id */ SELECT id, item_code, nama_produk FROM products WHERE tenant_id = $1 AND COALESCE(track_inventory, false) LIMIT 1", tid)
            if uid is None or len(gud) < 2 or brg is None:
                print(f"CATATAN B {tid}: tak ada >= 2 gudang aktif / barang ber-stok; Perbedaan Terkirim tak terukur", flush=True)
                continue
            n = await c.execute("UPDATE tenant_features SET enabled = true WHERE tenant_id = $1 AND feature = 'pemindahan_barang'", tid)
            if n.endswith(" 0"):
                await c.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, 'pemindahan_barang', true)", tid)
            cr = await ST.create_stock_transfer(rq, Response(), CreateStockTransferRequest(from_warehouse_id=gud[0]["id"], to_warehouse_id=gud[1]["id"],
                                                                                           items=[StockTransferItemCreate(item_id=brg["id"], quantity_requested=D("3"))]))
            pb = cr.data.id
            await c.execute("/* tenant_id */ UPDATE stock_transfer_items SET quantity_shipped = 3, unit_cost = 1000, total_value = 3000 WHERE stock_transfer_id = $1", pb)     # draf: boleh (trigger hanya membekukan sesudah Kirim)
            await c.execute("UPDATE stock_transfers SET status = 'in_transit', shipped_date = $2 WHERE id = $1 AND tenant_id = $3", pb, date.today(), tid)

            async def lap():
                d = (await ST.get_transfer_variance(rq))
                return [x for x in d["data"] if x["transfer_id"] == str(pb)], d["summary"]

            async def terima(q):
                await c.execute("""INSERT INTO inventory_ledger (tenant_id, product_id, product_code, product_name, movement_type, movement_date, source_type, source_id, source_number,
                                   quantity_in, quantity_out, quantity_balance, unit_cost, total_cost, average_cost, warehouse_id, created_by, notes)
                                   VALUES ($1, $2, $3, $4, 'RELOKASI_TERIMA_MASUK', $5, 'RELOKASI_TERIMA', $6, 'UJI-K9', $7, 0, 0, 1000, $8, 1000, $9, $10, 'uji K9')""",
                                tid, brg["id"], brg["item_code"], brg["nama_produk"], date.today(), pb, D(q), D(q) * 1000, gud[1]["id"], uid)
            b0, s0 = await lap()
            cek(f"B {tid} PB in_transit: tertunda penuh 3, nilai 3000, bukan sebagian", len(b0) == 1 and b0[0]["quantity_tertunda"] == 3.0 and b0[0]["nilai_tertunda"] == 3000.0 and b0[0]["diterima_sebagian"] is False, f"{b0}")
            await terima("1")
            b1, _ = await lap()
            cek(f"B {tid} sesudah terima sebagian (LEDGER): tertunda 2, diterima 1, sebagian", len(b1) == 1 and b1[0]["quantity_tertunda"] == 2.0 and b1[0]["quantity_received"] == 1.0 and b1[0]["diterima_sebagian"] is True, f"{b1}")
            await terima("2")
            b2, _ = await lap()
            cek(f"B {tid} sesudah lunas: hilang dari laporan", b2 == [])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
