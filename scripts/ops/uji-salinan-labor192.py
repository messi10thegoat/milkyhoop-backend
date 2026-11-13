"""BUG-192 uji DB di SALINAN (bukan prod): handler ASLI production.py (/labor) + bom.py atas klon, tenant pt-busana-indah-semesta. Mutasi HANYA di klon.
A WO tanpa work center, /labor 10 jam TANPA operation_id (kasus TESTER D8): tarif_ditemukan False, peringatan Indonesia, biaya diterapkan 0, NOL jurnal labor/overhead (perilaku terap tak berubah), audit_input_cost tetap
· B /labor dgn operation_id bertarif (Overhead Jahit): tarif_ditemukan True, peringatan None, biaya > 0 diterapkan + jurnal terbit · C medan lama tetap ada (rate_source, labor_rate_std, audit_input_cost)."""

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bom as B  # noqa: E402
from app.routers import production as P  # noqa: E402
from app.schemas import bom as SB  # noqa: E402
from app.schemas import production as SP  # noqa: E402
from app.services.role_resolver import AccountRole, resolve_account_id_by_role  # noqa: E402

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


async def jurnal(c, order_id, tipe):
    return [float(r["total_debit"]) for r in await c.fetch(
        "SELECT total_debit FROM journal_entries WHERE tenant_id = $1 AND source_id = $2 AND source_type = $3 AND status = 'POSTED' ORDER BY created_at", BS, order_id, tipe)]


async def wip_bersih(c, order_id, wip):
    """WIP bersih WO = baris WIP jurnal ber-source_id WO + baris KURAS month-end (jurnal PRODUCTION_RECONCILE, memo ditandai [id WO])."""
    return float(await c.fetchval(
        """SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
                  + COALESCE((SELECT SUM(jl2.debit - jl2.credit) FROM journal_lines jl2 JOIN journal_entries je2 ON je2.id = jl2.journal_id AND je2.tenant_id = $1
                              WHERE je2.source_type = 'PRODUCTION_RECONCILE' AND je2.status = 'POSTED' AND jl2.account_id = $3 AND jl2.memo LIKE '%[' || $4::text || ']'), 0)
           FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
           WHERE je.source_id = $2 AND jl.account_id = $3 AND je.status = 'POSTED'""", BS, order_id, wip, str(order_id)))


async def bom_aktif(c, rq, ids):
    ops = [SB.BOMOperationInput(operation_number=1, operation_name="Potong", labor_mode="piece_rate", cost_per_piece=Decimal("2000")),
           SB.BOMOperationInput(operation_number=2, operation_name="Jahit", labor_mode="piece_rate", cost_per_piece=Decimal("6000")),
           SB.BOMOperationInput(operation_number=3, operation_name="Finishing", labor_mode="piece_rate", cost_per_piece=Decimal("1500")),
           SB.BOMOperationInput(operation_number=4, operation_name="Overhead Jahit", labor_mode="time_based", run_time_minutes=6, overhead_rate_per_hour=Decimal("20000"))]
    comps = [SB.BOMComponentInput(component_product_id=ids["kain"], quantity=Decimal("1"), unit_cost=Decimal("40000")),
             SB.BOMComponentInput(component_product_id=ids["benang"], quantity=Decimal("0.02"), unit_cost=Decimal("15000")),
             SB.BOMComponentInput(component_product_id=ids["label"], quantity=Decimal("1"), unit_cost=Decimal("500"))]
    r = await B.create_bom(rq, SB.CreateBOMRequest(product_id=ids["kaos"], bom_code="UJI183-" + uuid.uuid4().hex[:6], output_quantity=Decimal("1"), components=comps, operations=ops))
    bid = uuid.UUID(str(r["data"]["id"]))
    await B.activate_bom(rq, bid)
    op = await c.fetchval("SELECT o.id FROM bom_operations o JOIN bill_of_materials b ON b.id = o.bom_id AND b.tenant_id = $2 WHERE o.bom_id = $1 AND o.operation_name = 'Overhead Jahit'", bid, BS)
    return bid, op


async def gudang(c, pid):
    return await c.fetchval("SELECT warehouse_id FROM warehouse_stock WHERE tenant_id = $1 AND item_id = $2 ORDER BY quantity DESC LIMIT 1", BS, str(pid))


async def buat_wo(c, rq, ids, bid, qty):
    wh = await gudang(c, ids["kain"])
    r = await P.create_production_order(rq, SP.CreateProductionOrderRequest(product_id=ids["kaos"], bom_id=bid, planned_quantity=Decimal(qty), warehouse_id=wh))
    oid = uuid.UUID(str(r["data"]["id"]))
    await P.release_order(rq, oid)
    await P.issue_materials(rq, oid, [SP.ProductionMaterialInput(product_id=ids["kain"], quantity=Decimal(qty), warehouse_id=await gudang(c, ids["kain"])),
                                      SP.ProductionMaterialInput(product_id=ids["benang"], quantity=Decimal(qty) * Decimal("0.02"), warehouse_id=await gudang(c, ids["benang"])),
                                      SP.ProductionMaterialInput(product_id=ids["label"], quantity=Decimal(qty), warehouse_id=await gudang(c, ids["label"]))])
    return oid


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return f"HTTP {e.status_code}: {e.detail}"


