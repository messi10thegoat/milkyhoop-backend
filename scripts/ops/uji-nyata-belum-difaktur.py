"""UJI NYATA BELUM-DIFAKTUR (24 Okt 2026), READ ONLY (pool default_transaction_read_only=on), rute NYATA. Mode: baru [merah_dp|merah_filter]
Per tenant (kaos + grapgrap-manado, BACA saja):
(1) summary belum_difaktur_count == jumlah ?aksi=belum_difaktur == uninvoiced_count (definisi Q-011 lama, predikat sama);
(2) summary belum_difaktur_value == Σ sisa_belum_difaktur baris hasil filter == sisa_belum_difaktur summary; nilai − dp konsisten;
(3) angka == SQL lepas terukur 24 Okt (grapgrap 54.266.000 / 17) bila tenant grapgrap-manado [kontrol dua sumber; kaos dibanding dgn SQL saat uji];
(4) detail SO (3 SO terbesar hasil filter) medan == baris daftar;
(5) kolom aksi baris TIDAK berubah oleh kunci laporan: daftar tanpa filter aksi/aksi_semua == fakta(laporan=False).
Kontrol merah (WAJIB FAIL) dari SALINAN pohon (scripts/ops/sabotase-belum-difaktur.py): merah_dp = kartu memakai nilai TANPA DP;
merah_filter = filter memanggil fakta tanpa laporan (pill kosong)."""
import asyncio, os, sys
from decimal import Decimal as D
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request

TENANT = ["kaos-biru-konveksi", "grapgrap-manado"]
gagal = []
SQL_SISA = """
WITH so AS (SELECT id, total_amount FROM sales_orders WHERE tenant_id = $1 AND status NOT IN ('draft', 'cancelled', 'completed')),
fk AS (SELECT si.sales_order_id AS so_id, SUM(jl.debit) AS difaktur FROM sales_invoices si
       JOIN journal_entries je ON je.source_id = si.id AND je.source_type = 'INVOICE' AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
       JOIN journal_lines jl ON jl.journal_id = je.id AND jl.debit > 0
       JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE'
       WHERE si.tenant_id = $1 AND si.status NOT IN ('draft', 'void') GROUP BY 1),
dp AS (SELECT COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id,
              SUM(GREATEST(0, cd.amount - COALESCE(cd.amount_applied, 0) - COALESCE(cd.amount_refunded, 0))) AS dp
       FROM customer_deposits cd LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
       WHERE cd.tenant_id = $1 AND cd.status IN ('posted', 'partial') GROUP BY 1),
b AS (SELECT GREATEST(0, so.total_amount - COALESCE(fk.difaktur, 0)) AS belum, COALESCE(dp.dp, 0) AS dp
      FROM so LEFT JOIN fk ON fk.so_id = so.id LEFT JOIN dp ON dp.so_id = so.id)
SELECT count(*) FILTER (WHERE belum > 0) AS n, COALESCE(SUM(GREATEST(0, belum - dp)) FILTER (WHERE belum > 0), 0) AS sisa FROM b"""


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import sales_orders as SO
    from app.services import so_aksi as A
    from app.utils.tanggal_tenant import tanggal_dokumen
    pool = await asyncpg.create_pool(os.environ["URL"], min_size=1, max_size=4, server_settings={"default_transaction_read_only": "on"})
    async with pool.acquire() as c0:
        assert await c0.fetchval("SHOW default_transaction_read_only") == "on"

    async def gp(): return pool
    SO.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp

    for T in TENANT:
        async with pool.acquire() as c:
            if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
                sys.exit(f"TENANT TAK ADA: {T}")
            uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)
            sql = await c.fetchrow(SQL_SISA, T)
            hari = await tanggal_dokumen(c, T)

        def req():
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
            r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
            return r

        def dt(x): return x.model_dump(mode="json") if hasattr(x, "model_dump") else x

        async def daftar(aksi):
            rows, skip = [], 0
            while True:
                r = dt(await SO.list_sales_orders(req(), status="all", customer_id=None, start_date=None, end_date=None, search=None, skip=skip,
                                                 limit=100, tugas=None, sort_by=None, sort_dir="desc", aksi=aksi, with_totals=False))
                rows += r["items"]
                if not r["has_more"]:
                    return rows
                skip += 100

        s = dt(await SO.get_sales_order_summary(req(), search=None))["data"]
        fl = await daftar("belum_difaktur")
        sisa_baris = sum(D(str(x["sisa_belum_difaktur"])) for x in fl)
        print(f"INFO {T}: kartu {s['belum_difaktur_value']} / {s['belum_difaktur_count']} pesanan; nilai {s['nilai_belum_difaktur']} − DP "
              f"{s['dp_belum_dipakai']}; SQL lepas {sql['sisa']} / {sql['n']}", flush=True)
        cek(f"{T} (1) summary count == ?aksi=belum_difaktur == uninvoiced_count", s["belum_difaktur_count"] == len(fl) == s["uninvoiced_count"],
            (s["belum_difaktur_count"], len(fl), s["uninvoiced_count"]))
        cek(f"{T} (2) kartu == Σ sisa baris == sisa summary; nilai − dp konsisten",
            D(str(s["belum_difaktur_value"])) == sisa_baris == D(str(s["sisa_belum_difaktur"]))
            and all(D(str(x["sisa_belum_difaktur"])) == max(D(0), D(str(x["nilai_belum_difaktur"])) - D(str(x["dp_belum_dipakai"]))) for x in fl)
            and all(D(str(x["nilai_belum_difaktur"])) > 0 for x in fl), (s["belum_difaktur_value"], sisa_baris, s["sisa_belum_difaktur"]))
        cek(f"{T} (3) dua sumber: kartu == SQL lepas (nilai jurnal, DP cache)", D(str(s["belum_difaktur_value"])) == D(str(sql["sisa"]))
            and s["belum_difaktur_count"] == sql["n"], (s["belum_difaktur_value"], sql["sisa"], s["belum_difaktur_count"], sql["n"]))
        if T == "grapgrap-manado":
            cek("grapgrap (3b) == angka terlapor ke pemilik 24 Okt (54.266.000 / 17)", D(str(s["belum_difaktur_value"])) == D("54266000")
                and s["belum_difaktur_count"] == 17, (s["belum_difaktur_value"], s["belum_difaktur_count"]))
        beda = []
        for x in sorted(fl, key=lambda x: -x["nilai_belum_difaktur"])[:3]:
            d = dt(await SO.get_sales_order_detail(req(), x["id"]))["data"]
            beda += [(x["order_number"], m, x[m], d[m]) for m in A.MEDAN_BELUM_DIFAKTUR if x[m] != d[m]]
        cek(f"{T} (4) detail == baris (3 SO terbesar)", not beda, beda)
        semua = await daftar(None)
        async with pool.acquire() as c:
            f0 = await A.fakta(c, T, hari)
        salah = [x["order_number"] for x in semua
                 if x["aksi"] != A.aksi_utama(A.aksi_baris(f0[__import__("uuid").UUID(x["id"])]["aksi"]))
                 or x["aksi_semua"] != A.aksi_baris(f0[__import__("uuid").UUID(x["id"])]["aksi"])]
        cek(f"{T} (5) kolom aksi baris tak berubah oleh kunci laporan ({len(semua)} SO)", not salah and all("belum_difaktur" not in x["aksi_semua"]
                                                                                                  for x in semua), salah[:5])
    await pool.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
