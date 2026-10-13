"""Harness NYATA pill AKSI Pesanan (W-20261014-AKSI-SO). kaos + grapgrap, pool READ ONLY (tulis apa pun -> galat).
Paritas: SQL INDEPENDEN (ditulis ulang dari definisi, tanpa kode aplikasi) == so_aksi.fakta (Python) == GET ?aksi=
(handler NYATA, semua halaman) == /summary <kunci>_count/_value; kolom aksi per baris == kunci pertama SQL.
Plus rincian SO berjalan yang 'belum ditagih' versi uang (kartu uninvoiced) tetapi bukan perlu_ditagih versi qty."""
import asyncio
import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402

from backend.api_gateway.app.routers import sales_orders as SO  # noqa: E402
from backend.api_gateway.app.services import so_agregat, so_aksi  # noqa: E402

TENANTS = ("kaos-biru-konveksi", "grapgrap-manado")
gagal = 0

SQL_INDEPENDEN = """
WITH so AS (SELECT s.*, tanggal_bisnis(s.tenant_id) AS hari FROM sales_orders s WHERE s.tenant_id = $1),
ln AS (SELECT so.id AS so_id, soi.id AS soi_id, soi.quantity AS q,
              COALESCE(soi.perlu_kirim, p.track_inventory, false) AS perlu, COALESCE(p.track_inventory, false) AS stok
       FROM so JOIN sales_order_items soi ON soi.sales_order_id = so.id
       LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id),
kirim AS (SELECT soi_id, SUM(k) AS k FROM (
    SELECT sii.sales_order_item_id AS soi_id, ifi.quantity AS k
    FROM sales_invoices si JOIN so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id
    JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    JOIN invoice_fulfillment_items ifi ON ifi.invoice_item_id = sii.id
    JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.tenant_id = si.tenant_id AND f.voided_at IS NULL AND f.status <> 'voided'
    UNION ALL
    SELECT ifi.sales_order_item_id, ifi.quantity
    FROM invoice_fulfillments f JOIN so ON so.id = f.sales_order_id AND so.tenant_id = f.tenant_id
    JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
    WHERE f.asal = 'so' AND f.voided_at IS NULL AND f.status <> 'voided') z GROUP BY 1),
fak AS (SELECT sii.sales_order_item_id AS soi_id, SUM(sii.quantity) AS f
        FROM sales_invoices si JOIN so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id
        JOIN sales_invoice_items sii ON sii.invoice_id = si.id
        WHERE si.status NOT IN ('draft','void') AND sii.sales_order_item_id IS NOT NULL GROUP BY 1),
lb AS (SELECT ln.so_id, (ln.perlu AND ln.q - COALESCE(kirim.k,0) > 0) AS sk,
              ((CASE WHEN ln.stok THEN COALESCE(kirim.k,0) ELSE ln.q END) - COALESCE(fak.f,0) > 0) AS st
       FROM ln LEFT JOIN kirim USING (soi_id) LEFT JOIN fak USING (soi_id)),
ps AS (SELECT so.id, so.order_number, so.status, so.total_amount, so.expected_ship_date, so.hari,
              so.status NOT IN ('draft','cancelled','completed') AS jalan,
              COALESCE(bool_or(lb.sk), false) AS sk, COALESCE(bool_or(lb.st), false) AS st
       FROM so LEFT JOIN lb ON lb.so_id = so.id GROUP BY 1,2,3,4,5,6)
SELECT id, order_number, status, total_amount,
       ARRAY_REMOVE(ARRAY[
         CASE WHEN jalan AND sk AND expected_ship_date < hari THEN 'telat_kirim' END,
         CASE WHEN status = 'draft' THEN 'perlu_dikonfirmasi' END,
         CASE WHEN jalan AND sk THEN 'perlu_dikirim' END,
         CASE WHEN jalan AND st THEN 'perlu_ditagih' END], NULL) AS aksi
FROM ps
"""


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    if not ok:
        gagal += 1


class _Pool:
    def __init__(self, p):
        self.p = p

    def acquire(self):
        return self.p.acquire()


