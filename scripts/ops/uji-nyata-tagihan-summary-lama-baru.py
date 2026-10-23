"""UJI NYATA paritas /bills/summary LAMA (pohon utama = kode live) vs BARU (cabang) -- 23 Okt 2026, segera_tempo aditif. BACA-SAJA
(transaksi READ ONLY), grapgrap + kaos, beberapa "hari ini". Mode TULIS=<berkas>: cetak JSON summary ke berkas (dijalankan dua kali,
sekali per pohon). Mode BANDING=<lama>,<baru>: SEMUA kunci lama harus SAMA PERSIS kecuali yang memang berubah definisinya
(jatuh_tempo_minggu_ini_* = alias segera_tempo, N hari; putusan pemilik KONTRAK-dashboard-pengingat §7); kunci BARU hanya segera_tempo_*.
MERAH=1 (BANDING): satu nilai lama diubah -> WAJIB gagal."""
import asyncio, json, os, sys
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
BOLEH_BEDA = {"jatuh_tempo_minggu_ini_count", "jatuh_tempo_minggu_ini_value"}
BARU_SAH = {"segera_tempo_count", "segera_tempo_value"}


async def tulis(berkas):
    import asyncpg
    from app.services import bills_service as BS
    from app.utils.tanggal_tenant import tanggal_dokumen as asli
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    svc = BS.BillsService(P())
    out = {}
    try:
        for t in TEN:
            h0 = await asli(c, t)
            for H in (h0, date(2026, 9, 15), date(2026, 10, 1)):
                async def palsu(conn, tid, _H=H): return _H
                BS.tanggal_dokumen = palsu
                for per in ("current_year", "current_month"):
                    out[f"{t}@{H}@{per}"] = (await svc.get_summary(t, per))["data"]
    finally:
        await tx.rollback(); await c.close()
    json.dump(out, open(berkas, "w"), default=str, sort_keys=True)
    print(f"TULIS {len(out)} summary -> {berkas}")


def banding(lama_p, baru_p):
    lama, baru = json.load(open(lama_p)), json.load(open(baru_p))
    if os.environ.get("MERAH") == "1":
        k0 = next(iter(lama)); lama[k0]["lewat_jatuh_tempo_count"] = lama[k0]["lewat_jatuh_tempo_count"] + 1
    gagal = []
    for L in sorted(set(lama) | set(baru)):
        a, b = lama.get(L), baru.get(L)
        if a is None or b is None:
            gagal.append((L, "hilang")); continue
        beda = sorted(k for k in a if k not in BOLEH_BEDA and a[k] != b.get(k))
        hilang = sorted(set(a) - set(b))
        tambah = sorted(set(b) - set(a))
        alias_ok = b["jatuh_tempo_minggu_ini_count"] == b["segera_tempo_count"] and b["jatuh_tempo_minggu_ini_value"] == b["segera_tempo_value"]
        ok = not beda and not hilang and set(tambah) == BARU_SAH and alias_ok
        print(("PASS " if ok else "FAIL ") + f"{L} kunci lama sama (kecuali alias), baru = segera_tempo_*"
              + f" | beda {beda} hilang {hilang} tambah {tambah} alias_ok {alias_ok}"
              + f" minggu_ini lama {a['jatuh_tempo_minggu_ini_count']}/{a['jatuh_tempo_minggu_ini_value']} -> segera {b['segera_tempo_count']}/{b['segera_tempo_value']}")
        if not ok:
            gagal.append(L)
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)


if os.environ.get("BANDING"):
    banding(*os.environ["BANDING"].split(","))
else:
    asyncio.run(tulis(os.environ["TULIS"]))
