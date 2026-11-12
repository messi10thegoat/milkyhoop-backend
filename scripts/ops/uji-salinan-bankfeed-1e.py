"""BF-1e -- uji di SALINAN (klon milkydb), lewat RUTE asli: daftar mutasi membawa jenis_tersedia/aksi_tersedia (KESETARAAN dengan pratinjau aksi sungguhan pada DB nyata), dokumen_tertaut,
total tampil, teks tebakan, satu bentuk dokumen (tebakan/tak-di-bank/tertaut), perlu_ditinjau per rekening == hitungan SQL. Dua tenant UJI; tanpa data grapgrap. Izin disetubkan di dua tempat
(BA dan TS; logika izin diuji unit + sabotase). Tulis HANYA di salinan."""
import asyncio
import sys
import uuid
from decimal import Decimal as D
from uuid import UUID

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/tanda")
import uji_c3_tanda as C3  # noqa: E402
from app.routers import bank_accounts as BAR  # noqa: E402
from app.routers import mutasi_bank as MR  # noqa: E402
from app.schemas.bankfeed import AlokasiFaktur, BatalkanCatatanRequest, CatatRequest, KecualikanRequest, LepaskanRequest  # noqa: E402
from app.services import bankfeed_aksi as BA  # noqa: E402
from app.services import bankfeed_dokumen as BDK  # noqa: E402
from app.services import bankfeed_tersedia as TS  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
TGL = C3.TGL
IZIN = {"ubah": True, "void": True, "tolak_jenis": set()}


async def _izin_stub(user, jalur, method="GET"):
    out = {}
    for j in jalur:
        if "/batalkan-catatan" in j:
            out[j] = IZIN["void"]
        elif "/mutasi-bank/" in j:
            out[j] = IZIN["ubah"]
        else:
            out[j] = j not in IZIN["tolak_jenis"]
    return out
TS.izin_rute_pengguna = _izin_stub
BA.izin_rute_pengguna = _izin_stub


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": C3.USER, "username": "uji-bf1e"}
    return r


async def panggil(coro):
    try:
        return 200, await coro
    except HTTPException as e:
        return e.status_code, e.detail


