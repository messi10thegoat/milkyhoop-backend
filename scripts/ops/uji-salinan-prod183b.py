"""BUG-183b uji DB di SALINAN (bukan prod): handler ASLI production.py + bom.py atas klon, tenant pt-busana-indah-semesta (BS). Mutasi HANYA di klon.
A WO 50 normal: FG 2.615.000 (standar), WIP bersih 0 · B WO 10 bahan AKTUAL lebih (kain 11 vs 10; TESTER D3): FG 523.000 (bukan 563.000), WIP bersih WO = +40.000 (selisih bahan) · C /labor aktual LEBIH BESAR dari standar:
FG tetap standar (209.200), kelebihan tertinggal di WIP · D dua output (3 lalu 1): FG 209.200, WIP 0 · F WO BELUM selesai ber-WIP != 0 · E month-end dry_run: hanya WO SELESAI ber-WIP != 0 (B, C), bukan A/D (WIP 0) / F (belum selesai)
· G month-end NYATA di klon: WIP bersih B dan C = 0, 5-90200 menerima selisih, A/D/F tak disentuh, jalan KEDUA = 409 (idempoten), memo per WO."""

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
from app.services import produksi_wip_sisa as PW  # noqa: E402
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
        # A
        oa = await wo("50")
        await galat(P.report_output(rq, oa, SP.ProductionCompletionInput(good_quantity=Decimal("50"))))
        cek("A WO 50 normal: FG 2.615.000 (standar), WIP bersih 0", await fg_total(oa) == 2615000.0 and await wip_bersih(c, oa, wip) == 0.0, f"{await fg_total(oa)} {await wip_bersih(c, oa, wip)}")
        # B  D3: kain 11 (std 10), benang 0.2, label 10
        ob = await wo("10", kain="11", benang="0.2", label="10")
        await galat(P.report_output(rq, ob, SP.ProductionCompletionInput(good_quantity=Decimal("10"))))
        cek("B bahan aktual lebih (kain 11 vs 10): FG 523.000 (bukan 563.000)", await fg_total(ob) == 523000.0, str(await fg_total(ob)))
        cek("B WIP bersih WO = +40.000 (selisih kain 1 m)", await wip_bersih(c, ob, wip) == 40000.0, str(await wip_bersih(c, ob, wip)))
        # C /labor aktual lebih besar
        oc = await wo("4")
        await P.record_labor(rq, oc, SP.ProductionLaborInput(operation_id=op, operation_name="Overhead Jahit", actual_hours=Decimal("10")))
        await galat(P.report_output(rq, oc, SP.ProductionCompletionInput(good_quantity=Decimal("4"))))
        cek("C /labor aktual 200.000 > standar 8.000: FG tetap standar 209.200; kelebihan 192.000 di WIP", await fg_total(oc) == 209200.0 and await wip_bersih(c, oc, wip) == 192000.0, f"{await fg_total(oc)} {await wip_bersih(c, oc, wip)}")
        # D dua output
        od = await wo("4")
        await galat(P.report_output(rq, od, SP.ProductionCompletionInput(good_quantity=Decimal("3"))))
        await galat(P.report_output(rq, od, SP.ProductionCompletionInput(good_quantity=Decimal("1"))))
        cek("D dua output (3 lalu 1): FG 209.200 total, WIP bersih 0", await fg_total(od) == 209200.0 and await wip_bersih(c, od, wip) == 0.0, f"{await fg_total(od)} {await wip_bersih(c, od, wip)}")
        # F belum selesai
        of = await wo("4")
        await galat(P.report_output(rq, of, SP.ProductionCompletionInput(good_quantity=Decimal("2"))))
        wf = await wip_bersih(c, of, wip)
        cek("F WO BELUM selesai (2 dari 4) punya WIP bersih != 0 (WIP sah)", wf != 0.0, str(wf))
        # E dry_run
        periode = date.today().strftime("%Y-%m")
        me = (await P.month_end_reconcile(rq, {"period": periode, "dry_run": True}))["data"]
        daftar = {x["order_id"]: x["sisa"] for x in me.get("wip_sisa", [])}
        print(f"INFO dry_run wip_sisa={me.get('wip_sisa')} total={me.get('wip_sisa_total')}", flush=True)
        cek("E dry_run melaporkan sisa B (+40.000) dan C (+192.000)", daftar.get(str(ob)) in ("40000.00", "40000") and daftar.get(str(oc)) in ("192000.00", "192000"), str(daftar))
        cek("E dry_run TIDAK memuat A/D (WIP 0) maupun F (belum selesai)", str(oa) not in daftar and str(od) not in daftar and str(of) not in daftar, str(daftar))
        # G nyata
        sebelum_f = await wip_bersih(c, of, wip)
        nyata = await galat(P.month_end_reconcile(rq, {"period": periode}))
        print(f"INFO month-end NYATA: {str(nyata)[:400]}", flush=True)
        cek("G month-end nyata sukses", isinstance(nyata, dict) and nyata.get("success") is True, str(nyata)[:200])
        cek("G WIP bersih B dan C = 0 sesudah kuras", await wip_bersih(c, ob, wip) == 0.0 and await wip_bersih(c, oc, wip) == 0.0, f"{await wip_bersih(c, ob, wip)} {await wip_bersih(c, oc, wip)}")
        # WIP kuras tercatat di jurnal reconcile: hitung baris memo per WO
        memo = {str(r["memo"]): float(r["credit"] - r["debit"]) for r in await c.fetch(
            """SELECT jl.memo, jl.credit, jl.debit FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
               WHERE je.source_type = 'PRODUCTION_RECONCILE' AND jl.account_id = $2 AND jl.memo LIKE 'Kuras sisa WIP WO %'""", BS, wip)}
        cek("G baris kuras ber-memo per WO untuk B (40.000) dan C (192.000)", memo.get(PW.memo_kuras(await c.fetchval("SELECT order_number FROM production_orders WHERE id = $1 AND tenant_id = $2", ob, BS), ob)) == 40000.0
            and memo.get(PW.memo_kuras(await c.fetchval("SELECT order_number FROM production_orders WHERE id = $1 AND tenant_id = $2", oc, BS), oc)) == 192000.0, str(list(memo.values())))
        cek("G A, D (WIP 0) dan F (belum selesai) TAK disentuh", await wip_bersih(c, oa, wip) == 0.0 and await wip_bersih(c, od, wip) == 0.0 and await wip_bersih(c, of, wip) == sebelum_f
            and not any(str(o) in k for k in memo for o in (oa, od, of)), f"{await wip_bersih(c, of, wip)} vs {sebelum_f}")
        var_masuk = float(await c.fetchval(
            """SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
               WHERE je.source_type = 'PRODUCTION_RECONCILE' AND jl.account_id = $2 AND jl.memo LIKE 'Selisih bahan/konversi %'""", BS, var))
        cek("G 5-90200 menerima selisih bahan/konversi 232.000 (40.000 + 192.000)", var_masuk >= 232000.0, str(var_masuk))
        kedua = await galat(P.month_end_reconcile(rq, {"period": periode}))
        cek("G jalan KEDUA di periode yang sama ditolak 409 (idempoten)", isinstance(kedua, str) and kedua.startswith("HTTP 409"), str(kedua)[:160])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
