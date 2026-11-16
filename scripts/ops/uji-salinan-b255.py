"""BUG-255 uji DB di SALINAN (DRAF, belum pernah dijalankan): pratinjau + tulis ASLI atas klon milkydb (scratch), tenant kaos + nyawit; tiap tenant dalam transaksi yang di-rollback.
A pratinjau ok; B tulis ke daftar aktif: berubah == berubah pratinjau, aktivitas == berubah; C ulang = 0 berubah; D kembali ke bawaan (NULL); E daftar tak ada = blok."""
import asyncio
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import customers as CU  # noqa: E402
from app.services import dh_pelanggan_massal as DH  # noqa: E402

HASIL = []


def cek(n, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + n + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await CU.get_pool()
    async with pool.acquire() as c:
        for tid in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            ids = [str(r["id"]) for r in await c.fetch("/* tenant_id */ SELECT id FROM customers WHERE tenant_id = $1 AND is_active ORDER BY id LIMIT 20", tid)]
            dl = await c.fetchrow("/* tenant_id */ SELECT id FROM price_lists WHERE tenant_id = $1 AND is_active LIMIT 1", tid)
            if not ids or not dl:
                cek(f"{tid}: data uji tersedia", False, "tak ada pelanggan/daftar aktif")
                continue
            tr = c.transaction()
            await tr.start()
            try:
                body = {"price_list_id": str(dl["id"]), "customer_ids": ids}
                r, _ = await CU._urai_dh_massal(c, tid, body)
                cek(f"A {tid}: pratinjau ok, dipilih {r['ringkas']['dipilih']}", r["ok"] and r["ringkas"]["dipilih"] == len(ids))
                h = await DH.tulis(c, {"tenant_id": tid, "user_id": None}, r, "uji")
                na = await c.fetchval("/* tenant_id */ SELECT count(*) FROM customer_activities WHERE tenant_id = $1 AND field_name = 'price_list_id' AND description LIKE '%(massal)'", tid)
                cek(f"B {tid}: berubah {h['jumlah_berubah']}, aktivitas {na}", h["jumlah_berubah"] == r["ringkas"]["berubah"] and na == h["jumlah_berubah"])
                r2, _ = await CU._urai_dh_massal(c, tid, body)
                cek(f"C {tid}: ulang 0 berubah", r2["ringkas"]["berubah"] == 0)
                r3, _ = await CU._urai_dh_massal(c, tid, {"price_list_id": None, "customer_ids": ids})
                await DH.tulis(c, {"tenant_id": tid, "user_id": None}, r3, "uji")
                nul = await c.fetchval("/* tenant_id */ SELECT count(*) FROM customers WHERE tenant_id = $1 AND id = ANY($2::uuid[]) AND price_list_id IS NULL", tid, ids)
                cek(f"D {tid}: kembali ke bawaan {nul}/{len(ids)}", nul == len(ids))
                r4, _ = await CU._urai_dh_massal(c, tid, {"price_list_id": "00000000-0000-0000-0000-000000000001", "customer_ids": ids})
                cek(f"E {tid}: daftar tak ada = blok", not r4["ok"] and r4["blocks"][0]["code"] == "DAFTAR_TIDAK_ADA")
            finally:
                await tr.rollback()
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
