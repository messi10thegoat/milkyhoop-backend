"""BUG-184 uji DB di SALINAN (bukan prod): handler ASLI production.py + bom.py atas klon, tenant pt-busana-indah-semesta (BS). Mutasi HANYA di klon.
1 create WO TANPA unit -> production_orders.unit = output_unit BOM · 2 create WO DENGAN unit -> unit dari body · 3 WO dirilis: daftar + detail mengembalikan status mentah 'released', status_text 'Dirilis', siap_diselesaikan False
· 4 sesudah start + output PENUH: daftar + detail siap_diselesaikan True, status TETAP 'in_progress' (tak ada penutupan otomatis), status_text 'Sedang Proses' · 5 output sebagian: siap_diselesaikan False
· 6 sesudah POST /complete: status 'completed', status_text 'Selesai', siap_diselesaikan False."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bom as B  # noqa: E402
from app.routers import production as P  # noqa: E402
from app.schemas import bom as SB  # noqa: E402
from app.schemas import production as SP  # noqa: E402

BS = "pt-busana-indah-semesta"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', BS)
    r.state.user = {"tenant_id": BS, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def kode(c, item_code):
    return await c.fetchval("SELECT id FROM products WHERE tenant_id = $1 AND item_code = $2", BS, item_code)


async def gudang(c, pid):
    return await c.fetchval("SELECT warehouse_id FROM warehouse_stock WHERE tenant_id = $1 AND item_id = $2 ORDER BY quantity DESC LIMIT 1", BS, str(pid))


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return f"HTTP {e.status_code}: {e.detail}"


async def langkah(nama, coro):
    """Jalankan langkah penyiapan; galat TAMPIL (bukan ditelan) supaya kegagalan alat/logika terlihat."""
    r = await galat(coro)
    cek(f"siapan: {nama} berhasil", not (isinstance(r, HTTPException) or (isinstance(r, str) and r.startswith("HTTP "))), str(r)[:300])
    return r


async def daftar_item(rq, oid, nomor):
    out = await P.list_production_orders(rq, 0, 100, nomor, None, None, None, None, None, "order_date", "desc")
    return next((x for x in out["items"] if str(x["id"]) == str(oid)), None) if isinstance(out, dict) else None


async def detail(rq, oid):
    return (await P.get_production_order(rq, oid))["data"]


async def main():
    pool = await P.get_pool()
    async with pool.acquire() as c:
        rq = await req(c)
        ids = {"kaos": await kode(c, "BRG-0001"), "kain": await kode(c, "BHN-KAIN"), "benang": await kode(c, "BHN-BENANG"), "label": await kode(c, "BHN-LABEL")}
        cek("prasyarat: 4 barang busana ada", all(ids.values()), str(ids))
        comps = [SB.BOMComponentInput(component_product_id=ids["kain"], quantity=Decimal("1"), unit_cost=Decimal("40000")),
                 SB.BOMComponentInput(component_product_id=ids["benang"], quantity=Decimal("0.02"), unit_cost=Decimal("15000")),
                 SB.BOMComponentInput(component_product_id=ids["label"], quantity=Decimal("1"), unit_cost=Decimal("500"))]
        r = await B.create_bom(rq, SB.CreateBOMRequest(product_id=ids["kaos"], bom_code="UJI184-" + uuid.uuid4().hex[:6], output_quantity=Decimal("1"), output_unit="Pcs", components=comps))
        bid = uuid.UUID(str(r["data"]["id"]))
        await B.activate_bom(rq, bid)
        wh = await gudang(c, ids["kain"])

        async def wo(unit, qty="2"):
            body = SP.CreateProductionOrderRequest(product_id=ids["kaos"], bom_id=bid, planned_quantity=Decimal(qty), warehouse_id=wh, unit=unit)
            res = await P.create_production_order(rq, body)
            return uuid.UUID(str(res["data"]["id"]))

        async def unit_db(oid):
            return await c.fetchval("SELECT unit FROM production_orders WHERE id = $1 AND tenant_id = $2", oid, BS)
        o1 = await wo(None)
        cek("1 create WO TANPA unit -> unit = output_unit BOM", await unit_db(o1) == "Pcs", str(await unit_db(o1)))
        o1b = await wo("   ")
        cek("1b create WO unit spasi -> ikut BOM", await unit_db(o1b) == "Pcs", str(await unit_db(o1b)))
        o2 = await wo("Kodi-UJI")
        cek("2 create WO DENGAN unit -> unit dari body", await unit_db(o2) == "Kodi-UJI", str(await unit_db(o2)))
        # 1c BOM bersatuan NON-dasar ('Lusin-UJI'): satuan WO tetap NULL (perilaku lama) dan Penerimaan produksi TETAP lolos (mitigasi BUG-195)
        rl = await B.create_bom(rq, SB.CreateBOMRequest(product_id=ids["kaos"], bom_code="UJI184L-" + uuid.uuid4().hex[:6], output_quantity=Decimal("1"), output_unit="Lusin-UJI", components=comps))
        bl = uuid.UUID(str(rl["data"]["id"]))
        await B.activate_bom(rq, bl)
        bom_lama, bid = bid, bl
        ol = await wo("1")
        bid = bom_lama
        cek("1c BOM bersatuan 'Lusin-UJI' (non-dasar): satuan WO NULL (bukan 'Lusin-UJI')", await unit_db(ol) is None, str(await unit_db(ol)))
        await P.release_order(rq, ol)
        await langkah("issue_materials 1c", P.issue_materials(rq, ol, [SP.ProductionMaterialInput(product_id=ids["kain"], quantity=Decimal("1"), warehouse_id=wh),
                                                                       SP.ProductionMaterialInput(product_id=ids["benang"], quantity=Decimal("0.02"), warehouse_id=await gudang(c, ids["benang"])),
                                                                       SP.ProductionMaterialInput(product_id=ids["label"], quantity=Decimal("1"), warehouse_id=await gudang(c, ids["label"]))]))
        await langkah("start_production 1c", P.start_production(rq, ol))
        r1c = await langkah("report_output 1c (satuan NULL -> lolos)", P.report_output(rq, ol, SP.ProductionCompletionInput(good_quantity=Decimal("1"))))
        cek("1c Penerimaan produksi WO satuan NULL TETAP lolos", isinstance(r1c, dict) and r1c.get("success") is True, str(r1c)[:200])
        # 3 dirilis
        nomor = await c.fetchval("SELECT order_number FROM production_orders WHERE id = $1 AND tenant_id = $2", o1, BS)
        await P.release_order(rq, o1)
        li, de = await daftar_item(rq, o1, nomor), await detail(rq, o1)
        cek("3 daftar: status 'released', status_text 'Dirilis', siap False", li is not None and (li["status"], li["status_text"], li["siap_diselesaikan"]) == ("released", "Dirilis", False), str(li)[:240])
        cek("3 detail: status 'released', status_text 'Dirilis', siap False", (de["status"], de["status_text"], de["siap_diselesaikan"]) == ("released", "Dirilis", False), str({k: de.get(k) for k in ("status", "status_text", "siap_diselesaikan")}))
        # bahan + start
        await P.issue_materials(rq, o1, [SP.ProductionMaterialInput(product_id=ids["kain"], quantity=Decimal("2"), warehouse_id=wh),
                                         SP.ProductionMaterialInput(product_id=ids["benang"], quantity=Decimal("0.04"), warehouse_id=await gudang(c, ids["benang"])),
                                         SP.ProductionMaterialInput(product_id=ids["label"], quantity=Decimal("2"), warehouse_id=await gudang(c, ids["label"]))])
        await langkah("start_production", P.start_production(rq, o1))
        # 5 output sebagian
        await langkah("report_output 1", P.report_output(rq, o1, SP.ProductionCompletionInput(good_quantity=Decimal("1"))))
        li, de = await daftar_item(rq, o1, nomor), await detail(rq, o1)
        cek("5 output sebagian (1 dari 2): siap False, status in_progress, 'Sedang Proses'", (li["status"], li["status_text"], li["siap_diselesaikan"], de["siap_diselesaikan"]) == ("in_progress", "Sedang Proses", False, False), str(li)[:240])
        # 4 output penuh
        await langkah("report_output 2 (penuh)", P.report_output(rq, o1, SP.ProductionCompletionInput(good_quantity=Decimal("1"))))
        li, de = await daftar_item(rq, o1, nomor), await detail(rq, o1)
        cek("4 output penuh: siap_diselesaikan True di daftar DAN detail", li["siap_diselesaikan"] is True and de["siap_diselesaikan"] is True, str(li)[:240])
        cek("4 status TETAP in_progress / 'Sedang Proses' (tak ada penutupan otomatis)", (li["status"], li["status_text"], de["status"], de["status_text"]) == ("in_progress", "Sedang Proses", "in_progress", "Sedang Proses"),
            f"{li['status']} {de['status']}")
        # 6 complete
        selesai = await galat(P.complete_order(rq, o1))
        li, de = await daftar_item(rq, o1, nomor), await detail(rq, o1)
        cek("6 sesudah /complete: 'completed', 'Selesai', siap False", isinstance(selesai, dict) and (li["status"], li["status_text"], li["siap_diselesaikan"], de["siap_diselesaikan"]) == ("completed", "Selesai", False, False), f"{selesai} {li}"[:300])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