async def main():
    baca = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2,
                                     server_settings={"default_transaction_read_only": "on"})
    async with baca.acquire() as c:
        cek(await c.fetchval("SHOW default_transaction_read_only") == "on", "pool READ ONLY")
        try:
            await c.execute("CREATE TEMP TABLE _x(i int)")
            cek(False, "tulis ditolak READ ONLY")
        except asyncpg.exceptions.ReadOnlySQLTransactionError:
            cek(True, "tulis ditolak READ ONLY")

    async def gp():
        return _Pool(baca)
    SO.get_pool = gp
    for t in TENANTS:
        SO.get_user_context = lambda r, t=t: {"tenant_id": t, "user_id": None}
        async with baca.acquire() as c:
            ind = {r["id"]: r for r in await c.fetch(SQL_INDEPENDEN, t)}
            hari = await SO.tanggal_dokumen(c, t)
            py = await so_aksi.fakta(c, t, hari)
            unin = await so_agregat.id_belum_ditagih(c, t)
        cek(set(ind) == set(py), f"{t}: himpunan SO sama", f"{len(ind)}")
        beda = [ind[i]["order_number"] for i in ind if list(ind[i]["aksi"]) != py[i]["aksi"]]
        cek(not beda, f"{t}: aksi per SO Python == SQL independen", ",".join(beda[:10]))
        summ = (await SO.get_sales_order_summary(None)).data
        summ = summ if isinstance(summ, dict) else summ.model_dump()
        baris = []
        for k in so_aksi.AKSI:
            sql_ids = {i for i, r in ind.items() if k in r["aksi"]}
            sql_v = float(sum((Decimal(str(ind[i]["total_amount"])) for i in sql_ids), Decimal(0)))
            api, skip = [], 0
            while True:
                r = await SO.list_sales_orders(None, status="all", customer_id=None, start_date=None, end_date=None,
                                               search=None, skip=skip, limit=100, tugas=None, sort_by=None,
                                               sort_dir="desc", aksi=k)
                api += r.items
                if not r.has_more:
                    break
                skip += 100
            api_ids = {__import__("uuid").UUID(x.id) for x in api}
            cek(api_ids == sql_ids, f"{t} {k}: ?aksi= == SQL", f"api {len(api_ids)} sql {len(sql_ids)}")
            cek(summ[f"{k}_count"] == len(sql_ids) and abs(summ[f"{k}_value"] - sql_v) < 0.005,
                f"{t} {k}: summary == SQL", f"{summ[f'{k}_count']}/{summ[f'{k}_value']} vs {len(sql_ids)}/{sql_v}")
            salah = [x.order_number for x in api if x.aksi != (list(ind[__import__('uuid').UUID(x.id)]['aksi']) or [None])[0]]
            cek(not salah, f"{t} {k}: kolom aksi baris == kunci pertama SQL", ",".join(salah[:10]))
            baris.append(f"{k}={len(sql_ids)}/Rp{sql_v:,.0f}")
        # baris daftar TANPA filter: aksi null untuk arsip
        r = await SO.list_sales_orders(None, status="cancelled", customer_id=None, start_date=None, end_date=None, search=None,
                                       skip=0, limit=100, tugas=None, sort_by=None, sort_dir="desc", aksi=None)
        cek(all(x.aksi is None for x in r.items), f"{t}: arsip (cancelled) aksi null", f"{len(r.items)} baris")
        print(f"RINGKAS {t} hari={hari} " + " ".join(baris))
        # rincian kartu uang vs qty
        tagih = {i for i, r in ind.items() if "perlu_ditagih" in r["aksi"]}
        hanya_uang = [i for i in unin if i not in tagih]
        hanya_qty = [i for i in tagih if i not in set(unin)]
        print(f"RINGKAS {t} uninvoiced-uang={len(unin)} perlu_ditagih-qty={len(tagih)} hanya-uang={len(hanya_uang)} hanya-qty={len(hanya_qty)}")
        async with baca.acquire() as c:
            for i in hanya_uang + hanya_qty:
                so = ind[i]
                d = await c.fetchrow(
                    """SELECT (SELECT count(*) FROM sales_order_items WHERE sales_order_id=$1) AS n_baris,
                              (SELECT count(*) FROM sales_invoices WHERE sales_order_id=$1 AND status NOT IN ('draft','void')) AS n_faktur,
                              (SELECT count(*) FROM sales_invoices WHERE sales_order_id=$1 AND status='draft') AS n_draf,
                              (SELECT COALESCE(SUM(total_amount),0) FROM sales_invoices WHERE sales_order_id=$1 AND status NOT IN ('draft','void')) AS tot_faktur""", i)
                print(f"JSON {'uang' if i in hanya_uang else 'qty'} {t} {so['order_number']} {so['status']} total={so['total_amount']} "
                      f"aksi={list(so['aksi'])} baris={d['n_baris']} faktur={d['n_faktur']} draf={d['n_draf']} Σfaktur={d['tot_faktur']}")
    print(f"RINGKAS gagal={gagal}")


asyncio.run(main())
