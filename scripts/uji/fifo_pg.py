import asyncio, os, uuid, datetime
from decimal import Decimal as D
import asyncpg
from app.services import fifo_lapisan as F
T, P = "t", uuid.UUID(int=1)
async def ledger(c):
    i = uuid.uuid4(); await c.execute("INSERT INTO inventory_ledger VALUES($1)", i); return i
async def sisa(c):
    r = await c.fetchrow("SELECT COALESCE(SUM(qty),0) q, COALESCE(SUM(nilai),0) n FROM (" + F.SQL_SISA.replace("HAVING l.qty_awal + COALESCE(SUM(m.qty), 0) > 0", "") + ") x", T, P)
    return D(str(r["q"])), D(str(r["n"]))
async def main():
    c = await asyncpg.connect(os.environ["DSN"])
    assert await F.fifo_aktif(c, T, P) is False                        # flag mati
    await c.execute("INSERT INTO tenant_features VALUES('t','fifo_v1',true)")
    assert await F.fifo_aktif(c, T, P) is False                        # costing_mulai kosong
    await c.execute("UPDATE products SET costing_mulai='2026-11-25'")
    assert await F.fifo_aktif(c, T, P) is True
    d = datetime.date(2026, 11, 25)
    async with c.transaction():
        g1 = await ledger(c); await F.catat_masuk(c, T, P, g1, d, D(180), D("3400000.00"))
        g2 = await ledger(c); await F.catat_masuk(c, T, P, g2, d, D(10), F.nilai_masuk(10, "20000.5"))
    assert await sisa(c) == (D(190), D("3600005.00"))
    async with c.transaction():
        a = await F.ambil_keluar(c, T, P, D(185)); k1 = await ledger(c)
        rata = await F.rata_sisa_sesudah(c, T, P, a); await F.catat_pakai(c, T, k1, a)
    assert sum(x.nilai for x in a) == D("3400000.00") + D("100002.50") and rata == D("20000.50")
    assert await sisa(c) == (D(5), D("100002.50")) or print(await sisa(c))
    try:
        await F.rencana_balik(c, T, P, g1, True); raise SystemExit("T5 tak menolak")
    except Exception as e: assert getattr(e, "code", "") == "FIFO_LAPISAN_TERPAKAI", e
    async with c.transaction():
        b = await F.rencana_balik(c, T, P, k1, False); v = await ledger(c); await F.catat_balik(c, T, v, b)
    assert b.nilai == D("3500002.50") and await sisa(c) == (D(190), D("3600005.00"))
    try:
        await F.ambil_keluar(c, T, P, D("190.0001")); raise SystemExit("T4 tak menolak")
    except F.StokFifoKurang: pass
    g3 = await ledger(c); await F.catat_masuk(c, T, P, g3, d, D(1), D(7))
    async with c.transaction():
        b = await F.rencana_balik(c, T, P, g3, True); v = await ledger(c); await F.catat_balik(c, T, v, b)
    assert await sisa(c) == (D(190), D("3600005.00"))
    try:
        await F.rencana_balik(c, T, P, await ledger(c), False); raise SystemExit("sebelum mulai tak menolak")
    except Exception as e: assert getattr(e, "code", "") == "FIFO_SEBELUM_MULAI", e
    try:
        await c.execute("UPDATE products SET costing_method='weighted_average'"); raise SystemExit("T1 tak menolak")
    except asyncpg.RaiseError as e: assert "FIFO_METODE_TERKUNCI" in str(e)
    print("UJI-PG-LULUS")
asyncio.run(main())
