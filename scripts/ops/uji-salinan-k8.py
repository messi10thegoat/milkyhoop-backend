"""K8 uji DB di SALINAN (bukan prod; V497 diterapkan di salinan oleh pembungkus): handler ASLI Pemindahan Barang dua langkah + daftar gudang atas klon, tenant kaos + nyawit.
A Kirim: Σ on_hand ledger produk TETAP, asal turun, Transit naik, NOL jurnal, verify_inventory_wac_reconciliation_all (Check 15) TAK berubah; Transit tak tampil di daftar/ringkasan gudang, bawaan tetap
B Terima sebagian lalu sisa: tertunda benar, status_text turunan, tujuan naik, Transit akhir 0; terima > tertunda ditolak; void setelah diterima ditolak
C Kirim lalu void: barang kembali dari Transit ke asal; Transit TIDAK bisa dipilih sebagai gudang tujuan; satu gudang nyata + Transit tetap punya gudang bawaan"""
import asyncio
import sys
from decimal import Decimal as D

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_transfers as ST  # noqa: E402
from app.routers import warehouses as WH  # noqa: E402
from app.schemas.stock_transfers import (CreateStockTransferRequest, ReceiveItemRequest, ReceiveTransferRequest, StockTransferItemCreate,  # noqa: E402
                                         VoidTransferRequest)
from app.services import gudang_bawaan as GBW  # noqa: E402
from app.services import gudang_transit as GT  # noqa: E402

HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


def kode(e):
    d = getattr(e, "detail", None)
    return d.get("code") if isinstance(d, dict) else getattr(e, "code", None)


