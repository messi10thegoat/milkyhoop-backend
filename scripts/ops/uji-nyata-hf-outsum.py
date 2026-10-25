"""UJI NYATA HOTFIX outstanding-summary (25 Okt 2026): BillsService.get_outstanding_summary pohon yang di-mount dijalankan ke DB
NYATA dalam transaksi READ ONLY (default_transaction_read_only=on) untuk kaos + grapgrap-manado. MODE=lama: pohon master WAJIB
gagal PostgresSyntaxError (kontrol merah: tes ini memang mengeksekusi kueri itu). MODE=baru: sukses + angka == compute_ap_outstanding
(outstanding = SUM semua baris = saldo vendor; counts.total = baris ber-bill_id; lewat tempo = baris ber-bill_id due_date < hari ini)."""
import asyncio, os, sys
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from app.services import bills_service as BS
from app.utils.tanggal_tenant import tanggal_dokumen

MODE = os.environ["MODE"]
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok:
        gagal.append(nama)


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"]); await c.execute("SET default_transaction_read_only = on")
    ada = {r[0] for r in await c.fetch('SELECT id FROM "Tenant"')}
    hilang = {"kaos-biru-konveksi", "grapgrap-manado"} - ada
    if hilang:
        sys.exit(f"TENANT TAK ADA: {sorted(hilang)} -- alat ukur menolak")

    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x):
                    x.tr = c.transaction(readonly=True); await x.tr.start(); return c
                async def __aexit__(x, *a):
                    await x.tr.rollback(); return False
            return CM()
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        try:
            r = await BS.BillsService(Pool()).get_outstanding_summary(t)
        except asyncpg.PostgresSyntaxError as e:
            cek(f"{t} lama-gagal-sintaks", MODE == "lama", e)
            continue
        except Exception as e:
            cek(f"{t} galat-lain", False, repr(e)); continue
        if MODE == "lama":
            cek(f"{t} lama-HARUS-gagal", False, "pohon lama sukses -> kontrol merah tak menggigit"); continue
        d = r["data"]
        hari = await tanggal_dokumen(c, t)
        rows = await c.fetch("SELECT bill_id, due_date, outstanding FROM compute_ap_outstanding($1)", t)
        tot = sum((Decimal(x["outstanding"]) for x in rows), Decimal(0))
        n = sum(1 for x in rows if x["bill_id"] is not None)
        lt = sum((Decimal(x["outstanding"]) for x in rows if x["bill_id"] is not None and x["due_date"] and x["due_date"] < hari), Decimal(0))
        print(f"  {t}: outstanding={d['amounts']['outstanding']} total={d['counts']['total']} overdue={d['amounts']['overdue']} "
              f"vendors={len(d['by_vendor'])} | fungsi: SUM={tot} n_tagihan={n} lewat_tempo={lt} baris={len(rows)}")
        cek(f"{t} sukses", r.get("success") is True)
        cek(f"{t} outstanding==SUM fungsi", Decimal(d["amounts"]["outstanding"]) == tot.to_integral_value(rounding="ROUND_DOWN"), (d["amounts"]["outstanding"], tot))
        cek(f"{t} counts.total==baris ber-bill_id", d["counts"]["total"] == n, (d["counts"]["total"], n))
        cek(f"{t} overdue==fungsi", Decimal(d["amounts"]["overdue"]) == lt.to_integral_value(rounding="ROUND_DOWN"), (d["amounts"]["overdue"], lt))
        cek(f"{t} as_of = tanggal bisnis", d["as_of_date"] == hari.isoformat())
    await c.close()
    print("gagal=", len(gagal), gagal)
    sys.exit(1 if gagal else 0)

asyncio.run(main())
