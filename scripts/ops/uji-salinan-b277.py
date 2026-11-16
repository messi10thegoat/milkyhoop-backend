"""BUG-277 uji DB di SALINAN (DRAF, belum pernah dijalankan): EB.SQL_BARANG ASLI atas klon, tenant kaos + nyawit. A kueri jalan, jumlah == hitung products tak terhapus; B xlsx dibaca balik pembaca impor = jumlah sama;
C sel berawalan rumus selalu berapostrof; D baca-saja (jumlah products sama)."""
import asyncio
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import ekspor_barang as EB  # noqa: E402
from app.services import impor_barang as IB  # noqa: E402

HASIL = []


def cek(n, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + n + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await RI.get_pool()
    async with pool.acquire() as c:
        for tid in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            n0 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL", tid)
            rows = await c.fetch(EB.SQL_BARANG + " ORDER BY p.id", tid)
            cek(f"A {tid}: {len(rows)} baris == {n0} barang", len(rows) == n0)
            baris, banyak, lewat = EB.siapkan(rows[: EB.MAKS_BARIS])
            out = IB.baca_xlsx(EB.bangun_xlsx(baris, banyak, lewat))
            cek(f"B {tid}: xlsx dibaca balik {len(out['baris'])} baris", len(out["baris"]) == len(baris))
            cek(f"C {tid}: tak ada sel teks berawalan rumus tanpa apostrof", not [v for x in baris for v in x if isinstance(v, str) and v[:1] in "=+-@\t\r"])
            n1 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL", tid)
            cek(f"D {tid}: baca-saja ({n0} == {n1})", n0 == n1)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