async def main():
    pool = await ST.get_pool()
    async with pool.acquire() as c:
        for tid in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            gud = await c.fetch("/* tenant_id */ SELECT id, name, is_default FROM warehouses WHERE tenant_id = $1 AND is_active AND NOT is_system ORDER BY is_default DESC, created_at", tid)
            kolom = await c.fetchval("/* tenant_id */ SELECT count(*) FROM information_schema.columns WHERE table_name = 'warehouses' AND column_name = 'is_system'")
            cek(f"prasyarat {tid}: V497 terapan, pemilik, >= 2 gudang aktif", kolom == 1 and uid is not None and len(gud) >= 2)
            if kolom != 1 or uid is None or len(gud) < 2:
                continue
            asal, tuj = gud[0], gud[1]
            brg = await c.fetchrow("""/* tenant_id */ SELECT ws.item_id, ws.available_quantity FROM warehouse_stock ws JOIN products p ON p.id = ws.item_id AND p.tenant_id = ws.tenant_id
                WHERE ws.tenant_id = $1 AND ws.warehouse_id = $2 AND ws.available_quantity >= 3 AND COALESCE(p.track_inventory, false)
                  AND NOT COALESCE(p.track_batches, false) AND NOT COALESCE(p.track_serial, false) AND get_weighted_average_cost($1, ws.item_id) > 0
                ORDER BY ws.available_quantity DESC LIMIT 1""", tid, asal["id"])
            if brg is None:
                print(f"CATATAN {tid}: tak ada barang ber-stok >= 3 di gudang {asal['name']} (klon); K8 tak terukur", flush=True)
                continue
            pid = brg["item_id"]
            n = await c.execute("UPDATE tenant_features SET enabled = true WHERE tenant_id = $1 AND feature = 'pemindahan_barang'", tid)
            if n.endswith(" 0"):
                await c.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, 'pemindahan_barang', true)", tid)
            rq = req(tid, uid)

            async def saldo_total():
                return D(str(await c.fetchval("/* tenant_id */ SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2", tid, pid)))

            async def saldo_g(gid):
                if gid is None:
                    return D(0)
                return D(str(await c.fetchval("/* tenant_id */ SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, pid, gid)))

            async def verdict():
                r = await c.fetchrow("/* tenant_id */ SELECT verdict, drift FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = $1", tid)
                return (r["verdict"], r["drift"]) if r else None

            async def daftar_id():
                d = await WH.list_warehouses(rq, skip=0, limit=100, search=None, is_active=None, is_branch=None, aksi=None, with_totals=False)
                return sorted(str(g.id) for g in d.data), d.total

            async def jurnal(pb):
                return await c.fetchval("/* tenant_id */ SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", tid, pb)

            t0, a0, u0, v0 = await saldo_total(), await saldo_g(asal["id"]), await saldo_g(tuj["id"]), await verdict()
            ids0, tot0 = await daftar_id()
            bw0 = (await GBW.bawaan(c, tid))
            body = CreateStockTransferRequest(from_warehouse_id=asal["id"], to_warehouse_id=tuj["id"], items=[StockTransferItemCreate(item_id=pid, quantity_requested=D("3"))])
            cr = await galat(ST.create_stock_transfer(rq, Response(), body))
            if isinstance(cr, HTTPException):
                cek(f"{tid} buat draf PB", False, f"{cr.status_code} {cr.detail}")
                continue
            pb = cr["data"]["id"] if isinstance(cr, dict) else cr.data.id
            # A Kirim
            pv = (await ST.preview_ship_stock_transfer(rq, pb))["data"]
            cek(f"A {tid} pratinjau Kirim ok, menyebut lokasi Transit, tanpa jurnal", pv["ok"] is True and pv["lokasi_transit"] == "Transit" and "Tanpa jurnal" in pv["catatan"], f"{pv['blocks']}")
            sh = await galat(ST.ship_stock_transfer(rq, pb))
            if isinstance(sh, HTTPException):
                cek(f"A {tid} Kirim", False, f"{sh.status_code} {sh.detail}")
                continue
            tr = await GT.id_transit(c, tid)
            cek(f"A {tid} Kirim: status in_transit 'Dikirim'", sh.data.status == "in_transit" and sh.data.status_text == "Dikirim" and sh.data.diterima_sebagian is False and tr is not None)
            cek(f"A {tid} Kirim: Σ on_hand TETAP, asal -3, Transit +3 (kartu stok asal → Transit)", await saldo_total() == t0 and await saldo_g(asal["id"]) == a0 - 3 and await saldo_g(tr) == 3,
                f"total {t0}->{await saldo_total()} asal {a0}->{await saldo_g(asal['id'])} transit {await saldo_g(tr)}")
            cek(f"A {tid} Kirim: NOL jurnal", await jurnal(pb) == 0)
            cek(f"A {tid} Check 15/Inv-3 (verify_inventory_wac_reconciliation_all) TAK berubah sesudah Kirim tanpa jurnal", await verdict() == v0, f"{v0} -> {await verdict()}")
            ids1, tot1 = await daftar_id()
            ringkas = (await WH.warehouses_summary(rq))["data"]
            cek(f"A {tid} Transit TAK tampil di daftar/ringkasan gudang; bawaan tetap", ids1 == ids0 and tot1 == tot0 and str(tr) not in ids1 and ringkas["total_count"] == tot0
                and (await GBW.bawaan(c, tid)) == bw0)
            dt = (await ST.get_stock_transfer(rq, pb)).data
            cek(f"A {tid} detail memuat status_text", dt.status_text == "Dikirim")
            # B Terima
            pt = (await ST.preview_receive_stock_transfer(rq, pb, ReceiveTransferRequest(items=[ReceiveItemRequest(item_id=pid, quantity_received=D("1"))])))["data"]
            cek(f"B {tid} pratinjau Terima sebagian: ok, tertunda sesudah 2, status tetap in_transit", pt["ok"] is True and pt["status_sesudah"] == "in_transit" and pt["lines"][0]["tertunda_sesudah"] == 2, f"{pt['blocks']}")
            rc = await galat(ST.receive_stock_transfer(rq, pb, ReceiveTransferRequest(items=[ReceiveItemRequest(item_id=pid, quantity_received=D("1"))])))
            cek(f"B {tid} Terima 1: 'Dikirim · diterima sebagian', tujuan +1, Transit 2, Σ tetap", not isinstance(rc, HTTPException) and rc.data.status == "in_transit" and rc.data.diterima_sebagian is True
                and await saldo_g(tuj["id"]) == u0 + 1 and await saldo_g(tr) == 2 and await saldo_total() == t0, f"{rc if isinstance(rc, HTTPException) else rc.data.status_text}")
            lebih = await galat(ST.receive_stock_transfer(rq, pb, ReceiveTransferRequest(items=[ReceiveItemRequest(item_id=pid, quantity_received=D("5"))])))
            cek(f"B {tid} Terima > tertunda ditolak PB_TERIMA_QTY_MELEBIHI, nol tulis", isinstance(lebih, HTTPException) and kode(lebih) == "PB_TERIMA_QTY_MELEBIHI" and await saldo_g(tr) == 2)
            sisa = await galat(ST.receive_stock_transfer(rq, pb, None))
            cek(f"B {tid} Terima sisa: status received 'Diterima', Transit 0, tujuan +3, asal -3, Σ tetap, nol jurnal",
                not isinstance(sisa, HTTPException) and sisa.data.status == "received" and sisa.data.status_text == "Diterima" and await saldo_g(tr) == 0
                and await saldo_g(tuj["id"]) == u0 + 3 and await saldo_g(asal["id"]) == a0 - 3 and await saldo_total() == t0 and await jurnal(pb) == 0)
            cek(f"B {tid} Check 15/Inv-3 tak berubah sesudah Terima", await verdict() == v0)
            vd = await galat(ST.void_stock_transfer(rq, pb, VoidTransferRequest(reason="uji")))
            cek(f"B {tid} void sesudah diterima ditolak PB_DUA_LANGKAH_SUDAH_DITERIMA", isinstance(vd, HTTPException) and kode(vd) == "PB_DUA_LANGKAH_SUDAH_DITERIMA", f"{getattr(vd, 'status_code', '')} {getattr(vd, 'detail', vd)}")
            # C Kirim lalu void
            a1, u1 = await saldo_g(asal["id"]), await saldo_g(tuj["id"])
            cr2 = await galat(ST.create_stock_transfer(rq, Response(), CreateStockTransferRequest(from_warehouse_id=asal["id"], to_warehouse_id=tuj["id"], items=[StockTransferItemCreate(item_id=pid, quantity_requested=D("2"))])))
            pb2 = cr2["data"]["id"] if isinstance(cr2, dict) else cr2.data.id
            await ST.ship_stock_transfer(rq, pb2)
            v2 = await galat(ST.void_stock_transfer(rq, pb2, VoidTransferRequest(reason="uji")))
            cek(f"C {tid} Kirim lalu void: barang kembali ke asal, Transit 0, status voided", not isinstance(v2, HTTPException) and await saldo_g(asal["id"]) == a1 and await saldo_g(tr) == 0
                and await saldo_total() == t0 and (await ST.get_stock_transfer(rq, pb2)).data.status == "voided", f"{v2 if isinstance(v2, HTTPException) else ''}")
            ke_transit = await galat(ST.create_stock_transfer(rq, Response(), CreateStockTransferRequest(from_warehouse_id=asal["id"], to_warehouse_id=tr, items=[StockTransferItemCreate(item_id=pid, quantity_requested=D("1"))])))
            cek(f"C {tid} Transit TAK bisa dipilih sebagai gudang tujuan pemindahan", isinstance(ke_transit, HTTPException) or "TAK_ADA" in str(getattr(ke_transit, "detail", "")))
            # satu gudang NYATA + Transit tetap punya gudang bawaan
            await c.execute("UPDATE warehouses SET is_active = false WHERE tenant_id = $1 AND NOT is_system AND id <> $2", tid, asal["id"])
            await c.execute("UPDATE warehouses SET is_default = false WHERE tenant_id = $1 AND id = $2", tid, asal["id"])
            bw = await GBW.bawaan(c, tid)
            cek(f"C {tid} SATU gudang nyata + Transit: bawaan tetap = gudang itu (SO kirim/GRN/SJ tak memblok)", bw is not None and bw["id"] == asal["id"], f"{bw}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
