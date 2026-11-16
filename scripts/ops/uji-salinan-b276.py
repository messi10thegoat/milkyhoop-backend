"""BUG-276 uji DB di SALINAN (DRAF, belum pernah dijalankan): services/impor_barang.rencana ASLI (BACA-SAJA) atas klon, tenant kaos + nyawit.
A baris tiket "UJI Teh Celup, pcs, beli 3rb, jual 4rb": rencana ok, 1 barang, tanpa blok TAK_TERBACA, payload base_unit 'pcs' (atau padanan satuan tenant), harga beli 3000 / jual 4000
B tanpa 'isi' multi-baris campur: satuan tunggal + 'dus isi 12 pcs' + tanpa satuan: ketiganya terbaca (ok), satuan dasar masing-masing benar
C kontrol: 'Minyak Sania beli' tetap blok TAK_TERBACA; 'Teh, pcs, dus, jual 4rb' tetap blok (dua sisa)
D tak menulis: jumlah products sebelum == sesudah"""
import asyncio
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import impor_barang as IB  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await RI.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            ctx = {"tenant_id": tid, "user_id": None}
            n0 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1", tid)
            tr = c.transaction(readonly=True)
            await tr.start()
            try:
                r = await IB.rencana(c, ctx, IB.tafsir_teks("UJI-276 Teh Celup, pcs, beli 3rb, jual 4rb"))
                bl = [b["code"] for x in r["baris"] for b in x["blocks"]]
                p = r["baris"][0]["payload"] if r["baris"] else {}
                cek(f"A {tid}: baris tiket ok={r['ok']}, blok {bl}, base_unit {p.get('base_unit')}, beli {p.get('purchase_price')}, jual {p.get('sales_price')}",
                    r["ok"] and not bl and str(p.get("base_unit", "")).lower() == "pcs" and float(p.get("purchase_price") or 0) == 3000 and float(p.get("sales_price") or 0) == 4000)
                r2 = await IB.rencana(c, ctx, IB.tafsir_teks("UJI-276 A, pcs, jual 4rb\nUJI-276 B, dus isi 12 pcs, jual 40rb\nUJI-276 C, jual 5rb"))
                satuan = [str(x["payload"].get("base_unit", "")).lower() for x in r2["baris"]]
                cek(f"B {tid}: tiga baris campur ok={r2['ok']}, satuan dasar {satuan}", r2["ok"] and satuan == ["pcs", "pcs", "pcs"])
                for t in ("UJI-276 Minyak beli", "UJI-276 Teh, pcs, dus, jual 4rb"):
                    rr = await IB.rencana(c, ctx, IB.tafsir_teks(t))
                    cek(f"C {tid}: '{t}' tetap blok", not rr["ok"] and any(b["code"] == "TAK_TERBACA" for x in rr["baris"] for b in x["blocks"]))
            finally:
                await tr.rollback()
            n1 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM products WHERE tenant_id = $1", tid)
            cek(f"D {tid}: pratinjau tak menulis products ({n0} == {n1})", n0 == n1)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
