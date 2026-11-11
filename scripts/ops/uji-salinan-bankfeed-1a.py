"""BF-1a -- uji di SALINAN (klon milkydb) dengan golden set ANONIM di /root/fixtures/bankfeed_golden (di LUAR repo; hilang = TOLAK). RUTE ASLI
(daftar, pratinjau impor, impor); kaos + nyawit; tulis HANYA di salinan; penyimpanan berkas (MinIO) TIDAK disentuh (pembaca isi diganti ke berkas golden).
T1 parser vs hitungan MANDIRI csv (nyata, PEND, Σ CR/DB per berkas); 4 berkas = 1310 baris == baris c-jawaban; kaki+saldo berurutan lolos tanpa blok.
T2 impor Agu..Nov berurutan: baru == nyata; Σ di DB == Σ berkas; sidik unik; keterangan berkoma tersimpan utuh.
T3 impor ulang Agu: 0 baru, duplikat == nyata (pratinjau == tulis).
T4 daftar: cacah, total Σ server Decimal, filter status/q/dari-sampai, paging.
T5 REKENING_BERKAS_BEDA; BERKAS_TAK_SEIMBANG (kaki dirusak); jalur berkas milik pengguna lain ditolak.
T6 mungkin_duplikat lintas format (baris lama dgn tanggal/arah/nominal/saldo sama, sidik beda).
T7 dua impor Agu SERENTAK di rekening baru: total baris == nyata (bukan dua kali), satu baru + satu 0.
T8 NOL jurnal / bank_transactions / jurnal baru; lampiran + audit tercatat; tenant lain tak tersentuh."""
import asyncio
import csv
import os
import re
import sys
import uuid
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

FIX = "/root/fixtures_ro"          # dipasang read-only oleh pembungkus .sh
BERKAS = [f"a-mutasi-2026-{m}.csv" for m in ("08", "09", "10", "11")]
sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import mutasi_bank as MR  # noqa: E402
from app.schemas.bankfeed import ImporMutasiRequest  # noqa: E402
from app.services import bankfeed_impor as BI  # noqa: E402
from app.services import bankfeed_parser as P  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
USER = str(uuid.uuid4())
ISI_DOK = {}   # document_id -> bytes (pengganti MinIO)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, user=USER):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": user, "username": "uji-bf1a"}
    return r


async def _baca(row):
    return ISI_DOK[str(row["id"])]
BI.baca_isi_dokumen = _baca


def hitung_mandiri(isi: bytes) -> dict:
    """Hitungan MANDIRI (csv sederhana, bukan parser) untuk membandingkan parser."""
    baris = [r for r in csv.reader(isi.decode("latin-1").splitlines()) if r]
    data = [r for r in baris if re.match(r"^'(\d{2}/\d{2}/\d{4}|PEND)$", r[0])]
    nyata = [r for r in data if r[0] != "'PEND"]
    return {"nyata": len(nyata), "pend": len(data) - len(nyata), "kredit": sum((D(r[3]) for r in nyata if r[4] == "CR"), D(0)),
            "debet": sum((D(r[3]) for r in nyata if r[4] == "DB"), D(0)), "koma": sum(1 for r in nyata if "," in r[1]), "semua": len(data)}


async def siapkan_dokumen(c, tid, nama, isi: bytes, user=USER, umur_jam=0) -> uuid.UUID:
    did = uuid.uuid4()
    await c.execute("INSERT INTO documents (id, tenant_id, file_name, original_name, file_type, file_extension, file_size, storage_type, file_path, category, uploaded_by, uploaded_at) "
                    "VALUES ($1, $2, $3, $3, 'text/csv', 'csv', $4, 's3', $5, 'statement', $6::uuid, now() - make_interval(hours => $7))",
                    did, tid, nama, len(isi), f"fx/{did}.csv", user, umur_jam)
    ISI_DOK[str(did)] = isi
    return did


async def rekening_uji(c, tid, nomor="8888882185") -> uuid.UUID:
    coa = await c.fetchval("SELECT id FROM chart_of_accounts c WHERE tenant_id = $1 AND account_type = 'ASSET' AND NOT COALESCE(is_header, false) "
                           "AND NOT EXISTS (SELECT 1 FROM bank_accounts b WHERE b.coa_id = c.id) ORDER BY account_code LIMIT 1", tid)
    rid = uuid.uuid4()
    await c.execute("INSERT INTO bank_accounts (id, tenant_id, account_name, account_number, bank_name, coa_id, account_type, created_by) "
                    "VALUES ($1, $2, $3, $4, 'BCA', $5, 'bank', $6::uuid)", rid, tid, f"UJI BF1A {uuid.uuid4().hex[:6]}", nomor, coa, USER)
    return rid


