"""BUG-180 uji DB di SALINAN (bukan prod; migrasi V495 diterapkan HANYA di klon oleh skrip pembungkus): handler ASLI routers/bom.py + calculate_bom_cost atas klon, tenant pt-busana-indah-semesta (BS) + kaos (KA).
S1 Accurate dgn Jahit PER PCS Rp6.000 + WC 6 mnt @20.000/jam TANPA operasi overhead terpisah: standar 40.800 · labor 9.500 · overhead 2.000 · TOTAL 52.300 · S2 per jam tetap (operasi 'Overhead Jahit' per jam terpisah): 52.300
· S3 per pcs TANPA WC dgn 6 menit: overhead 0 · S4 per pcs ber-WC + operasi per jam ber-WC sama: overhead 4.000 (tiap operasi dihitung SEKALI; tak dobel) · S5 per pcs ber-WC milik TENANT LAIN: tarif tak terbaca -> overhead 0
· S6 BOM lama (tarif operasi 0 tersimpan): pratinjau recalculate tarif 0->20.000 & total 50.300->52.300 nol tulis; recalculate draf -> 52.300; BOM AKTIF tak berubah · S7 fungsi lama (V134) vs baru: BOM TANPA operasi per pcs ber-WC identik."""
import asyncio
import sys
import uuid
from decimal import Decimal

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


async def biaya(c, tid, bid):
    r = await c.fetchrow("SELECT status, standard_cost, labor_cost, overhead_cost, total_cost FROM bill_of_materials WHERE id = $1 AND tenant_id = $2", bid, tid)
    return {k: (float(v) if k != "status" else v) for k, v in dict(r).items()}


async def produk(c, tid, n):
    return [r["id"] for r in await c.fetch("SELECT id FROM products WHERE tenant_id = $1 ORDER BY created_at, id LIMIT $2", tid, n)]


def komponen(ids):
    return [S.BOMComponentInput(component_product_id=ids[1], quantity=Decimal("1"), unit_cost=Decimal("40000")),
            S.BOMComponentInput(component_product_id=ids[2], quantity=Decimal("0.02"), unit_cost=Decimal("15000")),
            S.BOMComponentInput(component_product_id=ids[3], quantity=Decimal("1"), unit_cost=Decimal("500"))]


def op(n, nama, tarif, wc=None, menit=None, mode="piece_rate"):
    return S.BOMOperationInput(operation_number=n, operation_name=nama, labor_mode=mode, cost_per_piece=Decimal(str(tarif)) if mode == "piece_rate" else None,
                               work_center_id=wc, run_time_minutes=menit)


async def buat(c, rq, tid, ids, kode, ops):
    r = await B.create_bom(rq, S.CreateBOMRequest(product_id=ids[0], bom_code=f"UJI180-{kode}-" + uuid.uuid4().hex[:5], output_quantity=Decimal("1"), components=komponen(ids), operations=ops))
    return uuid.UUID(str(r["data"]["id"]))


