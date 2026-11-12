"""BUG-183 uji DB di SALINAN (bukan prod): handler ASLI production.py + bom.py atas klon, tenant pt-busana-indah-semesta (BS). Mutasi HANYA di klon.
A WO 50 kaos (BOM: bahan 40.800 · upah borongan 9.500 · overhead per jam 2.000 per unit): FG 2.615.000 (52.300/unit), jurnal PRODUCTION_LABOR 475.000 + PRODUCTION_OVERHEAD 100.000, WIP bersih WO = 0
B /labor SEBAGIAN dulu (overhead 1 jam = 20.000): standar menutup KEKURANGAN saja -> overhead total 100.000 (80.000 + 20.000, tak dobel), upah 475.000 · C dua output (30 lalu 20): upah 285.000 + 190.000 = 475.000, overhead 60.000 + 40.000
= 100.000, FG total 2.615.000 (output kedua tak mengulang) · D /labor aktual LEBIH BESAR dari standar (10 jam = 200.000): aktual menang, standar overhead 0, FG = bahan + upah + 200.000
E month-end-reconcile dry_run berjalan dan melaporkan selisih."""
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
    return float(await c.fetchval(
        """SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
           WHERE je.source_id = $2 AND jl.account_id = $3 AND je.status = 'POSTED'""", BS, order_id, wip))


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


