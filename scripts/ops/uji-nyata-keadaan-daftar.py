"""UJI NYATA KEADAAN-LIST (16 Okt 2026): SELECT-only, grapgrap+kaos. SO: fakta_daftar -> kirim_keadaan cocok ship_text/kirim_hari/
kirim_terbuka tiap baris. SI: jatuh_tempo_keadaan cocok jatuh_tempo_terbuka/_hari (terbuka = predikat router: posted/partial & sisa AR>0)."""
import asyncio, os, sys
from collections import Counter
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from app.services import so_posisi as SP, faktur_daftar as FD

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
HARI = date.today()
gagal = []


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    for t in TEN:
        rows = await c.fetch("SELECT id, status, total_amount, expected_ship_date, quote_id, dp_percent, dp_amount FROM sales_orders WHERE tenant_id=$1", t)
        rows = [dict(r) for r in rows]
        try:
            f = await SP.fakta_daftar(c, t, rows, HARI)
        except TypeError:
            f = await SP.fakta_daftar(c, t, [dict(r) for r in rows], HARI)
        dist = Counter()
        for k, v in f.items():
            kd, teks, hari, terbuka = v["kirim_keadaan"], v["ship_text"], v["kirim_hari"], v["kirim_terbuka"]
            dist[kd] += 1
            if terbuka and hari is not None:
                ok = kd == ("telat" if hari < 0 else "hari_ini" if hari == 0 else "akan") and (kd == "telat") == teks.endswith(", telat")
            elif teks == "—":
                ok = kd in ("tanpa", "selesai")
            else:
                ok = teks == "Terkirim" and kd in ("selesai", "tanpa")
            if not ok:
                gagal.append((t, k, kd, teks, hari, terbuka))
        print(t, "SO", len(f), dict(dist))
        si = await c.fetch("""SELECT si.status, si.due_date,
              (si.status IN ('posted','partial') AND si.id IN (SELECT invoice_id FROM compute_ar_outstanding($1) WHERE outstanding > 0)) AS terbuka
              FROM sales_invoices si WHERE si.tenant_id=$1""", t)
        d2 = Counter()
        for r in si:
            kd = FD.jatuh_tempo_keadaan(r["status"], bool(r["terbuka"]), r["due_date"], HARI)
            hari = FD.jatuh_tempo_hari(bool(r["terbuka"]), r["due_date"], HARI)
            d2[kd] += 1
            overdue = bool(r["terbuka"]) and hari is not None and hari < 0
            if (kd == "telat") != (overdue and r["status"] not in ("draft", "void")):
                gagal.append((t, "SI", r["status"], r["due_date"], kd))
        print(t, "SI", len(si), dict(d2))
    await c.close()
    print("RINGKAS GAGAL=%d %s" % (len(gagal), gagal[:5]))
    sys.exit(1 if gagal else 0)

asyncio.run(main())
