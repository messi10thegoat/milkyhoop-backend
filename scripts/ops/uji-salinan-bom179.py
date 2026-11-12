"""BUG-179 uji DB di SALINAN (bukan prod): handler ASLI routers/bom.py atas klon milkydb. Tenant pt-busana-indah-semesta (BS) + kaos-biru-konveksi (KA).
S1 skenario Accurate: kain 1x40.000 + benang 0,02x15.000 + label 1x500; borongan 2.000/6.000/1.500; Jahit 6 mnt x overhead WC 20.000/jam, tarif TAK dikirim -> total 52.300
 (standar 40.800 · labor 9.500 · overhead 2.000) dari baris bill_of_materials · S2 tarif eksplisit > 0 dipertahankan (5.000 bukan 20.000) · S3 BOM lama (tarif 0 tersimpan, rekaman bug):
pratinjau recalculate = tarif lama 0 -> baru 20.000, biaya sebelum 50.300 -> sesudah 52.300, NOL tulis; recalculate draf -> 52.300 · S4 BOM AKTIF: pratinjau bisa_segarkan false, recalculate TAK mengubah tarif/biaya
· S5 LINTAS TENANT: recalculate + pratinjau BOM tenant lain -> 404; BOM KA memakai id Work Center BS (buat + ubah) -> tarif TIDAK terbaca (0), tarif WC BS tak bocor."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bom as B  # noqa: E402
from app.schemas import bom as S  # noqa: E402

BS, KA = "pt-busana-indah-semesta", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


async def biaya(c, tid, bid):
    r = await c.fetchrow("SELECT status, standard_cost, labor_cost, overhead_cost, total_cost FROM bill_of_materials WHERE id = $1 AND tenant_id = $2", bid, tid)
    return {k: (float(v) if k != "status" else v) for k, v in dict(r).items()}


async def tarif_ops(c, tid, bid):
    return [(r["operation_name"], float(r["labor_rate_per_hour"]), float(r["overhead_rate_per_hour"])) for r in await c.fetch(
        """SELECT o.operation_name, o.labor_rate_per_hour, o.overhead_rate_per_hour FROM bom_operations o
           JOIN bill_of_materials b ON b.id = o.bom_id AND b.tenant_id = $2 WHERE o.bom_id = $1 ORDER BY o.operation_number""", bid, tid)]


async def produk(c, tid, n):
    return [r["id"] for r in await c.fetch("SELECT id FROM products WHERE tenant_id = $1 ORDER BY created_at, id LIMIT $2", tid, n)]


def badan_skenario(ids, wc, kode, rate_overhead=None):
    ov = {} if rate_overhead is None else {"overhead_rate_per_hour": Decimal(str(rate_overhead))}
    return S.CreateBOMRequest(
        product_id=ids[0], bom_code=kode, output_quantity=Decimal("1"),
        components=[S.BOMComponentInput(component_product_id=ids[1], quantity=Decimal("1"), unit_cost=Decimal("40000")),
                    S.BOMComponentInput(component_product_id=ids[2], quantity=Decimal("0.02"), unit_cost=Decimal("15000")),
                    S.BOMComponentInput(component_product_id=ids[3], quantity=Decimal("1"), unit_cost=Decimal("500"))],
        operations=[S.BOMOperationInput(operation_number=1, operation_name="Potong", labor_mode="piece_rate", cost_per_piece=Decimal("2000")),
                    S.BOMOperationInput(operation_number=2, operation_name="Jahit", labor_mode="piece_rate", cost_per_piece=Decimal("6000")),
                    S.BOMOperationInput(operation_number=3, operation_name="Finishing", labor_mode="piece_rate", cost_per_piece=Decimal("1500")),
                    S.BOMOperationInput(operation_number=4, operation_name="Overhead Jahit", work_center_id=wc, run_time_minutes=6, labor_mode="time_based", **ov)])


async def main():
    pool = await B.get_pool()
    async with pool.acquire() as c:
        ids = await produk(c, BS, 4)
        idk = await produk(c, KA, 4)
        cek("prasyarat: BS >= 4 barang, KA >= 4 barang", len(ids) == 4 and len(idk) == 4, f"{len(ids)} {len(idk)}")
        rb, rk = await req(c, BS), await req(c, KA)
        w = await B.create_work_center(rb, S.CreateWorkCenterRequest(code="UJI179-" + uuid.uuid4().hex[:6], name="UJI Jahit 179", capacity_per_hour=Decimal("10"),
                                                                     labor_rate_per_hour=Decimal("0"), overhead_rate_per_hour=Decimal("20000")))
        wc = uuid.UUID(str(w["data"]["id"]))
        # S1
        r1 = await B.create_bom(rb, badan_skenario(ids, wc, "UJI179-S1-" + uuid.uuid4().hex[:5]))
        b1 = uuid.UUID(str(r1["data"]["id"]))
        x = await biaya(c, BS, b1)
        cek("S1 skenario Accurate: standar 40.800 · labor 9.500 · overhead 2.000 · total 52.300", (x["standard_cost"], x["labor_cost"], x["overhead_cost"], x["total_cost"]) == (40800.0, 9500.0, 2000.0, 52300.0), str(x))
        cek("S1 tarif operasi Overhead Jahit tersimpan 20.000/jam (snapshot WC)", ("Overhead Jahit", 0.0, 20000.0) in await tarif_ops(c, BS, b1), str(await tarif_ops(c, BS, b1)))
        # S2 eksplisit
        r2 = await B.create_bom(rb, badan_skenario(ids, wc, "UJI179-S2-" + uuid.uuid4().hex[:5], rate_overhead=5000))
        b2 = uuid.UUID(str(r2["data"]["id"]))
        x2 = await biaya(c, BS, b2)
        cek("S2 tarif eksplisit 5.000 dipertahankan: overhead 500 (6/60 x 5.000), total 50.800", (x2["overhead_cost"], x2["total_cost"]) == (500.0, 50800.0), str(x2))
        # S3 BOM lama (rekaman bug): tarif 0 tersimpan
        await c.execute("""UPDATE bom_operations SET overhead_rate_per_hour = 0 WHERE bom_id = $1 AND bom_id IN (SELECT id FROM bill_of_materials WHERE id = $1 AND tenant_id = $2)""", b1, BS)
        await c.fetchval("SELECT calculate_bom_cost($1)", b1)
        lama = await biaya(c, BS, b1)
        cek("S3 prasyarat: BOM lama bertarif 0 -> total 50.300 (angka bug MH)", lama["total_cost"] == 50300.0 and lama["overhead_cost"] == 0.0, str(lama))
        p = (await B.recalculate_cost_preview(rb, b1))["data"]
        cek("S3 pratinjau: tarif lama 0 -> baru 20.000, biaya 50.300 -> 52.300", p["bisa_segarkan_tarif"] is True and len(p["tarif"]) == 1 and p["tarif"][0]["overhead_rate_per_hour"] == {"lama": 0.0, "baru": 20000.0}
            and p["sebelum"]["total_cost"] == 50300.0 and p["sesudah"]["total_cost"] == 52300.0 and p["sesudah"]["overhead_cost"] == 2000.0, str(p))
        cek("S3 pratinjau NOL tulis (baris BOM + tarif operasi tak berubah)", await biaya(c, BS, b1) == lama and ("Overhead Jahit", 0.0, 0.0) in await tarif_ops(c, BS, b1))
        rc = (await B.recalculate_cost(rb, b1))["data"]
        x3 = await biaya(c, BS, b1)
        cek("S3 recalculate draf: 52.300, tarif_diperbarui 1, pratinjau == hasil nyata", x3["total_cost"] == 52300.0 and len(rc["tarif_diperbarui"]) == 1 and rc["tarif_diperbarui"] == p["tarif"] and p["sesudah"] == {k: x3[k] for k in p["sesudah"]}, f"{x3} {rc}")
        # S4 BOM aktif
        await c.execute("""UPDATE bom_operations SET overhead_rate_per_hour = 0 WHERE bom_id = $1 AND bom_id IN (SELECT id FROM bill_of_materials WHERE id = $1 AND tenant_id = $2)""", b1, BS)
        await c.execute("UPDATE bill_of_materials SET status = 'active' WHERE id = $1 AND tenant_id = $2", b1, BS)
        await c.fetchval("SELECT calculate_bom_cost($1)", b1)
        aktif0 = await biaya(c, BS, b1)
        pa = (await B.recalculate_cost_preview(rb, b1))["data"]
        await B.recalculate_cost(rb, b1)
        cek("S4 BOM aktif: bisa_segarkan false, tarif kosong; recalculate TAK mengubah tarif/biaya", pa["bisa_segarkan_tarif"] is False and pa["tarif"] == [] and await biaya(c, BS, b1) == aktif0
            and ("Overhead Jahit", 0.0, 0.0) in await tarif_ops(c, BS, b1), f"{pa} {aktif0}")
        # S5 lintas tenant
        for fn, nama in ((B.recalculate_cost, "recalculate"), (B.recalculate_cost_preview, "pratinjau")):
            e = await galat(fn(rk, b2))
            cek(f"S5 {nama} BOM tenant BS dibuka KA -> 404", isinstance(e, HTTPException) and e.status_code == 404, str(e)[:120])
        bad = badan_skenario(idk, wc, "UJI179-X-" + uuid.uuid4().hex[:5])
        rx = await B.create_bom(rk, bad)
        bx = uuid.UUID(str(rx["data"]["id"]))
        cek("S5 buat BOM KA dgn id WC milik BS: tarif TIDAK terbaca (0), overhead 0", ("Overhead Jahit", 0.0, 0.0) in await tarif_ops(c, KA, bx) and (await biaya(c, KA, bx))["overhead_cost"] == 0.0, str(await tarif_ops(c, KA, bx)))
        await B.update_bom(rk, bx, S.UpdateBOMRequest(operations=bad.operations))
        cek("S5 ubah BOM KA dgn id WC milik BS: tarif TIDAK terbaca (0)", ("Overhead Jahit", 0.0, 0.0) in await tarif_ops(c, KA, bx), str(await tarif_ops(c, KA, bx)))
        px = (await B.recalculate_cost_preview(rk, bx))["data"]
        cek("S5 pratinjau BOM KA: tarif WC BS tak bocor (tarif kosong)", px["tarif"] == [], str(px))
        # ubah BOM BS lewat jalur UBAH memberi tarif dari WC
        r4 = await B.create_bom(rb, S.CreateBOMRequest(product_id=ids[0], bom_code="UJI179-U-" + uuid.uuid4().hex[:5], output_quantity=Decimal("1")))
        b4 = uuid.UUID(str(r4["data"]["id"]))
        await B.update_bom(rb, b4, S.UpdateBOMRequest(operations=badan_skenario(ids, wc, "x").operations))
        cek("S1u jalur UBAH BS: overhead 2.000 dari WC", (await biaya(c, BS, b4))["overhead_cost"] == 2000.0, str(await biaya(c, BS, b4)))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
