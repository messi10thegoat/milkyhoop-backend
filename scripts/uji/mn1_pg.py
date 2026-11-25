import asyncio, os, uuid
from decimal import Decimal as D
import asyncpg
from app.services import stok_minimum_gudang as S
T = "t"
async def main():
    c = await asyncpg.connect(os.environ["DSN"])
    p, g1, g2, gs = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await c.execute("INSERT INTO products VALUES($1,'t','Kaos',NULL,'pcs',NULL)", p)
    for g, n, sy in ((g1, "Utama", False), (g2, "Cabang", False), (gs, "Transit", True)):
        await c.execute("INSERT INTO warehouses VALUES($1,'t',$2,true,$3)", g, n, sy)
    await c.execute("INSERT INTO inventory_ledger(tenant_id,product_id,warehouse_id,quantity_in,quantity_out) VALUES('t',$1,$2,10,0),('t',$1,$2,0,7),('t',$1,$3,4,0),('t',$1,$4,9,0)", p, g1, g2, gs)
    assert [r["gudang"] for r in await S.baca(c, T, p)] == ["Cabang", "Utama"]
    await S.simpan(c, T, p, [{"gudang_id": g1, "minimum": 5, "maksimum": 20}, {"gudang_id": g2, "minimum": 2, "maksimum": None}])
    b = {r["gudang"]: r for r in await S.baca(c, T, p)}
    assert b["Utama"]["stok"] == 3 and b["Utama"]["usulan_beli"] == 2 and b["Cabang"]["usulan_beli"] == 0 and b["Utama"]["maksimum"] == 20
    d = await S.daftar_pesan_ulang(c, T)
    assert d["jumlah_perlu_pesan"] == 1 and d["jumlah_dipantau"] == 2 and d["items"][0]["teks"] == "Kaos di Utama: stok 3 pcs, minimum 5 — usul pesan 2 pcs."
    for bad, kode in (([{"gudang_id": gs, "minimum": 1}], "GUDANG_TAK_SAH"), ([{"gudang_id": g1, "minimum": 5, "maksimum": 1}], "MINIMUM_TAK_SAH")):
        try:
            await S.simpan(c, T, p, bad); raise SystemExit("tak menolak " + kode)
        except Exception as e: assert getattr(e, "code", "") == kode, e
    await S.simpan(c, T, p, [{"gudang_id": g2, "minimum": 0, "maksimum": None}])
    assert await c.fetchval("SELECT count(*) FROM stok_minimum_gudang") == 1
    print("UJI-MN1-PG-LULUS")
asyncio.run(main())