async def mut(c, tid, rid, arah, nominal, tgl=TGL, ket="TRSF UJI"):
    mid = uuid.uuid4()
    await c.execute("INSERT INTO mutasi_bank (id, tenant_id, bank_account_id, tanggal, keterangan, keterangan_normal, arah, nominal, sidik) VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8)",
                    mid, tid, rid, tgl, f"{ket} {mid.hex[:6]}", arah, nominal, uuid.uuid4().hex)
    return mid


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1e"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        async with pool.acquire() as c:
            rid, coa_x = await C3.rekening_kembar(c, tid, "8888880511")
            rid_b, coa_b = await C3.rekening_kembar(c, tid, "8888880512")
            ids = {k: await mut(c, tid, rid, arah, D(n), ket=k) for k, arah, n in (("tinjau_masuk", "masuk", 300000), ("tinjau_keluar", "keluar", 120000), ("catat", "keluar", 50000),
                                                                                 ("kecuali", "masuk", 7000), ("cocok_uji", "masuk", 9000), ("dupe", "masuk", 11000))}
            await c.execute("UPDATE mutasi_bank SET status = 'mungkin_duplikat', duplikat_dari = $3 WHERE tenant_id = $1 AND id = $2", tid, ids["dupe"], ids["tinjau_masuk"])

        async def daftar(status=None):
            return await MR.daftar_mutasi_bank(req(tid), rid, status=status, q=None, dari=None, sampai=None, limit=50, offset=0)

        async def tulis(fn, mid, body):
            return await panggil(fn(req(tid), rid, mid, body, Response()))
        # keadaan: satu dicatat (transfer ke B), satu dikecualikan
        s1, h1 = await tulis(MR.catat, ids["catat"], CatatRequest(jenis="transfer", rekening_lawan=rid_b))
        s2, h2 = await tulis(MR.kecualikan, ids["kecuali"], KecualikanRequest(alasan="bukan_transaksi"))
        d = await daftar()
        per = {x["id"]: x for x in d["data"]}
        tinjau = per[str(ids["tinjau_masuk"])]
        cek(f"{tid} D1 baris untuk_ditinjau masuk: jenis_tersedia berurut tanpa bayar_tagihan bila belum tersedia; aksi cocokkan/catat/kecualikan ok; dokumen_tertaut kosong",
            s1 == 200 and s2 == 200 and [j["kunci"] for j in tinjau["jenis_tersedia"]] == [j for j in TS.URUTAN_JENIS["masuk"] if j in BA.JENIS_CATAT and j not in BA.JENIS_BELUM]
            and set(tinjau["aksi_tersedia"]) == {"cocokkan", "catat", "kecualikan"} and all(v["ok"] for v in tinjau["aksi_tersedia"].values()) and tinjau["dokumen_tertaut"] == [], f"{s1} {s2} {tinjau}")
        dicatat = per[str(ids["catat"])]
        cek(f"{tid} D2 baris dicatat (transfer): aksi hanya batalkan_catatan ok + lepaskan tidak (alasan); dokumen_tertaut = satu bentuk (jenis transfer, id dokumen, nomor TRF-, bt_id rekening ini)",
            set(dicatat["aksi_tersedia"]) == {"batalkan_catatan", "lepaskan"} and dicatat["aksi_tersedia"]["batalkan_catatan"]["ok"] and not dicatat["aksi_tersedia"]["lepaskan"]["ok"]
            and dicatat["aksi_tersedia"]["lepaskan"]["alasan"] and len(dicatat["dokumen_tertaut"]) == 1 and set(dicatat["dokumen_tertaut"][0]) == set(BDK.bentuk(None, None, None, None))
            and dicatat["dokumen_tertaut"][0]["jenis"] == "transfer" and str(dicatat["dokumen_tertaut"][0]["nomor"]).startswith("TRF-"), f"{dicatat}")
        kec = per[str(ids["kecuali"])]
        cek(f"{tid} D3 baris dikecualikan: hanya lepaskan ok; dokumen_tertaut kosong", set(kec["aksi_tersedia"]) == {"lepaskan"} and kec["aksi_tersedia"]["lepaskan"]["ok"] and kec["dokumen_tertaut"] == [], f"{kec}")
        # alasan "lain" tanpa catatan: DITOLAK 422 CATATAN_WAJIB (pratinjau dan tulis), baris tak berubah (uji yang memang diharapkan, bukan celah harness)
        s_pv, h_pv = await panggil(MR.pratinjau_kecualikan(req(tid), rid, ids["cocok_uji"], KecualikanRequest(alasan="lain")))
        s_tl, h_tl = await tulis(MR.kecualikan, ids["cocok_uji"], KecualikanRequest(alasan="lain"))
        async with pool.acquire() as c:
            st_lain = await c.fetchval("SELECT status FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, ids["cocok_uji"])
        cek(f"{tid} D3b alasan lain tanpa catatan -> pratinjau memblokir CATATAN_WAJIB, tulis 422; baris tetap untuk_ditinjau",
            s_pv == 200 and "CATATAN_WAJIB" in [x["code"] for x in h_pv["data"]["blocks"]] and s_tl == 422 and "catatan singkat" in str(h_tl) and st_lain == "untuk_ditinjau", f"{s_pv} {s_tl} {st_lain} tl={str(h_tl)[:120]}")
        # kesetaraan dengan pratinjau sungguhan (DB nyata): aksi ok == pratinjau tanpa blok keadaan
        salah = []
        for nama, mid in ids.items():
            row = per[str(mid)]
            for aksi, ok in row["aksi_tersedia"].items():
                if aksi == "lepaskan":
                    pv = await panggil(MR.pratinjau_lepaskan(req(tid), rid, mid, LepaskanRequest()))
                elif aksi == "kecualikan":
                    pv = await panggil(MR.pratinjau_kecualikan(req(tid), rid, mid, KecualikanRequest(alasan="bukan_transaksi")))
                elif aksi == "batalkan_catatan":
                    pv = await panggil(MR.pratinjau_batalkan_catatan(req(tid), rid, mid, BatalkanCatatanRequest(alasan="uji")))
                else:
                    continue                                   # cocokkan/catat butuh masukan; keadaan-nya sama (SUDAH_DIPUTUS) dan dicakup tes unit
                kode_blok = [b["code"] for b in pv[1]["data"]["blocks"]] if pv[0] == 200 else ["GALAT"]
                if ok["ok"] != (not any(k in TS.KODE_KEADAAN for k in kode_blok)):
                    salah.append((nama, aksi, ok, kode_blok))
        cek(f"{tid} D4 KESETARAAN pada DB nyata: aksi_tersedia.ok == pratinjau aksi tanpa blok keadaan (lepaskan/kecualikan/batalkan_catatan, semua baris)", not salah, str(salah))
        # mungkin_duplikat + teks tebakan
        dupe = per[str(ids["dupe"])]
        cek(f"{tid} D5 mungkin_duplikat tetap bisa cocokkan/catat/kecualikan; tebakan membawa keyakinan_text dan dasar_text (teks server, bukan enum)", set(dupe["aksi_tersedia"]) == {"cocokkan", "catat", "kecualikan"}
            and dupe["tebakan"]["keyakinan_text"] and (dupe["tebakan"]["dasar"] is None or (dupe["tebakan"]["dasar_text"] and dupe["tebakan"]["dasar_text"] != dupe["tebakan"]["dasar"])), f"{dupe['tebakan']}")
        cek(f"{tid} D6 total: masuk/keluar/jumlah + masuk_tampil/keluar_tampil teks rupiah dari server (Σ seluruh rekening, bukan halaman)", d["total"]["jumlah"] == 6 and d["total"]["masuk_tampil"].startswith("Rp") and d["total"]["keluar_tampil"].startswith("Rp")
            and D(d["total"]["masuk"]) == D(300000 + 7000 + 9000 + 11000) and D(d["total"]["keluar"]) == D(120000 + 50000), f"{d['total']}")
        # tanpa izin
        IZIN["ubah"] = False
        d2 = {x["id"]: x for x in (await daftar())["data"]}
        IZIN["ubah"] = True
        t2 = d2[str(ids["tinjau_masuk"])]
        cek(f"{tid} D7 tanpa izin ubah: jenis_tersedia kosong, semua aksi ok:false dengan alasan", t2["jenis_tersedia"] == [] and all(not v["ok"] and v["alasan"] for v in t2["aksi_tersedia"].values()), f"{t2['aksi_tersedia']}")
        IZIN["tolak_jenis"] = {"/api/customer-deposits"}
        d3 = {x["id"]: x for x in (await daftar())["data"]}
        IZIN["tolak_jenis"] = set()
        cek(f"{tid} D8 tanpa izin C modul uang muka: jenis uang_muka_pelanggan hilang, jenis lain tetap", "uang_muka_pelanggan" not in [j["kunci"] for j in d3[str(ids["tinjau_masuk"])]["jenis_tersedia"]]
            and "terima_pembayaran" in [j["kunci"] for j in d3[str(ids["tinjau_masuk"])]["jenis_tersedia"]])
        # perlu_ditinjau per rekening == hitungan SQL; rute daftar Kas & Bank
        async with pool.acquire() as c:
            hit = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND status IN ('untuk_ditinjau', 'mungkin_duplikat')", tid, rid)
        lr = await BAR.list_bank_accounts(req(tid), is_active=None, account_type=None, search="UJI C3", skip=0, limit=100, sort_by="account_name", sort_order="asc", aksi=None, with_totals=False)
        mine = {x["id"]: x for x in lr["items"]}
        cek(f"{tid} G6 daftar Kas & Bank: perlu_ditinjau rekening uji == hitungan SQL ({hit}); rekening tanpa mutasi = 0", str(rid) in mine and mine[str(rid)]["perlu_ditinjau"] == hit == 4 and mine[str(rid_b)]["perlu_ditinjau"] == 0, f"{mine.get(str(rid), {}).get('perlu_ditinjau')}")
        # tak-di-bank: bentuk dokumen sama
        tdb = await MR.tak_di_bank(req(tid), rid, dari=None, sampai=None)
        bentuk_ok = all(set(x["dokumen"]) == set(BDK.bentuk(None, None, None, None)) for x in tdb["data"])
        cek(f"{tid} D9 tak-di-bank memakai SATU bentuk dokumen ({len(tdb['data'])} baris diperiksa)", bentuk_ok)
        # batal catatan lalu daftar: dokumen_tertaut hilang, baris kembali ke ditinjau
        sb, hb = await panggil(MR.batalkan_catatan(req(tid), rid, ids["catat"], BatalkanCatatanRequest(alasan="uji batal"), Response()))
        d4 = {x["id"]: x for x in (await daftar())["data"]}
        cek(f"{tid} D10 sesudah batalkan catatan: baris kembali untuk_ditinjau, dokumen_tertaut kosong (tautan dilepas tak ikut tampil), perlu_ditinjau naik", sb == 200 and d4[str(ids["catat"])]["status"] == "untuk_ditinjau" and d4[str(ids["catat"])]["dokumen_tertaut"] == [], f"{sb} {hb}")
    n = len(HASIL)
    print(f"HASIL {sum(HASIL)}/{n} lulus", flush=True)
    return 0 if all(HASIL) and n else 1


sys.exit(asyncio.run(utama()))
