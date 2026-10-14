"""Harness NYATA DEFINISI-TAGIH (READ ONLY, 14 Okt 2026): perlu_ditagih = NetSuite Pending Billing; telat|dikirim|siap =
partisi bersih; kartu ?tugas=menunggu_tagih = pill perlu_ditagih (kaos + grapgrap).
MODE=dump -> tulis /out/lama.json (aksi utama + anggota kunci, kode yang di-mount) untuk banding sebelum/sesudah."""
import asyncio, json, os, sys
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.services import so_aksi, dashboard_v2
from backend.api_gateway.app.utils.tanggal_tenant import tanggal_dokumen

TENANT = ("grapgrap-manado", "kaos-biru-konveksi")
K = ("telat_kirim", "perlu_dikirim", "siap_diserahkan", "perlu_ditagih")
gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await c.execute("BEGIN READ ONLY")
    dump = {}
    try:
        lama = json.load(open("/out/lama.json")) if os.environ.get("MODE") != "dump" and os.path.exists("/out/lama.json") else {}
        for t in TENANT:
            hi = await tanggal_dokumen(c, t)
            nomor = {r["id"]: r["order_number"] for r in await c.fetch(
                "SELECT id, order_number FROM sales_orders WHERE tenant_id = $1", t)}
            f = await so_aksi.fakta(c, t, hi)
            ang = {k: {nomor[i] for i in so_aksi.id_aksi(f, k)} for k in K}
            utama = {nomor[i]: so_aksi.aksi_utama(v["aksi"]) for i, v in f.items()}
            r = so_aksi.ringkasan(f)
            dump[t] = {"utama": utama, "anggota": {k: sorted(v) for k, v in ang.items()},
                       "nilai": {k: r[k + "_value"] for k in K}}
            print("JSON", t, str(hi), json.dumps({k: (len(ang[k]), r[k + "_value"]) for k in K}))
            if os.environ.get("MODE") == "dump":
                continue
            for a, b in (("telat_kirim", "perlu_dikirim"), ("telat_kirim", "siap_diserahkan"),
                         ("perlu_dikirim", "siap_diserahkan")):
                cek(not (ang[a] & ang[b]), f"{t} {a}∩{b} kosong", str(sorted(ang[a] & ang[b])))
            kartu = {nomor[i] for i in await dashboard_v2.id_tugas(c, t, "menunggu_tagih", hi)}
            cek(kartu == ang["perlu_ditagih"], f"{t} kartu menunggu_tagih == pill perlu_ditagih", f"{len(kartu)}")
            flag = await c.fetchval("SELECT count(*) FROM tenant_features WHERE tenant_id=$1 AND feature=$2 AND enabled",
                                    t, so_aksi.FLAG_TAGIH_SEBELUM_KIRIM)
            cek(flag == 0, f"{t} flag {so_aksi.FLAG_TAGIH_SEBELUM_KIRIM} MATI (bawaan)")
            if t == "grapgrap-manado":
                cek(ang["perlu_ditagih"] == {"SO-2609-0027"}, "grapgrap perlu_ditagih == {SO-2609-0027}",
                    str(sorted(ang["perlu_ditagih"])))
                cek(not (ang["perlu_dikirim"] & ang["perlu_ditagih"]), "grapgrap perlu_dikirim∩perlu_ditagih kosong")
                cek(not ({"SO-2610-0019", "SO-2610-0014"} & ang["perlu_ditagih"]), "grapgrap SO-2610-0019/-0014 bukan perlu_ditagih")
            else:
                cek("SO-2609-0273" in ang["perlu_dikirim"] & ang["perlu_ditagih"],
                    "kaos SO-2609-0273 di perlu_dikirim∩perlu_ditagih (Partially Fulfilled)")
            if t in lama:
                ubah = {n: (lama[t]["utama"].get(n), u) for n, u in utama.items() if lama[t]["utama"].get(n) != u}
                print("JSON", t, "kolom_aksi_berubah", json.dumps(ubah))
                for k in K:
                    print("JSON", t, k, len(lama[t]["anggota"][k]), "->", len(ang[k]),
                          lama[t]["nilai"][k], "->", r[k + "_value"],
                          "keluar", sorted(set(lama[t]["anggota"][k]) - ang[k]),
                          "masuk", sorted(ang[k] - set(lama[t]["anggota"][k])))
    finally:
        await c.execute("ROLLBACK")
        await c.close()
    if os.environ.get("MODE") == "dump":
        json.dump(dump, open("/out/lama.json", "w"))
    print(f"RINGKAS gagal={gagal}")

asyncio.run(main())
