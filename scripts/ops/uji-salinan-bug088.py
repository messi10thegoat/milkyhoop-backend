"""BUG-088 -- uji di SALINAN (klon milkydb): paritas pajak masukan PO /calculate == PO tersimpan (create) == /to-bill/preview == /bills/calculate
untuk PPN 12% (DPP 11/12) dan 11% (DPP penuh), mode pajak BARIS dan HEADER, tenant kaos + nyawit. PO UJI ditulis HANYA ke salinan."""
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bills as RB, purchase_orders as RO  # noqa: E402
from app.schemas.bills import CalculateBillRequestV2  # noqa: E402
from app.schemas.purchase_orders import CreatePurchaseOrderRequest  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
HARAP = {12: D("211200.00"), 11: D("211200.00")}


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/api/purchase-orders", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug088"}
    return r


async def periksa(tid):
    pool = await RO.get_pool()
    async with pool.acquire() as conn:
        vendor = await conn.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 AND COALESCE(is_active, true) ORDER BY created_at LIMIT 1", tid)
        kode = {int(r["rate"]): r["id"] for r in await conn.fetch(
            "SELECT id, rate FROM tax_codes WHERE tenant_id = $1 AND is_active AND direction = 'input' AND tax_type = 'ppn' AND rate IN (11, 12)", tid)}
        hari = date.today()
    for tarif in (12, 11):
        for mode in ("baris", "header"):
            tb, th = (tarif, 0) if mode == "baris" else (0, tarif)
            badan = CreatePurchaseOrderRequest(vendor_id=str(vendor["id"]), vendor_name=vendor["name"], po_date=hari, tax_rate=th,
                                               items=[{"description": f"UJI BUG088 {tarif} {mode}", "quantity": D("10"), "unit": "Karton",
                                                       "unit_price": D("192000"), "tax_rate": tb}])
            hit = (await RO.calculate_purchase_order(req(tid), badan))["data"]
            buat = await RO.create_purchase_order(req(tid), badan)
            pid = uuid.UUID(str((buat["data"] if isinstance(buat, dict) else buat.data)["id"]))
            async with pool.acquire() as conn:
                simpan = await conn.fetchval("SELECT tax_amount FROM purchase_orders WHERE id = $1 AND tenant_id = $2", pid, tid)
            tag = (await RO.preview_convert_to_bill(req(tid), pid, None))["data"]
            item = {"product_name": "UJI BUG088", "qty": D("10"), "price": 192000.0}
            if mode == "baris":
                item.update(tax_code_id=kode[tarif], tax_rate=tarif)
            bb = CalculateBillRequestV2(vendor_id=vendor["id"], due_date=hari + timedelta(days=30), items=[item],
                                        tax_rate=th, tax_code_id=kode[tarif] if mode == "header" else None)
            bill = (await RB.calculate_bill_totals(req(tid), bb))["calculation"]
            angka = (D(str(hit["tax_amount"])), D(str(simpan)), D(str(tag["bill_tax"])), D(str(bill["tax_amount"])))
            cek(f"{tid} PPN {tarif}% {mode}: PO calculate == PO tersimpan == to-bill == bill calculate == {HARAP[tarif]}",
                len(set(angka)) == 1 and angka[0] == HARAP[tarif], " / ".join(str(x) for x in angka) + f" blok to-bill={[b['code'] for b in tag.get('blocks', [])]}")


async def utama():
    pool = await RO.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
    if not str(db).startswith("scratch_b088"):  # PENJAGA: tulis PO UJI hanya di salinan
        print(f"FAIL bukan salinan: {db} -- berhenti sebelum menulis", flush=True)
        return 3
    print(f"[info] basis data = {db}", flush=True)
    for t in TENANT:
        await periksa(t)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