async def pratinjau(tid, rid, did, **kw):
    return (await MR.pratinjau_impor(req(tid), rid, ImporMutasiRequest(document_id=did, **kw)))["data"]


async def impor(tid, rid, did, **kw):
    try:
        h = await MR.impor_mutasi_bank(req(tid), rid, ImporMutasiRequest(document_id=did, **kw), Response())
        return 201, h["data"]
    except HTTPException as e:
        return e.status_code, e.detail


async def daftar(tid, rid, **kw):
    a = dict(status=None, q=None, dari=None, sampai=None, limit=50, offset=0)
    a.update(kw)
    return await MR.daftar_mutasi_bank(req(tid), rid, **a)


async def periksa(pool, tid, fx):
    async with pool.acquire() as c:
        rid = await rekening_uji(c, tid)
        dok = {n: await siapkan_dokumen(c, tid, n, fx[n]) for n in BERKAS}
        n_jurnal0 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid)
        n_bt0 = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid)
        lain = [t for t in TENANT if t != tid][0]
        lain0 = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1", lain)
    mandiri = {n: hitung_mandiri(fx[n]) for n in BERKAS}

    # ---- T1 parser vs hitungan mandiri + total 1310
    ok = True
    for n in BERKAS:
        h = P.baca(fx[n], n)
        m = mandiri[n]
        ok &= (not h.blocks and len(h.baris) == m["nyata"] and sum(d["n"] for d in h.dilewati) == m["pend"]
               and sum(b.nominal for b in h.baris if b.arah == "masuk") == m["kredit"] and sum(b.nominal for b in h.baris if b.arah == "keluar") == m["debet"])
    cek(f"{tid} T1 parser == hitungan mandiri per berkas (nyata, PEND, Σ CR, Σ DB), tanpa blok", ok, str({n[-6:-4]: (mandiri[n]["nyata"], mandiri[n]["pend"]) for n in BERKAS}))
    with open(f"{FIX}/c-jawaban-benar.csv", newline="") as f:
        n_jawaban = sum(1 for _ in csv.DictReader(f))
    cek(f"{tid} T1 total baris data 4 berkas (termasuk PEND) == baris c-jawaban", sum(m["semua"] for m in mandiri.values()) == n_jawaban,
        f"{sum(m['semua'] for m in mandiri.values())} vs {n_jawaban}")
    semua_sidik = [s for n in BERKAS for s in P.beri_sidik(tid, rid, P.baca(fx[n], n).baris)]
    cek(f"{tid} T1 sidik unik di seluruh baris golden (tanpa tabrakan)", len(set(semua_sidik)) == len(semua_sidik), f"{len(semua_sidik)} baris")

    # ---- T2 impor berurutan
    kumulatif = 0
    for n in BERKAS:
        pv = await pratinjau(tid, rid, dok[n])
        st, h = await impor(tid, rid, dok[n], hash_isi=pv["payload"]["hash_isi"])
        m = mandiri[n]
        kumulatif += m["nyata"]
        cek(f"{tid} T2 {n}: pratinjau == tulis (baru {m['nyata']}, PEND {m['pend']})",
            pv["ok"] and st == 201 and pv["baru"] == h["baru"] == m["nyata"] and h["duplikat"] == 0 and sum(d["n"] for d in h["dilewati"]) == m["pend"], f"pratinjau.baru={pv['baru']} tulis={h if st != 201 else h['baru']}")
    async with pool.acquire() as c:
        n_db = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)
        sums = await c.fetchrow("SELECT COALESCE(SUM(nominal) FILTER (WHERE arah = 'masuk'), 0) AS k, COALESCE(SUM(nominal) FILTER (WHERE arah = 'keluar'), 0) AS d, "
                                "count(DISTINCT sidik) AS u FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)
        koma_db = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND position(',' in keterangan) > 0", tid, rid)
    cek(f"{tid} T2 baris di DB == Σ nyata ({kumulatif}); Σ masuk/keluar == Σ berkas; sidik unik", n_db == kumulatif == sums["u"]
        and sums["k"] == sum(m["kredit"] for m in mandiri.values()) and sums["d"] == sum(m["debet"] for m in mandiri.values()), f"db={n_db}")
    cek(f"{tid} T2 keterangan berkoma tersimpan utuh (koma berkutip)", koma_db == sum(m["koma"] for m in mandiri.values()) and koma_db > 0, f"{koma_db} baris")

    # ---- T3 impor ulang
    pv = await pratinjau(tid, rid, dok[BERKAS[0]])
    st, h = await impor(tid, rid, dok[BERKAS[0]])
    async with pool.acquire() as c:
        n_db2 = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)
    cek(f"{tid} T3 impor ulang Agu: pratinjau == tulis, 0 baru, duplikat == nyata, baris DB tak bertambah",
        pv["baru"] == h["baru"] == 0 and pv["duplikat"] == h["duplikat"] == mandiri[BERKAS[0]]["nyata"] and n_db2 == n_db)

    # ---- T4 daftar
    d = await daftar(tid, rid)
    cek(f"{tid} T4 daftar: cacah untuk_ditinjau == seluruh baris, total Σ server == Σ berkas",
        d["cacah"] == {"untuk_ditinjau": kumulatif, "mungkin_duplikat": 0, "dicatat": 0, "dikecualikan": 0}
        and D(d["total"]["masuk"]) == sums["k"] and D(d["total"]["keluar"]) == sums["d"] and d["total"]["jumlah"] == kumulatif
        and len(d["data"]) == 50 and d["has_more"] is True, f"cacah={d['cacah']} total={d['total']}")
    d2 = await daftar(tid, rid, dari=__import__("datetime").date(2026, 10, 1), sampai=__import__("datetime").date(2026, 10, 31), limit=200)
    cek(f"{tid} T4 filter rentang Okt: jumlah == baris Okt dan tanggal semua di Okt", d2["total"]["jumlah"] == mandiri[BERKAS[2]]["nyata"]
        and all(x["tanggal"].startswith("2026-10") for x in d2["data"]))
    d3 = await daftar(tid, rid, q="KARTU DEBIT", limit=200)
    cek(f"{tid} T4 pencarian q (keterangan) menemukan baris berkoma", d3["total"]["jumlah"] > 0 and all("KARTU DEBIT" in x["keterangan"].upper() for x in d3["data"]))
    d4 = await daftar(tid, rid, status="dicatat")
    cek(f"{tid} T4 status dicatat kosong; contoh baris: teks + tebakan kosong", d4["data"] == [] and d["data"][0]["tebakan"]["jenis"] == "kosong"
        and d["data"][0]["status_text"] == "Untuk ditinjau" and d["data"][0]["nominal_tampil"].startswith("Rp "))

    # ---- T5 pagar
    async with pool.acquire() as c:
        await c.execute("UPDATE bank_accounts SET account_number = '1234567890' WHERE id = $1", rid)
    pv = await pratinjau(tid, rid, dok[BERKAS[0]])
    async with pool.acquire() as c:
        await c.execute("UPDATE bank_accounts SET account_number = '8888882185' WHERE id = $1", rid)
    cek(f"{tid} T5 rekening berkas beda -> REKENING_BERKAS_BEDA", [b["code"] for b in pv["blocks"]] == ["REKENING_BERKAS_BEDA"] and not pv["ok"])
    rusak = re.sub(rb"(Kredit,=,)[0-9.]+", rb"\g<1>1.00", fx[BERKAS[1]], count=1)
    async with pool.acquire() as c:
        d_rusak = await siapkan_dokumen(c, tid, "rusak.csv", rusak)
        d_lain = await siapkan_dokumen(c, tid, "lain.csv", fx[BERKAS[1]], user=str(uuid.uuid4()))
        d_tua = await siapkan_dokumen(c, tid, "tua.csv", fx[BERKAS[1]], umur_jam=30)
    pv = await pratinjau(tid, rid, d_rusak)
    cek(f"{tid} T5 kaki dirusak -> BERKAS_TAK_SEIMBANG berkalimat", [b["code"] for b in pv["blocks"]] == ["BERKAS_TAK_SEIMBANG"] and "Kredit" in pv["blocks"][0]["message"])
    cek(f"{tid} T5 berkas milik pengguna lain / lewat 24 jam / tak ada ditolak",
        [b["code"] for b in (await pratinjau(tid, rid, d_lain))["blocks"]] == ["BERKAS_BUKAN_MILIK_ANDA"]
        and [b["code"] for b in (await pratinjau(tid, rid, d_tua))["blocks"]] == ["BERKAS_KEDALUWARSA"]
        and [b["code"] for b in (await pratinjau(tid, rid, uuid.uuid4()))["blocks"]] == ["BERKAS_TAK_DITEMUKAN"])
    st, det = await impor(tid, rid, d_rusak)
    cek(f"{tid} T5 simpan berkas tak seimbang -> 422 IMPOR_DITOLAK, tak ada baris tertulis", st == 422 and det["code"] == "IMPOR_DITOLAK")

    # ---- T6 mungkin_duplikat lintas format + T7 serentak (rekening baru)
    async with pool.acquire() as c:
        rid2 = await rekening_uji(c, tid)
        satu = P.baca(fx[BERKAS[0]], BERKAS[0]).baris[0]
        await c.execute("INSERT INTO mutasi_bank (tenant_id, bank_account_id, tanggal, keterangan, keterangan_normal, arah, nominal, saldo_berkas, sidik) "
                        "VALUES ($1, $2, $3, 'KETERANGAN PDF BERBEDA', 'keterangan pdf berbeda', $4, $5, $6, 'sidik-lama-pdf')",
                        tid, rid2, satu.tanggal, satu.arah, satu.nominal, satu.saldo)
    pv = await pratinjau(tid, rid2, dok[BERKAS[0]])
    st, h = await impor(tid, rid2, dok[BERKAS[0]])
    async with pool.acquire() as c:
        bendera = await c.fetchrow("SELECT count(*) FILTER (WHERE status = 'mungkin_duplikat') AS m, count(*) FILTER (WHERE duplikat_dari IS NOT NULL) AS dd, count(*) AS n "
                                   "FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND sidik <> 'sidik-lama-pdf'", tid, rid2)
    cek(f"{tid} T6 baris mirip lintas format -> mungkin_duplikat (tetap diimpor, bertanda, menunjuk baris lama)",
        len(pv["mungkin_duplikat"]) == 1 and bendera["m"] == 1 and bendera["dd"] == 1 and bendera["n"] == mandiri[BERKAS[0]]["nyata"])
    d5 = await daftar(tid, rid2)
    cek(f"{tid} T6 cacah: mungkin_duplikat 1 terpisah dari untuk_ditinjau (baris lama + sisanya)",
        d5["cacah"]["mungkin_duplikat"] == 1 and d5["cacah"]["untuk_ditinjau"] == mandiri[BERKAS[0]]["nyata"], str(d5["cacah"]))
    async with pool.acquire() as c:
        rid3 = await rekening_uji(c, tid)
    hasil = await asyncio.gather(impor(tid, rid3, dok[BERKAS[0]]), impor(tid, rid3, dok[BERKAS[0]]))
    async with pool.acquire() as c:
        n3 = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid3)
    baru = sorted(x[1]["baru"] for x in hasil if x[0] == 201)
    cek(f"{tid} T7 dua impor identik SERENTAK: total baris == nyata (bukan dua kali), satu baru + satu 0",
        n3 == mandiri[BERKAS[0]]["nyata"] and baru == [0, mandiri[BERKAS[0]]["nyata"]], f"n={n3} baru={baru} status={[x[0] for x in hasil]}")

    # ---- T8 nol jurnal, lampiran, audit, tenant lain
    async with pool.acquire() as c:
        n_jurnal1 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid)
        n_bt1 = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid)
        imp = await c.fetchval("SELECT count(*) FROM mutasi_bank_impor WHERE tenant_id = $1 AND bank_account_id = ANY($2::uuid[])", tid, [rid, rid2, rid3])
        lamp = await c.fetchval("SELECT count(*) FROM document_attachments WHERE tenant_id = $1 AND entity_type = 'mutasi_bank_impor' AND entity_id IN "
                                "(SELECT id FROM mutasi_bank_impor WHERE tenant_id = $1 AND bank_account_id = ANY($2::uuid[]))", tid, [rid, rid2, rid3])
        audit = await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND \"eventType\" = 'BANK_FEED_IMPOR' AND entity_id IN "
                                 "(SELECT id FROM mutasi_bank_impor WHERE tenant_id = $1 AND bank_account_id = ANY($2::uuid[]))", tid, [rid, rid2, rid3])
        lain1 = await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1", lain)
    cek(f"{tid} T8 NOL jurnal dan NOL bank_transactions baru", n_jurnal1 == n_jurnal0 and n_bt1 == n_bt0, f"jurnal {n_jurnal0}->{n_jurnal1} bt {n_bt0}->{n_bt1}")
    cek(f"{tid} T8 tiap impor punya lampiran + audit (riwayat tak terpotong)", imp > 0 and lamp == imp and audit == imp, f"impor={imp} lampiran={lamp} audit={audit}")
    cek(f"{tid} T8 tenant lain tak tersentuh", lain1 == lain0)


async def utama():
    fx = {}
    for n in BERKAS + ["c-jawaban-benar.csv"]:
        if not os.path.isfile(f"{FIX}/{n}"):
            print(f"TOLAK: fixture golden hilang: {n}")
            return 4
    for n in BERKAS:
        fx[n] = open(f"{FIX}/{n}", "rb").read()
    pool = await MR.get_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1a"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid, fx)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
