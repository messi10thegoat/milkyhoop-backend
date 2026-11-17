"""BE-B (17 Nov 2026) -- uji di SALINAN: kartu stok satuan campuran (BUG-108), isyarat stok PER GUDANG (BUG-075), riwayat Penyesuaian tanpa kata teknis (BUG-221).
SQL baru dijalankan di DB asli (tes unit memakai koneksi palsu). Tulis HANYA di salinan (nama DB dicek)."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT, stock_adjustments as SA  # noqa: E402
from app.services import stok_baris as SB  # noqa: E402
from app.services import stok_sesuai as SS  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])
    print(("[ok] " if ok else "[GAGAL] ") + nama + ("" if ok else " :: " + str(info)[:300]), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-be-b"}
    return r


async def satu(conn, tid):
    r = await conn.fetchrow(
        """SELECT p.id AS pid, p.base_unit AS dasar, il.warehouse_id AS wid, w.name AS wnama
           FROM products p JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = p.tenant_id
           JOIN warehouses w ON w.id = il.warehouse_id AND w.tenant_id = p.tenant_id
           WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND il.warehouse_id IS NOT NULL AND NOT w.is_system
           GROUP BY p.id, p.base_unit, il.warehouse_id, w.name HAVING SUM(il.quantity_in) - SUM(il.quantity_out) >= 30 ORDER BY p.id LIMIT 1""", tid)
    if not r:
        print(f"[info] {tid}: tak ada barang goods berstok >= 30 -> dilewati", flush=True)
        return
    pid, wid, wnama, dasar = r["pid"], r["wid"], r["wnama"], r["dasar"] or "Pcs"
    await conn.execute(
        "INSERT INTO unit_conversions (id, tenant_id, product_id, base_unit, conversion_unit, conversion_factor, is_active) VALUES ($1, $2, $3, $4, 'UJI-Dus', 12, true)",
        uuid.uuid4(), tid, pid, dasar)

    # 108: kartu stok memuat *_campur_text
    h = await IT.get_item_history(req(tid), pid, page=1, limit=20, warehouse_id=None, date_from=None, date_to=None)
    ada = [m for m in h["data"] if m.get("quantity_balance_campur_text")]
    cek(f"{tid} 108 kartu stok: medan campur ada + ada baris bersaldo besar yang tertulis campur", "saldo_awal_campur_text" in h and len(ada) > 0
        and all(any(ch.isdigit() for ch in m["quantity_balance_campur_text"]) for m in ada), (h["data"][:1], h.get("saldo_awal_campur_text")))
    cek(f"{tid} 108 teks lama tetap (quantity_balance_text)", all(m.get("quantity_balance_text") for m in h["data"]))

    # 075: isyarat per gudang -- gudang baris menang, kalimat menyebut gudang; tanggal mundur jauh -> stok per tanggal 0
    mundur = date.today() - timedelta(days=3650)
    iy = await SB.isyarat(conn, tid, mundur, date.today(), [{"item_id": pid, "quantity": 1, "unit": None, "warehouse_id": str(wid)}])
    cek(f"{tid} 075 isyarat gudang baris: kalimat menyebut '{wnama}'", len(iy) == 1 and f" di {wnama} per " in iy[0]["kalimat"], iy)
    iy2 = await SB.isyarat(conn, tid, mundur, date.today(), [{"item_id": pid, "quantity": 1, "unit": None}], str(wid))
    cek(f"{tid} 075 isyarat gudang kepala sama dgn baris", iy2 == iy, (iy, iy2))
    iy3 = await SB.isyarat(conn, tid, mundur, date.today(), [{"item_id": pid, "quantity": 1, "unit": None}])
    cek(f"{tid} 075 tanpa gudang: kalimat lama tanpa nama gudang", len(iy3) == 1 and " di " not in iy3[0]["kalimat"].split(" per ")[0], iy3)

    # 221: riwayat Penyesuaian -- buat + posting + batal lewat penulis, lalu baca riwayat
    from app.schemas.stock_adjustments import CreateStockAdjustmentRequest, VoidStockAdjustmentRequest
    body = CreateStockAdjustmentRequest(adjustment_date=str(date.today()), adjustment_type="decrease", warehouse_id=str(wid), notes="UJI be-b",
                                        items=[{"product_id": str(pid), "quantity_adjustment": -1}])
    rq = req(tid)
    c = await SA.create_stock_adjustment(rq, body)
    sa_id = uuid.UUID(c["data"]["id"]) if "data" in c and "id" in c["data"] else None
    if sa_id:
        await SA.post_stock_adjustment(rq, sa_id)
        await SA.void_stock_adjustment(rq, sa_id, VoidStockAdjustmentRequest(reason="UJI"))
        rw = await SS.riwayat(conn, tid, sa_id)
        teks = " | ".join(e.get("ringkas") or e.get("summary") or str(e) for e in rw["events"])
        cek(f"{tid} 221 riwayat SA: 'diterbitkan' + 'dibatalkan', tanpa 'diposting'/'(void)'", "diterbitkan" in teks and "dibatalkan" in teks and "diposting" not in teks and "(void)" not in teks, teks)
    else:
        cek(f"{tid} 221 buat SA", False, c)


async def utama():
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_beb_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            await satu(conn, tid)
    json.dump({"CEK": CEK}, open(os.environ["KELUAR"], "w"), indent=1)
    gagal = [c for c in CEK if not c[1]]
    print(f"[info] {len(CEK)} cek, {len(gagal)} gagal", flush=True)
    sys.exit(1 if gagal or not CEK else 0)

asyncio.run(utama())