async def buat_wo(c, rq, ids, bid, qty):
    r = await P.create_production_order(rq, SP.CreateProductionOrderRequest(product_id=ids["kaos"], bom_id=bid, planned_quantity=Decimal(qty)))
    oid = uuid.UUID(str(r["data"]["id"]))
    await P.release_order(rq, oid)
    await P.issue_materials(rq, oid, [SP.ProductionMaterialInput(product_id=ids["kain"], quantity=Decimal(qty)),
                                      SP.ProductionMaterialInput(product_id=ids["benang"], quantity=Decimal(qty) * Decimal("0.02")),
                                      SP.ProductionMaterialInput(product_id=ids["label"], quantity=Decimal(qty))])
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
        bid, op = await bom_aktif(c, rq, ids)
        bx = await c.fetchrow("SELECT standard_cost, labor_cost, overhead_cost, total_cost FROM bill_of_materials WHERE id = $1 AND tenant_id = $2", bid, BS)
        cek("BOM uji: 40.800 · 9.500 · 2.000 · 52.300", tuple(float(v) for v in bx) == (40800.0, 9500.0, 2000.0, 52300.0), str(dict(bx)))
        # A
        oa = await buat_wo(c, rq, ids, bid, "50")
        ra = await galat(P.report_output(rq, oa, SP.ProductionCompletionInput(good_quantity=Decimal("50"))))
        fg = await c.fetchrow("SELECT pc.unit_cost, pc.total_cost FROM production_completions pc JOIN production_orders po ON po.id = pc.production_order_id AND po.tenant_id = $2 WHERE pc.production_order_id = $1", oa, BS)
        cek("A WO 50: output berhasil", isinstance(ra, dict), str(ra)[:200])
        cek("A FG 52.300/unit, total 2.615.000", fg is not None and (float(fg["unit_cost"]), float(fg["total_cost"])) == (52300.0, 2615000.0), str(dict(fg)) if fg else "")
        cek("A jurnal PRODUCTION_LABOR 475.000 + PRODUCTION_OVERHEAD 100.000", await jurnal(c, oa, "PRODUCTION_LABOR") == [475000.0] and await jurnal(c, oa, "PRODUCTION_OVERHEAD") == [100000.0],
            f"{await jurnal(c, oa, 'PRODUCTION_LABOR')} {await jurnal(c, oa, 'PRODUCTION_OVERHEAD')}")
        cek("A WIP bersih WO = 0", await wip_bersih(c, oa, wip) == 0.0, str(await wip_bersih(c, oa, wip)))
        # B /labor sebagian
        ob = await buat_wo(c, rq, ids, bid, "4")
        await P.record_labor(rq, ob, SP.ProductionLaborInput(operation_id=op, operation_name="Overhead Jahit", actual_hours=Decimal("1")))
        await galat(P.report_output(rq, ob, SP.ProductionCompletionInput(good_quantity=Decimal("4"))))
        ohb = await jurnal(c, ob, "PRODUCTION_OVERHEAD")
        cek("B /labor sebagian 1 jam lalu output 4: overhead total 8.000 (tak dobel; std 4 x 2.000)", sum(ohb) == 8000.0 and len(ohb) == 2, str(ohb))
        cek("B upah standar 4 x 9.500 = 38.000", sum(await jurnal(c, ob, "PRODUCTION_LABOR")) == 38000.0, str(await jurnal(c, ob, "PRODUCTION_LABOR")))
        # C dua output
        oc = await buat_wo(c, rq, ids, bid, "4")
        await galat(P.report_output(rq, oc, SP.ProductionCompletionInput(good_quantity=Decimal("3"))))
        await galat(P.report_output(rq, oc, SP.ProductionCompletionInput(good_quantity=Decimal("1"))))
        lc, hc = await jurnal(c, oc, "PRODUCTION_LABOR"), await jurnal(c, oc, "PRODUCTION_OVERHEAD")
        cek("C dua output (3 lalu 1): upah 28.500 + 9.500 = 38.000; overhead 6.000 + 2.000 = 8.000", lc == [28500.0, 9500.0] and hc == [6000.0, 2000.0], f"{lc} {hc}")
        cek("C FG total 4 x 52.300 = 209.200 (output kedua tak mengulang)", float(await c.fetchval("SELECT COALESCE(SUM(pc.total_cost), 0) FROM production_completions pc JOIN production_orders po ON po.id = pc.production_order_id AND po.tenant_id = $2 WHERE pc.production_order_id = $1", oc, BS)) == 209200.0)
        # D aktual lebih besar
        od = await buat_wo(c, rq, ids, bid, "4")
        await P.record_labor(rq, od, SP.ProductionLaborInput(operation_id=op, operation_name="Overhead Jahit", actual_hours=Decimal("10")))
        await galat(P.report_output(rq, od, SP.ProductionCompletionInput(good_quantity=Decimal("4"))))
        ohd = await jurnal(c, od, "PRODUCTION_OVERHEAD")
        cek("D /labor aktual 10 jam (200.000) > standar 8.000: standar overhead 0, total 200.000, FG = 163.200 + 38.000 + 200.000", ohd == [200000.0]
            and float(await c.fetchval("SELECT COALESCE(SUM(pc.total_cost), 0) FROM production_completions pc JOIN production_orders po ON po.id = pc.production_order_id AND po.tenant_id = $2 WHERE pc.production_order_id = $1", od, BS)) in (401200.0, 401199.0, 401200.0), f"{ohd}")
        # E month-end dry_run
        try:
            me = await P.month_end_reconcile(rq, {"period": date.today().strftime("%Y-%m"), "dry_run": True})
            print(f"INFO month-end dry_run: {str(me)[:600]}", flush=True)
            cek("E month-end-reconcile dry_run berjalan (sukses)", isinstance(me, dict) and me.get("success") is True)
            # E2 rekonsiliasi akhir bulan NYATA (di klon): akun applied terkuras, WIP tiap WO uji tetap 0
            ap = {}
            for peran in (AccountRole.MFG_LABOR_APPLIED, AccountRole.MFG_OVERHEAD_APPLIED):
                ap[peran] = await resolve_account_id_by_role(c, BS, peran)

            async def saldo_applied():
                return {str(p): float(await c.fetchval(
                    """SELECT COALESCE(SUM(jl.credit - jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
                       WHERE jl.account_id = $2 AND je.status = 'POSTED'""", BS, a)) for p, a in ap.items()}
            sebelum = await saldo_applied()
            nyata = await galat(P.month_end_reconcile(rq, {"period": date.today().strftime("%Y-%m")}))
            sesudah = await saldo_applied()
            print(f"INFO month-end NYATA di klon: {str(nyata)[:500]} | applied sebelum={sebelum} sesudah={sesudah}", flush=True)
            cek("E2 month-end nyata: akun applied (2-10430/2-10440) terkuras = 0", isinstance(nyata, dict) and all(v == 0.0 for v in sesudah.values()), f"{sebelum} -> {sesudah} {str(nyata)[:200]}")
            cek("E2 WIP bersih tiap WO uji tetap 0 sesudah rekonsiliasi", all(await wip_bersih(c, o, wip) == 0.0 for o in (oa, ob, oc, od)))
        except Exception as e:  # noqa: BLE001
            cek("E month-end-reconcile dry_run berjalan", False, f"{type(e).__name__}: {e}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
