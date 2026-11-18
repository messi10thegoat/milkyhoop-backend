"""BUG-123 (18 Nov 2026) -- uji di SALINAN: Penyesuaian Stok bersatuan besar (server mengonversi ke satuan dasar; satuan tanpa konversi = blok berkalimat + field; pratinjau/draf
memuat baris terkonversi + teks campur; posting menulis kartu stok dalam satuan dasar). Handler ASLI. Tulis HANYA di salinan (nama DB dicek)."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_adjustments as SA  # noqa: E402
from app.schemas.stock_adjustments import CreateStockAdjustmentRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])
    print(("[ok] " if ok else "[GAGAL] ") + nama + ("" if ok else " :: " + str(info)[:300]), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-123"}
    return r


async def satu(conn, tid):
    r = await conn.fetchrow(
        """SELECT p.id AS pid, p.base_unit AS dasar, il.warehouse_id AS wid
           FROM products p JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = p.tenant_id
           JOIN warehouses w ON w.id = il.warehouse_id AND w.tenant_id = p.tenant_id
           WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND il.warehouse_id IS NOT NULL AND NOT w.is_system AND p.deleted_at IS NULL
           GROUP BY p.id, p.base_unit, il.warehouse_id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) >= 30 ORDER BY p.id LIMIT 1""", tid)
    if not r:
        print(f"[info] {tid}: tak ada barang goods berstok >= 30 -> dilewati", flush=True)
        return
    pid, wid, dasar = r["pid"], r["wid"], r["dasar"] or "Pcs"
    await conn.execute("DELETE FROM unit_conversions WHERE tenant_id = $1 AND product_id = $2 AND conversion_unit = 'UJI-Dus'", tid, pid)
    await conn.execute("INSERT INTO unit_conversions (id, tenant_id, product_id, base_unit, conversion_unit, conversion_factor, is_active) VALUES ($1, $2, $3, $4, 'UJI-Dus', 12, true)",
                       uuid.uuid4(), tid, pid, dasar)
    rq = req(tid)

    def badan(items, tipe="increase"):
        return CreateStockAdjustmentRequest(adjustment_date=str(date.today()), adjustment_type=tipe, warehouse_id=str(wid), notes="UJI 123", items=items)

    stok = lambda: conn.fetchval("SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, pid, wid)
    sebelum = await stok()

    # pratinjau: 2 UJI-Dus = 24 satuan dasar; teks campur; input dicatat
    pv = (await SA.preview_create_stock_adjustment(rq, badan([{"product_id": str(pid), "quantity_adjustment": 2, "unit": "UJI-Dus"}])))["data"]
    b0 = (pv.get("baris_draf") or [{}])[0]
    cek(f"{tid} pratinjau: ok + 2 UJI-Dus = 24 {dasar}", pv["ok"] and D(b0.get("quantity_adjustment", "0")) == 24 and b0.get("unit") == dasar and b0.get("unit_input") == "UJI-Dus" and b0.get("quantity_input") in ("2", "2.0"), pv)
    cek(f"{tid} pratinjau: teks campur sesudah memuat UJI-Dus", "UJI-Dus" in (b0.get("quantity_after_campur_text") or ""), b0)

    # satuan tanpa konversi -> blok berkalimat + field (pratinjau) dan 400 berkode (tulis)
    pv2 = (await SA.preview_create_stock_adjustment(rq, badan([{"product_id": str(pid), "quantity_adjustment": 1, "unit": "UJI-Tak-Ada"}])))["data"]
    cek(f"{tid} satuan tanpa konversi: SATUAN_TAK_TERKONVERSI + field + kalimat", not pv2["ok"] and pv2["blocks"][0]["code"] == "SATUAN_TAK_TERKONVERSI" and pv2["blocks"][0].get("field") == "items[0].unit"
        and "UJI-Tak-Ada" in pv2["blocks"][0]["message"], pv2)
    try:
        await SA.create_stock_adjustment(rq, badan([{"product_id": str(pid), "quantity_adjustment": 1, "unit": "UJI-Tak-Ada"}]))
        cek(f"{tid} tulis satuan tanpa konversi ditolak", False, "lolos")
    except HTTPException as e:
        cek(f"{tid} tulis satuan tanpa konversi ditolak 400 kode sama", e.status_code == 400, (e.status_code, e.detail))

    # tulis + posting: kartu stok dalam satuan dasar
    c = await SA.create_stock_adjustment(rq, badan([{"product_id": str(pid), "quantity_adjustment": 2, "unit": "UJI-Dus"}]))
    sa_id = uuid.UUID(c["data"]["id"])
    row = await conn.fetchrow("SELECT i.quantity_adjustment, i.unit FROM stock_adjustment_items i JOIN stock_adjustments s ON s.id = i.stock_adjustment_id AND s.tenant_id = $2 WHERE i.stock_adjustment_id = $1", sa_id, tid)
    cek(f"{tid} draf tersimpan dalam satuan dasar (24 {dasar})", D(row["quantity_adjustment"]) == 24 and row["unit"] == dasar, dict(row))
    await SA.post_stock_adjustment(rq, sa_id)
    sesudah = await stok()
    cek(f"{tid} posting: stok gudang +24 satuan dasar", D(sesudah) - D(sebelum) == 24, (sebelum, sesudah))

    # hitung ulang dalam satuan besar: fisik = (stok sekarang / 12 dibulatkan bawah) UJI-Dus
    fisik = int(D(sesudah) // 12)
    pv3 = (await SA.preview_create_stock_adjustment(rq, badan([{"product_id": str(pid), "quantity_adjustment": 0, "physical_quantity": fisik, "unit": "UJI-Dus"}], "recount")))["data"]
    ada = pv3.get("baris_draf") or []
    cek(f"{tid} hitung ulang bersatuan: fisik {fisik} UJI-Dus = {fisik * 12} {dasar}", (pv3["ok"] and ada and D(ada[0]["quantity_after"]) == fisik * 12) or (not pv3["ok"] and pv3["blocks"][0]["code"] == "PENYESUAIAN_SELISIH_NOL"), pv3)


async def utama():
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_s123_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            await satu(conn, tid)
    json.dump({"CEK": CEK}, open(os.environ["KELUAR"], "w"), indent=1)
    gagal = [c for c in CEK if not c[1]]
    print(f"[info] {len(CEK)} cek, {len(gagal)} gagal", flush=True)
    sys.exit(1 if gagal or not CEK else 0)

asyncio.run(utama())
