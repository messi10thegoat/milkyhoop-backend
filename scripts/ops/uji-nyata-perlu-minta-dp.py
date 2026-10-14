"""Harness NYATA PERLU-MINTA-DP (READ ONLY): kode cabang vs SQL INDEPENDEN + kartu dashboard = pill (kaos + grapgrap).
Opsional /out/lama.json (aksi utama kode master sebelumnya) untuk daftar perubahan kolom aksi."""
import asyncio, json, os, sys
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.services import so_aksi, dashboard_v2
from backend.api_gateway.app.utils.tanggal_tenant import tanggal_dokumen

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1

MINTA_IND = """
SELECT so.id FROM sales_orders so
WHERE so.tenant_id = $1 AND so.status NOT IN ('draft','cancelled','completed')
  AND (COALESCE(so.dp_percent,0) > 0 OR COALESCE(so.dp_amount,0) > 0)
  AND NOT EXISTS (SELECT 1 FROM proformas p WHERE p.tenant_id = so.tenant_id AND p.sales_order_id = so.id AND p.status NOT IN ('draft','cancelled'))
  AND NOT EXISTS (SELECT 1 FROM customer_deposits cd LEFT JOIN proformas pf ON pf.id = cd.proforma_id AND pf.tenant_id = cd.tenant_id
                  WHERE cd.tenant_id = so.tenant_id AND COALESCE(cd.sales_order_id, pf.sales_order_id) = so.id
                    AND cd.status NOT IN ('void','draft','refunded'))
  AND NOT EXISTS (SELECT 1 FROM sales_invoices si WHERE si.tenant_id = so.tenant_id AND si.sales_order_id = so.id AND si.status NOT IN ('draft','void'))"""
HARAP = {"kaos-biru-konveksi": (13, 14), "grapgrap-manado": (0, 4)}
PELUNASAN_KAOS = {"SO-2609-0330", "SO-2610-0003", "SO-2610-0004"}

async def main():
    lama = json.load(open("/out/lama.json")) if os.path.exists("/out/lama.json") else {}
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await c.execute("BEGIN READ ONLY")
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        hari = await tanggal_dokumen(c, t)
        no = {r["id"]: r["order_number"] for r in await c.fetch("SELECT id, order_number FROM sales_orders WHERE tenant_id=$1", t)}
        f = await so_aksi.fakta(c, t, hari)
        minta = set(so_aksi.id_aksi(f, "perlu_minta_dp")); tunggu = set(so_aksi.id_aksi(f, "menunggu_dp"))
        ind = {r["id"] for r in await c.fetch(MINTA_IND, t)}   # tanpa syarat sisa>0 (diperiksa terpisah)
        print(f"== {t}")
        cek(minta == ind, f"{t} perlu_minta_dp kode == SQL independen", f"kode-ind={sorted(no[i] for i in minta-ind)} ind-kode={sorted(no[i] for i in ind-minta)}")
        cek(len(minta) == HARAP[t][0], f"{t} perlu_minta_dp == {HARAP[t][0]}", str(len(minta)))
        cek(not (minta & tunggu), f"{t} perlu_minta_dp ∩ menunggu_dp kosong")
        if t == "kaos-biru-konveksi":
            cek(not ({no[i] for i in minta} & PELUNASAN_KAOS), "kaos 3 SO ber-proforma PELUNASAN TIDAK masuk")
        print("  daftar perlu_minta_dp (aksi lama -> baru, nilai):")
        for i in sorted(minta, key=lambda x: no[x]):
            print(f"    {no[i]}: {lama.get(t, {}).get(no[i])} -> {so_aksi.aksi_utama(f[i]['aksi'])}  DP {f[i]['nilai']['perlu_minta_dp']}")
        r = so_aksi.ringkasan(f)
        tugas = await dashboard_v2.tugas_tenant(c, t, hari)
        kartu = [x for x in tugas if x["type"] == "so_dp_pending"]
        ref = {x["number"] for x in kartu[0]["refs"]} if kartu else set()
        nilai_kartu = kartu[0]["amount"] if kartu else None
        cek(ref == {no[i] for i in minta | tunggu} and len(ref) == HARAP[t][1], f"{t} kartu dashboard == menunggu_dp ∪ perlu_minta_dp ({HARAP[t][1]})", f"{len(ref)}")
        cek(set(await dashboard_v2.id_tugas(c, t, "dp_belum_diterima", hari)) == minta | tunggu, f"{t} ?tugas=dp_belum_diterima == gabungan")
        tot = r["menunggu_dp_value"] + r["perlu_minta_dp_value"]
        print(f"  summary menunggu_dp ({r['menunggu_dp_count']}, {r['menunggu_dp_value']}) perlu_minta_dp ({r['perlu_minta_dp_count']}, {r['perlu_minta_dp_value']}) kartu amount={nilai_kartu}")
        cek(kartu and abs(float(str(nilai_kartu).replace(',', '')) - tot) < 0.005 if isinstance(nilai_kartu, (int, float, str)) else False,
            f"{t} nilai kartu == Σ summary _value DP", f"{nilai_kartu} vs {tot}")
        print("  summary semua:", {k: r[f"{k}_count"] for k in so_aksi.AKSI})
    await c.execute("ROLLBACK")
    print(f"RINGKAS gagal={gagal}")
asyncio.run(main())