async def main():
    pool = await B.get_pool()
    async with pool.acquire() as c:
        ids, idk = await produk(c, BS, 4), await produk(c, KA, 4)
        cek("prasyarat: BS >= 4 barang, KA >= 4 barang", len(ids) == 4 and len(idk) == 4)
        rb, rk = await req(c, BS), await req(c, KA)
        w = await B.create_work_center(rb, S.CreateWorkCenterRequest(code="UJI180-" + uuid.uuid4().hex[:6], name="UJI Jahit 180", capacity_per_hour=Decimal("10"), overhead_rate_per_hour=Decimal("20000")))
        wc = uuid.UUID(str(w["data"]["id"]))
        wk = await B.create_work_center(rk, S.CreateWorkCenterRequest(code="UJI180-" + uuid.uuid4().hex[:6], name="UJI KA 180", capacity_per_hour=Decimal("10"), overhead_rate_per_hour=Decimal("99000")))
        wck = uuid.UUID(str(wk["data"]["id"]))
        # S1
        b1 = await buat(c, rb, BS, ids, "S1", [op(1, "Potong", 2000), op(2, "Jahit", 6000, wc, 6), op(3, "Finishing", 1500)])
        x = await biaya(c, BS, b1)
        cek("S1 per pcs ber-WC (Jahit 6.000, 6 mnt @20.000/jam) tanpa operasi overhead: 40.800 · 9.500 · 2.000 · 52.300", (x["standard_cost"], x["labor_cost"], x["overhead_cost"], x["total_cost"]) == (40800.0, 9500.0, 2000.0, 52300.0), str(x))
        # S2 per jam tetap
        b2 = await buat(c, rb, BS, ids, "S2", [op(1, "Potong", 2000), op(2, "Jahit", 6000), op(3, "Finishing", 1500), op(4, "Overhead Jahit", 0, wc, 6, "time_based")])
        x2 = await biaya(c, BS, b2)
        cek("S2 per jam tetap: operasi 'Overhead Jahit' terpisah -> total 52.300 (overhead 2.000)", (x2["overhead_cost"], x2["total_cost"]) == (2000.0, 52300.0), str(x2))
        # S3 tanpa WC
        b3 = await buat(c, rb, BS, ids, "S3", [op(1, "Potong", 2000), op(2, "Jahit", 6000, None, 6), op(3, "Finishing", 1500)])
        x3 = await biaya(c, BS, b3)
        cek("S3 per pcs TANPA WC (menit 6): overhead 0, total 50.300", (x3["overhead_cost"], x3["total_cost"]) == (0.0, 50300.0), str(x3))
        # S4 tak dobel
        b4 = await buat(c, rb, BS, ids, "S4", [op(1, "Jahit", 6000, wc, 6), op(2, "Overhead Jahit", 0, wc, 6, "time_based")])
        x4 = await biaya(c, BS, b4)
        cek("S4 per pcs ber-WC + per jam ber-WC: overhead 4.000 (2.000 + 2.000; tiap operasi SEKALI)", x4["overhead_cost"] == 4000.0 and x4["labor_cost"] == 6000.0, str(x4))
        # S5 WC tenant lain
        b5 = await buat(c, rb, BS, ids, "S5", [op(1, "Jahit", 6000, wck, 6)])
        x5 = await biaya(c, BS, b5)
        cek("S5 per pcs dgn WC milik tenant LAIN: tarif tak terbaca, overhead 0", x5["overhead_cost"] == 0.0 and x5["labor_cost"] == 6000.0, str(x5))
        # S6 BOM lama
        await c.execute("""UPDATE bom_operations SET overhead_rate_per_hour = 0 WHERE bom_id = $1 AND bom_id IN (SELECT id FROM bill_of_materials WHERE id = $1 AND tenant_id = $2)""", b1, BS)
        await c.fetchval("SELECT calculate_bom_cost($1)", b1)
        lama = await biaya(c, BS, b1)
        cek("S6 prasyarat: BOM lama tarif 0 -> total 50.300", lama["total_cost"] == 50300.0, str(lama))
        p = (await B.recalculate_cost_preview(rb, b1))["data"]
        cek("S6 pratinjau: per pcs tarif 0 -> 20.000, total 50.300 -> 52.300, NOL tulis", p["bisa_segarkan_tarif"] and p["tarif"][0]["overhead_rate_per_hour"] == {"lama": 0.0, "baru": 20000.0}
            and p["sebelum"]["total_cost"] == 50300.0 and p["sesudah"]["total_cost"] == 52300.0 and await biaya(c, BS, b1) == lama, str(p))
        await B.recalculate_cost(rb, b1)
        cek("S6 recalculate draf -> 52.300", (await biaya(c, BS, b1))["total_cost"] == 52300.0)
        await c.execute("""UPDATE bom_operations SET overhead_rate_per_hour = 0 WHERE bom_id = $1 AND bom_id IN (SELECT id FROM bill_of_materials WHERE id = $1 AND tenant_id = $2)""", b1, BS)
        await c.execute("UPDATE bill_of_materials SET status = 'active' WHERE id = $1 AND tenant_id = $2", b1, BS)
        await c.fetchval("SELECT calculate_bom_cost($1)", b1)
        aktif0 = await biaya(c, BS, b1)
        await B.recalculate_cost(rb, b1)
        cek("S6 BOM AKTIF: recalculate tak mengubah tarif/biaya", await biaya(c, BS, b1) == aktif0, str(aktif0))
        # S7 BOM tanpa per pcs ber-WC: identik dgn rumus lama
        sama = await c.fetchval("""SELECT count(*) FROM bill_of_materials b WHERE b.tenant_id = $1 AND NOT EXISTS (SELECT 1 FROM bom_operations o WHERE o.bom_id = b.id AND o.labor_mode = 'piece_rate' AND o.work_center_id IS NOT NULL)""", BS)
        cek("S7 prasyarat: ada BOM tanpa per pcs ber-WC (sampel kontrol)", sama >= 1, str(sama))
        for bid in [r["id"] for r in await c.fetch("SELECT b.id FROM bill_of_materials b WHERE b.tenant_id = $1 AND NOT EXISTS (SELECT 1 FROM bom_operations o WHERE o.bom_id = b.id AND o.labor_mode = 'piece_rate' AND o.work_center_id IS NOT NULL) AND b.status = 'draft' LIMIT 5", BS)]:
            sebelum = await biaya(c, BS, bid)
            await c.fetchval("SELECT calculate_bom_cost($1)", bid)
            cek(f"S7 BOM {str(bid)[:8]} tanpa per pcs ber-WC: hitung ulang identik", await biaya(c, BS, bid) == sebelum, f"{sebelum} {await biaya(c, BS, bid)}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
