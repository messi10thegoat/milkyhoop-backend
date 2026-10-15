"""Harness NYATA PELUNASAN-TEKS (15 Okt 2026), BACA saja (BEGIN READ ONLY) grapgrap + kaos: untuk SETIAP penerimaan berjenis
efektif PELUNASAN -> riwayat proforma-nya, riwayat faktur yang diterapkan, dan Rekap SO-nya menyebut "Pelunasan {nomor}" dan
TIDAK "Uang muka {nomor}"; kontrol: penerimaan DP tetap "Uang muka {nomor}". label_deposit == jenis efektif SQL."""
import asyncio, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from backend.api_gateway.app.services import so_riwayat as R, rekap_pesanan as RK, deposit_jenis as DJ

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1

async def boleh(_m):
    return True

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await c.execute("BEGIN READ ONLY")
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            deps = await c.fetch(f"""SELECT cd.id, cd.deposit_number, cd.proforma_id, cd.status, cd.journal_id,
                       COALESCE(cd.sales_order_id, (SELECT p.sales_order_id FROM proformas p WHERE p.id = cd.proforma_id)) AS so,
                       {DJ.sql_jenis_efektif('cd')} AS jenis FROM customer_deposits cd WHERE cd.tenant_id = $1""", t)
            salah_label = 0
            for d in deps:
                salah_label += await DJ.label_deposit(c, t, d["id"]) != DJ.label_jenis(d["jenis"])
            cek(salah_label == 0, f"{t}: label_deposit == jenis efektif untuk {len(deps)} penerimaan")
            n_pel = n_cek = buruk = 0
            for d in deps:
                no, lb = d["deposit_number"], DJ.label_jenis(d["jenis"])
                salah = "Uang muka" if d["jenis"] == "PELUNASAN" else "Pelunasan"
                teks = []
                if d["proforma_id"]:
                    rp = await R.riwayat_proforma(c, t, d["proforma_id"], boleh)
                    teks += [e.get("summary") or e.get("ringkas") or json.dumps(e, default=str) for e in (rp or {}).get("events", [])]
                for a in await c.fetch("SELECT invoice_id FROM customer_deposit_applications WHERE deposit_id = $1 AND tenant_id = $2", d["id"], t):
                    rf = await R.riwayat_faktur(c, t, a["invoice_id"], boleh)
                    teks += [json.dumps(e, default=str) for e in (rf or {}).get("events", [])]
                if d["so"] and d["status"] != "void" and d["journal_id"]:
                    rk = await RK.muat_rekap_pesanan(c, {"tenant_id": t, "user_id": uid}, d["so"])
                    teks.append(json.dumps(rk, default=str))
                semua = " ".join(teks)
                if no and no in semua:
                    n_cek += 1
                    n_pel += d["jenis"] == "PELUNASAN"
                    if f"{salah} {no}" in semua:
                        buruk += 1; print("JSON salah label", t, no, d["jenis"])
            cek(buruk == 0, f"{t}: {n_cek} penerimaan muncul di riwayat/Rekap ({n_pel} pelunasan), label sesuai jenis", f"salah {buruk}")
    finally:
        await c.execute("ROLLBACK"); await c.close()
    print("RINGKAS gagal=%d" % gagal)
asyncio.run(main())
