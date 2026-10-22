"""UJI NYATA Recurring bills / generate (22 Okt 2026, G3): BACA-SAJA. recurring_bills = 0 baris di produksi -> pembuktian:
 A. SQL yang ditulis generate (INSERT bills, INSERT bill_items, UPDATE recurring_bills, SELECT idempotensi) di-PREPARE terhadap skema nyata (kolom/tipe/NOT NULL
    terurai; tak menulis) -- dulu INSERT hanya terbukti di kode, tak pernah berjalan.
 B. statis: tak ada lagi penulisan status 'posted' ke bills tanpa jurnal; posting hanya lewat BillsService.post_bill.
 C. info: draf tagihan dgn status_v2 NULL (jalur lama) yang tak bisa diposting kanonik."""
import asyncio, inspect, os, re, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from app.routers import recurring_bills as RB

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:200]}"))
    if not ok: gagal.append(nama)


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    src = inspect.getsource(RB.generate_bill)
    sqls = re.findall(r'"""(.*?)"""', src, flags=re.S)
    sqls = [s for s in sqls if re.match(r"\s*(INSERT INTO|UPDATE|SELECT)\b", s)]  # docstring dibuang
    cek(f"generate memuat 5 pernyataan SQL ({len(sqls)})", len(sqls) == 5, len(sqls))
    for s in sqls:
        ket = " ".join(s.split())[:46]
        try:
            await c.prepare(s)
            cek(f"PREPARE terurai di skema nyata: {ket}", True)
        except Exception as e:
            cek(f"PREPARE terurai di skema nyata: {ket}", False, e)
    cek("statis: tak ada tulis status 'posted' ke bills di generate", "UPDATE bills SET status" not in src and "bill_status = \"posted\"" not in src)
    cek("statis: posting via BillsService.post_bill", ".post_bill(tenant_id=tid" in src)
    n = await c.fetchval("SELECT count(*) FROM bills WHERE status_v2 IS NULL")
    print(f"INFO bills status_v2 NULL (jalur lama; tak bisa diposting kanonik) = {n}; recurring_bills = {await c.fetchval('SELECT count(*) FROM recurring_bills')} baris")
    print("RINGKAS RECURRING-GENERATE gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
