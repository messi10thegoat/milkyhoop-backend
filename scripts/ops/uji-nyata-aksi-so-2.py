"""Harness NYATA AKSI-SO-2 (READ ONLY): kode cabang vs SQL INDEPENDEN, kaos + grapgrap."""
import asyncio, os, sys
from decimal import Decimal
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.services import so_aksi, so_posisi, so_agregat
from backend.api_gateway.app.utils.tanggal_tenant import tanggal_dokumen

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1

SISA_KIRIM = """
WITH kirim AS (
  SELECT sii.sales_order_item_id AS soi, SUM(ifi.quantity) q
  FROM invoice_fulfillment_items ifi
  JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.voided_at IS NULL AND f.status <> 'voided'
  JOIN sales_invoice_items sii ON sii.id = ifi.invoice_item_id
  JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = f.tenant_id
  WHERE f.tenant_id = $1 GROUP BY 1
  UNION ALL
  SELECT ifi.sales_order_item_id, SUM(ifi.quantity) FROM invoice_fulfillment_items ifi
  JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.voided_at IS NULL AND f.status <> 'voided'
  WHERE f.tenant_id = $1 AND f.asal = 'so' GROUP BY 1)
SELECT DISTINCT so.id FROM sales_orders so
JOIN sales_order_items soi ON soi.sales_order_id = so.id
LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
WHERE so.tenant_id = $1 AND so.status NOT IN ('draft','cancelled','completed')
  AND COALESCE(soi.perlu_kirim, p.track_inventory, false)
  AND soi.quantity > COALESCE((SELECT SUM(k.q) FROM kirim k WHERE k.soi = soi.id), 0)"""

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await c.execute("BEGIN READ ONLY")
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        hari = await tanggal_dokumen(c, t)
        rows = {r["id"]: dict(r) for r in await c.fetch(
            "SELECT id, order_number, status, total_amount, expected_ship_date, quote_id, fase_lokasi, fase_gudang_id FROM sales_orders WHERE tenant_id=$1", t)}
        no = lambda s: sorted(rows[i]["order_number"] for i in s)
        f = await so_aksi.fakta(c, t, hari)
        kode = {k: set(so_aksi.id_aksi(f, k)) for k in so_aksi.AKSI}
        jalan = {i for i, r in rows.items() if r["status"] not in ("draft", "cancelled", "completed")}
        sisa = {r["id"] for r in await c.fetch(SISA_KIRIM, t)}
        tersedia = {r["id"] for r in await c.fetch(
            "SELECT id FROM sales_orders WHERE tenant_id=$1 AND fase_lokasi='tersedia' AND status NOT IN ('draft','cancelled','completed')", t)}
        # menunggu_dp independen: proforma DP issued; uang muka hidup ber-jurnal yang menaut proforma ITU atau SO tanpa proforma (kolam) < nominal; SO berjalan
        dp_ind = {r["sid"] for r in await c.fetch("""
            SELECT DISTINCT pf.sales_order_id sid FROM proformas pf JOIN sales_orders so ON so.id = pf.sales_order_id AND so.tenant_id = pf.tenant_id
            WHERE pf.tenant_id=$1 AND pf.status='issued' AND pf.purpose='DP' AND so.status NOT IN ('draft','cancelled','completed')
              AND pf.amount > COALESCE((SELECT SUM(cd.amount) FROM customer_deposits cd WHERE cd.tenant_id=$1
                                        AND (cd.proforma_id=pf.id OR (cd.proforma_id IS NULL AND cd.sales_order_id=so.id))
                                        AND cd.status NOT IN ('void','draft') AND cd.journal_id IS NOT NULL),0)""", t)}
        # kolom Posisi (kode cabang, fase dikosongkan) = teks "Menunggu DP"
        pos = await so_posisi.fakta_daftar(c, t, [dict(r, fase_lokasi=None) for r in rows.values()], hari)
        dp_pos = {i for i in jalan if pos[str(i)]["position_text"] == "Menunggu DP"}
        ind = {"telat_kirim": {i for i in sisa if rows[i]["expected_ship_date"] and rows[i]["expected_ship_date"] < hari},
               "menunggu_dp": dp_ind, "perlu_dikonfirmasi": {i for i, r in rows.items() if r["status"] == "draft"},
               "perlu_dikirim": sisa - tersedia, "siap_diserahkan": sisa & tersedia}
        print(f"== {t} hari={hari}")
        print("  kunci | kode | SQL independen")
        for k in so_aksi.AKSI:
            print(f"  {k} | {len(kode[k])} | {len(ind[k]) if k in ind else '-'}")
            if k in ind:
                cek(kode[k] == ind[k], f"{t} {k} kode == SQL independen", f"kode-ind={no(kode[k]-ind[k])} ind-kode={no(ind[k]-kode[k])}")
        cek(kode["menunggu_dp"] == dp_pos, f"{t} menunggu_dp == kolom Posisi 'Menunggu DP' (tanpa fase)", str(no(dp_pos)))
        cek(not (kode["perlu_dikirim"] & kode["siap_diserahkan"]), f"{t} perlu_dikirim ∩ siap_diserahkan kosong")
        cek(kode["siap_diserahkan"] <= set(await so_agregat.id_tersedia(c, t)), f"{t} siap_diserahkan ⊂ ?tugas=tersedia")
        print("  kolom aksi menunggu_dp/siap:", {rows[i]["order_number"]: so_aksi.aksi_utama(f[i]["aksi"]) for i in kode["menunggu_dp"] | kode["siap_diserahkan"]})
        r = so_aksi.ringkasan(f)
        print("  summary:", {k: (r[f"{k}_count"], r[f"{k}_value"]) for k in so_aksi.AKSI})
        if t == "grapgrap-manado":
            cek(set(no(kode["menunggu_dp"])) == {"SO-2609-0009", "SO-2609-0017", "SO-2609-0019", "SO-2610-0014"},
                "grapgrap ?aksi=menunggu_dp == 4 SO MASTER", str(no(kode["menunggu_dp"])))
    await c.execute("ROLLBACK")
    print(f"RINGKAS gagal={gagal}")
asyncio.run(main())