async def main():
    pool = await P.get_pool()
    async with pool.acquire() as c:
        rq = await req(c)
        ids = {"kaos": await kode(c, "BRG-0001"), "kain": await kode(c, "BHN-KAIN"), "benang": await kode(c, "BHN-BENANG"), "label": await kode(c, "BHN-LABEL")}
        cek("prasyarat: 4 barang busana ada", all(ids.values()), str(ids))
        wip = await resolve_account_id_by_role(c, BS, AccountRole.WIP_GENERIC)
        var = await resolve_account_id_by_role(c, BS, AccountRole.COGS_VARIANCE_PRODUCTION)
        bid, op = await bom_aktif(c, rq, ids)
        for kunci, tambah in (("kain", "80"), ("benang", "4"), ("label", "80")):
            await c.execute("UPDATE warehouse_stock SET quantity = quantity + $4 WHERE tenant_id = $1 AND item_id = $2 AND warehouse_id = $3", BS, str(ids[kunci]), await gudang(c, ids[kunci]), Decimal(tambah))

        async def wo(qty, kain=None, benang=None, label=None):
            wh = await gudang(c, ids["kain"])
            r = await P.create_production_order(rq, SP.CreateProductionOrderRequest(product_id=ids["kaos"], bom_id=bid, planned_quantity=Decimal(qty), warehouse_id=wh))
            oid = uuid.UUID(str(r["data"]["id"]))
            await P.release_order(rq, oid)
            q = Decimal(qty)
            await P.issue_materials(rq, oid, [SP.ProductionMaterialInput(product_id=ids["kain"], quantity=Decimal(kain or q), warehouse_id=await gudang(c, ids["kain"])),
                                              SP.ProductionMaterialInput(product_id=ids["benang"], quantity=Decimal(benang or q * Decimal("0.02")), warehouse_id=await gudang(c, ids["benang"])),
                                              SP.ProductionMaterialInput(product_id=ids["label"], quantity=Decimal(label or q), warehouse_id=await gudang(c, ids["label"]))])
            return oid

        async def fg_total(oid):
            return float(await c.fetchval("SELECT COALESCE(SUM(pc.total_cost), 0) FROM production_completions pc JOIN production_orders po ON po.id = pc.production_order_id AND po.tenant_id = $2 WHERE pc.production_order_id = $1", oid, BS))
        async def jml_jurnal(oid):
            return await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_id = $2 AND source_type IN ('PRODUCTION_LABOR', 'PRODUCTION_OVERHEAD') AND status = 'POSTED'", BS, oid)

        # A kasus TESTER: tanpa operation_id, WO tanpa work center
        oa = await wo("5")
        ra = await galat(P.record_labor(rq, oa, SP.ProductionLaborInput(operation_name="Jahit", actual_hours=Decimal("10"), hourly_rate=20000)))
        da = ra.get("data") if isinstance(ra, dict) else None
        cek("A /labor tanpa operasi: sukses, tarif_ditemukan False, peringatan Indonesia", isinstance(da, dict) and da.get("tarif_ditemukan") is False and (da.get("peringatan") or "").startswith("Tarif standar 0"), str(ra)[:300])
        cek("A biaya diterapkan 0, audit_input_cost 200000 (perilaku terap tak berubah), NOL jurnal labor/overhead", isinstance(da, dict) and da.get("labor_cost_applied") == "0.00" and da.get("overhead_cost_applied") == "0.00"
            and da.get("audit_input_cost") == 200000 and await jml_jurnal(oa) == 0, f"{da} jurnal={await jml_jurnal(oa)}")
        # B dengan operation_id bertarif
        ob = await wo("5")
        rb = await galat(P.record_labor(rq, ob, SP.ProductionLaborInput(operation_id=op, operation_name="Overhead Jahit", actual_hours=Decimal("10"), hourly_rate=20000)))
        db = rb.get("data") if isinstance(rb, dict) else None
        cek("B /labor dgn operasi bertarif: tarif_ditemukan True, peringatan None", isinstance(db, dict) and db.get("tarif_ditemukan") is True and db.get("peringatan") is None, str(rb)[:300])
        cek("B biaya > 0 diterapkan + jurnal terbit", isinstance(db, dict) and (Decimal(db["labor_cost_applied"]) + Decimal(db["overhead_cost_applied"])) > 0 and await jml_jurnal(ob) >= 1, f"{db} jurnal={await jml_jurnal(ob)}")
        # C medan lama
        cek("C medan lama tetap ada (rate_source, labor_rate_std, overhead_rate_std, audit_input_cost)", all(isinstance(d, dict) and all(k in d for k in ("rate_source", "labor_rate_std", "overhead_rate_std", "audit_input_cost")) for d in (da, db)))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
