"""Harness NYATA DASBOR-SATU-SUMBER (READ ONLY, 15 Okt 2026): kartu so_to_ship == pill telat|dikirim|siap ∩ (tanggal kirim
<= akhir minggu ATAU kosong) == ?tugas=harus_kirim (kaos + grapgrap). Harapan MASTER: grapgrap 21, kaos 15."""
import asyncio, os, sys
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.services import so_aksi, dashboard_v2 as dv
from backend.api_gateway.app.utils.tanggal_tenant import tanggal_dokumen

HARAP = {"grapgrap-manado": 21, "kaos-biru-konveksi": 15}
gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await c.execute("BEGIN READ ONLY")
    try:
        for t, harap in HARAP.items():
            hi = await tanggal_dokumen(c, t)
            batas = dv.akhir_minggu(hi)
            so = {r["id"]: r for r in await c.fetch(
                "SELECT id, order_number, status, expected_ship_date FROM sales_orders WHERE tenant_id=$1", t)}
            f = await so_aksi.fakta(c, t, hi)
            pill = set()
            for k in ("telat_kirim", "perlu_dikirim", "siap_diserahkan"):
                pill |= set(so_aksi.id_aksi(f, k))   # = ?aksi=<k>
            pasangan = {i for i in pill if so[i]["expected_ship_date"] is None or so[i]["expected_ship_date"] <= batas}
            kartu_rows = await dv.so_harus_kirim(c, t, hi)
            kartu = {r["id"] for r in kartu_rows}
            tugas = set(await dv.id_tugas(c, t, "harus_kirim", hi))
            cek(kartu == pasangan, f"{t} kartu == pill∩jendela (pasangan dari DB)", f"{len(kartu)} vs {len(pasangan)}")
            cek(tugas == kartu, f"{t} ?tugas=harus_kirim == kartu")
            cek(len(kartu) == harap, f"{t} kartu == {harap}", str(len(kartu)))
            semua = await dv.tugas_tenant(c, t, hi)
            k1 = [x for x in semua if x["type"] == "so_to_ship"]
            cek(len(k1) == 1 and {r["id"] for r in k1[0]["refs"]} == {str(i) for i in kartu},
                f"{t} /tasks so_to_ship refs == kartu", k1[0]["title_html"] + " | " + k1[0]["subtitle"] if k1 else "-")
            n_telat = len(kartu & set(so_aksi.id_aksi(f, "telat_kirim")))
            cek((f"· {n_telat} lewat tanggal kirim" in k1[0]["subtitle"]) if n_telat else ("lewat" not in k1[0]["subtitle"]),
                f"{t} subjudul lewat == |telat ∩ kartu| = {n_telat}")
            cek(not any(so[i]["expected_ship_date"] and so[i]["expected_ship_date"] > batas for i in kartu),
                f"{t} tak ada tanggal sesudah minggu ini")
            print("JSON", t, "kosong", sum(1 for i in kartu if so[i]["expected_ship_date"] is None),
                  "status", sorted({so[i]["status"] for i in kartu}))
    finally:
        await c.execute("ROLLBACK")
        await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
