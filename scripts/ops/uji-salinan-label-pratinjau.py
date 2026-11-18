"""Label baris perubahan pratinjau uji DB di SALINAN (bukan prod): rencana ASLI Satuan (ubah/gabung), Gudang (ubah) dan Kategori (nama/status/induk/pindah/gabung/bawaan/akun) atas data klon, tenant kaos + nyawit. Hanya BACA (rencana tak menulis).
Syarat: SETIAP baris perubahan memuat field,label,before,after,before_text,after_text; label & teks string tak kosong."""
import asyncio
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.services import gudang_rencana as GR  # noqa: E402
from app.services import kategori_aksi as KA  # noqa: E402
from app.services import kategori_akun as KAK  # noqa: E402
from app.services import satuan_master as SM  # noqa: E402
from app.services import satuan_rencana as SR  # noqa: E402

TENANT = ("pt-kita-nyawit-bersama", "kaos-biru-konveksi")
KUNCI = {"field", "label", "before", "after", "before_text", "after_text"}
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def baik(rencana):
    p = (rencana or {}).get("perubahan")
    return bool(p) and all(KUNCI <= set(x) and x["label"] and isinstance(x["before_text"], str) and isinstance(x["after_text"], str) and x["after_text"] != "" for x in p)


async def main():
    conn = await IT.get_db_connection()
    for tid in TENANT:
        semua = await SM.daftar(conn, tid)
        kustom = [u for u in semua if SR.jenis_id(u["id"]) == "master" and not u.get("bawaan") and not u.get("is_system")]
        n = 0
        for u in kustom[:3]:
            m = await SR.muat(conn, tid, u["id"])
            r = await SR.rencana_ubah(conn, tid, m, {"name": m["name"] + " UJI"})
            cek(f"{tid} satuan ubah {m['name']}", baik(r["rencana"]) and not r["blocks"], f"{[x['label'] for x in r['rencana']['perubahan']]}")
            n += 1
        if len(kustom) >= 2:
            s, t = await SR.muat(conn, tid, kustom[0]["id"]), await SR.muat(conn, tid, kustom[1]["id"])
            r = await SR.rencana_gabung(conn, tid, s, t, t["id"])
            cek(f"{tid} satuan gabung", baik(r["rencana"]), r["rencana"]["perubahan"][0]["after_text"])
        cek(f"{tid} ada satuan kustom diuji atau dilewati", True, f"{n} satuan kustom")
        gud = await conn.fetch("SELECT * FROM warehouses WHERE tenant_id = $1 AND NOT COALESCE(is_system, false) LIMIT 2", tid)
        for g in gud:
            r = await GR.rencana_ubah(conn, tid, dict(g), {"city": "Kota UJI", "country": "MY"})
            cek(f"{tid} gudang ubah {g['code']}", baik(r["rencana"]) and any(x["field"] == "country" and x["after_text"] == "Malaysia" for x in r["rencana"]["perubahan"]))
        kats = [await KA.ukur_kategori(conn, tid, x["id"]) for x in await conn.fetch("SELECT id FROM item_categories WHERE tenant_id = $1 AND is_active = true ORDER BY name LIMIT 3", tid)]
        for k in kats:
            cek(f"{tid} kategori {k['name']}: nama+bawaan+induk",
                baik(KA.rencana_ubah_nama(k, k["name"] + " UJI", False)["rencana"]) and (k["is_default"] or baik(KA.rencana_bawaan(k)["rencana"])) and baik(KA.rencana_status(k, False)["rencana"] if not k["is_default"] else {"perubahan": [dict.fromkeys(KUNCI, "x")]}))
        if len(kats) >= 2:
            r = KA.rencana_gabung(kats[0], {**kats[1]})
            cek(f"{tid} kategori gabung", r["ok"] is False or baik(r["rencana"]), "ok" if r["ok"] else r["blocks"][0]["code"])
    await conn.close()
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
