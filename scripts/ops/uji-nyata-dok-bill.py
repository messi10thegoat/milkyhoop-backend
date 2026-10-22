"""UJI NYATA paritas dokumen tagihan antarbundel (22 Okt 2026): BACA-SAJA. Mode: baru
Untuk SETIAP tagihan nyata (kaos + grapgrap): dokumen versi bundel PO / uang muka vendor (po_daftar.sql_dok_bill + dok_bill) ==
dokumen tagihan itu di bundelnya sendiri (tagihan_daftar.kelompok_tagihan, kelompok 'faktur'). Termasuk tagihan LUNAS (dulu "diposting").
Juga: PD.dokumen_po + UMD.kelompok_untuk berjalan di skema nyata atas semua PO / uang muka vendor (SQL terurai)."""
import asyncio, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from app.services import po_daftar as PD, tagihan_daftar as TD, uang_muka_vendor_daftar as UMD

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok: gagal.append(nama)


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        rows = await c.fetch(f"SELECT {PD.sql_dok_bill()} FROM bills b {TD.join_rincian()} WHERE b.tenant_id = $1 ORDER BY b.id", t)
        ids = [r["id"] for r in rows]
        bundel = {}
        for i in range(0, len(ids), 100):
            bundel.update(await TD.kelompok_tagihan(c, t, ids[i:i + 100]))
        beda, status = [], {}
        for r in rows:
            a = PD.dok_bill(r)
            b = next(g["docs"][0] for g in bundel[r["id"]] if g["key"] == "faktur")
            status[a["status"]] = status.get(a["status"], 0) + 1
            if a != b:
                beda.append((r["invoice_number"], a.get("status"), b.get("status")))
        print(f"INFO {t}: {len(rows)} tagihan, status {status}")
        cek(f"{t}: dok tagihan bundel PO/UM == bundel tagihan ({len(rows)} tagihan)", not beda, beda[:5])
        po = [r["id"] for r in await c.fetch("SELECT id FROM purchase_orders WHERE tenant_id = $1", t)]
        d = await PD.dokumen_po(c, t, po)
        cek(f"{t}: dokumen_po berjalan ({len(po)} PO)", len(d) == len(po))
        vd = [r["id"] for r in await c.fetch("SELECT id FROM vendor_deposits WHERE tenant_id = $1", t)]
        k = await UMD.kelompok_untuk(c, t, vd)
        cek(f"{t}: kelompok_untuk UM vendor berjalan ({len(vd)} UM)", len(k) == len(vd))
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
