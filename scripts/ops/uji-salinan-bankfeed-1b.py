"""BF-1b -- uji PENENTU TEBAKAN di SALINAN (klon milkydb) dengan golden set ANONIM di /root/fixtures/bankfeed_golden (di LUAR repo; hilang = TOLAK).
RUTE/penentu ASLI (daftar, /calon, TB.tebak); kaos + nyawit; tulis HANYA di salinan (benih + impor); penyimpanan berkas (MinIO) TIDAK disentuh.
Benih: 283 catatan b-catatan-milkyhoop.csv -> bank_transactions + jurnal seimbang (11 pasangan void benar-benar dgn reversed_by_id/reversal_of_id) pada rekening UJI baru.
Impor 4 berkas a-mutasi (1303 baris) lalu:
S0 benih: jumlah bt/jurnal/pasangan; kandidat SQL (tanpa pasangan batal & VOIDED) == hitungan MANDIRI python (259).
T1 PRESISI yakin == 100% terhadap c-jawaban `cocok` (satu yakin salah = merah) + yakin > 0.
T2 keputusan_pemilik / di_luar_periode / transfer PEMILIK / setoran TOKO-CDM TAK PERNAH yakin.
T3 pasangan void (asli + pembalik) TAK PERNAH kandidat (tebak, /calon).
T4 jalur SQL == jalur python murni (jenis, keyakinan, dokumen) pada semua 1303 baris.
T5 UJI ADVERSARIAL: golden dibelah id_baris genap/ganjil; presisi yakin + layak_massal diukur di KEDUA paruh = 100%; cakupan dicatat.
T6 layak_massal: 1:1 unik dua arah; baris yang dokumennya diperebutkan tak pernah massal.
T7 daftar (halaman) memuat tebakan lengkap dan SAMA dgn penentu atas semua baris; pindah halaman tak mengubah keyakinan.
T8 /calon: nominal persis + selisih 2.500; q melebar; pasangan void tak muncul; rekening lain tak bocor.
T9 akun bawaan F2 ada di tenant (4-90000/5-20850/5-80000); BIAYA ADM nyata -> catat 5-20850; BUNGA/PAJAK BUNGA sintetis -> 4-90000/5-80000.
T10 NOL tulisan: jurnal/bank_transactions/tautan/mutasi_bank tak berubah oleh tebak/daftar/calon."""
import asyncio
import collections
import csv
import os
import re
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

FIX = "/root/fixtures_ro"
BERKAS = [f"a-mutasi-2026-{m}.csv" for m in ("08", "09", "10", "11")]
sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import mutasi_bank as MR  # noqa: E402
from app.schemas.bankfeed import ImporMutasiRequest  # noqa: E402
from app.services import bankfeed_impor as BI  # noqa: E402
from app.services import bankfeed_tebak as TB  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
USER = str(uuid.uuid4())
ISI_DOK = {}
SUMBER = {"BILL_PAYMENT": "BILL_PAYMENT", "CUSTOMER_DEPOSIT": "CUSTOMER_DEPOSIT", "RECEIVE_PAYMENT": "RECEIVE_PAYMENT", "EXPENSE": "EXPENSE",
          "BANK_TRANSFER": "BANK_TRANSFER", "BANK_TRANSACTION": "BANK_TRANSACTION"}
TAK_PERNAH_YAKIN = ("keputusan_pemilik", "di_luar_periode", "catat:transfer_antar_rekening", "cocok:setoran_toko")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": USER, "username": "uji-bf1b"}
    return r


async def _baca(row):
    return ISI_DOK[str(row["id"])]
BI.baca_isi_dokumen = _baca


async def siapkan_dokumen(c, tid, nama, isi: bytes) -> uuid.UUID:
    did = uuid.uuid4()
    await c.execute("INSERT INTO documents (id, tenant_id, file_name, original_name, file_type, file_extension, file_size, storage_type, file_path, category, uploaded_by, uploaded_at) "
                    "VALUES ($1, $2, $3, $3, 'text/csv', 'csv', $4, 's3', $5, 'statement', $6::uuid, now())", did, tid, nama, len(isi), f"fx/{did}.csv", USER)
    ISI_DOK[str(did)] = isi
    return did


async def rekening_uji(c, tid, nomor="8888882185") -> uuid.UUID:
    coa = await c.fetchval("SELECT id FROM chart_of_accounts c WHERE tenant_id = $1 AND account_type = 'ASSET' AND NOT COALESCE(is_header, false) "
                           "AND NOT EXISTS (SELECT 1 FROM bank_accounts b WHERE b.coa_id = c.id) ORDER BY account_code LIMIT 1", tid)
    rid = uuid.uuid4()
    await c.execute("INSERT INTO bank_accounts (id, tenant_id, account_name, account_number, bank_name, coa_id, account_type, created_by) "
                    "VALUES ($1, $2, $3, $4, 'BCA', $5, 'bank', $6::uuid)", rid, tid, f"UJI BF1B {uuid.uuid4().hex[:6]}", nomor, coa, USER)
    return rid


async def impor(tid, rid, did):
    try:
        h = await MR.impor_mutasi_bank(req(tid), rid, ImporMutasiRequest(document_id=did), Response())
        return 201, h["data"]
    except HTTPException as e:
        return e.status_code, e.detail


def baca_csv(nama):
    return list(csv.DictReader(open(f"{FIX}/{nama}", encoding="utf-8-sig", newline="")))


def pembalik(r):
    return bool(re.match(r"^(RV|VD)-", r["nomor_jurnal"])) and bool(r["pasangan_void"])


async def benih(c, tid, rid, b):
    """283 catatan -> bank_transactions + jurnal seimbang (asli dulu, pembalik sesudahnya; pembalik.reversal_of_id, asli.reversed_by_id)."""
    coa_bank = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", rid, tid)
    lawan = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '5-20900'", tid)
    bt_ke_mh, mh_ke_bt, mh_ke_je = {}, {}, {}
    urut = sorted(b, key=lambda r: (1 if pembalik(r) else 0, r["id_mh"]))
    tag = rid.hex[:6]
    async with c.transaction():
        for r in urut:
            n, je, bt = D(r["nominal"]), uuid.uuid4(), uuid.uuid4()
            asli = mh_ke_je.get(r["pasangan_void"]) if pembalik(r) else None
            tipe = "REVERSAL" if pembalik(r) else SUMBER[r["jenis_asal"]]
            await c.execute("INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit, "
                            "created_by, reversal_of_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'POSTED', $8, $8, $9::uuid, $10)",
                            je, tid, f"UJI1B-{tag}-{r['id_mh']}", date.fromisoformat(r["tanggal"]), f"uji {r['id_mh']}", tipe, bt, abs(n), USER, asli)
            masuk = n > 0
            await c.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit) VALUES ($1, 1, $2, $3, $4), ($1, 2, $5, $6, $7)",
                            je, coa_bank, abs(n) if masuk else D(0), D(0) if masuk else abs(n), lawan, D(0) if masuk else abs(n), abs(n) if masuk else D(0))
            await c.execute("INSERT INTO bank_transactions (id, tenant_id, bank_account_id, transaction_date, transaction_type, amount, running_balance, reference_number, "
                            "description, payee_payer, journal_id, status, reference_type, origin_type, transaction_number, created_by) "
                            "VALUES ($1, $2, $3, $4, $5, $6, 0, NULLIF($7, ''), $8, NULLIF($9, ''), $10, $11, $12, 'SYSTEM', $13, $14::uuid)",
                            bt, tid, rid, date.fromisoformat(r["tanggal"]), r["jenis_transaksi"], n, r["nomor_referensi"], r["keterangan_anon"], r["nama_kontak_anon"],
                            je, r["status"], r["jenis_asal"], r["nomor_jurnal"], USER)
            bt_ke_mh[str(bt)], mh_ke_bt[r["id_mh"]], mh_ke_je[r["id_mh"]] = r["id_mh"], str(bt), je
            if asli is not None:
                await c.execute("UPDATE journal_entries SET reversed_by_id = $1, reversed_at = now() WHERE id = $2 AND tenant_id = $3", je, asli, tid)
    return bt_ke_mh, mh_ke_bt


async def hitung(c, tid, rid):
    return {"je": await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            "bt": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid),
            "tautan": await c.fetchval("SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1", tid),
            "mb": await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid),
            "mb_status": await c.fetchval("SELECT string_agg(DISTINCT status, ',') FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)}


def mandiri_kandidat(b):
    """Himpunan kandidat yang HARUS dikembalikan SQL: POSTED, bukan bagian pasangan batal."""
    batal = {r["id_mh"] for r in b if pembalik(r)} | {r["pasangan_void"] for r in b if pembalik(r)}
    return {r["id_mh"] for r in b if r["status"] == "POSTED" and r["id_mh"] not in batal}, batal


async def periksa(pool, tid, fx_b, fx_j, fx_mut):
    b = fx_b
    truth = {x["id_baris"]: x for x in fx_j}
    async with pool.acquire() as c:
        rid = await rekening_uji(c, tid)
        rid2 = await rekening_uji(c, tid, "8888883333")
        dok = {n: await siapkan_dokumen(c, tid, n, fx_mut[n]) for n in BERKAS}
        bt_ke_mh, mh_ke_bt = await benih(c, tid, rid, b)
        # satu bt di REKENING LAIN (tenant sama) bernominal sama dgn 1 baris -> tak boleh bocor ke rekening ini
        coa2 = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", rid2, tid)
        lawan = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '5-20900'", tid)
        je2, bt2 = uuid.uuid4(), uuid.uuid4()
        async with c.transaction():
            await c.execute("INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit, created_by) "
                            "VALUES ($1, $2, $3, $4, 'uji rekening lain', 'RECEIVE_PAYMENT', $5, 'POSTED', 500000, 500000, $6::uuid)", je2, tid, f"UJI1B-LAIN-{rid2.hex[:6]}", date(2026, 9, 21), bt2, USER)
            await c.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit) VALUES ($1, 1, $2, 500000, 0), ($1, 2, $3, 0, 500000)", je2, coa2, lawan)
            await c.execute("INSERT INTO bank_transactions (id, tenant_id, bank_account_id, transaction_date, transaction_type, amount, running_balance, payee_payer, journal_id, status, "
                            "origin_type, transaction_number, created_by) VALUES ($1, $2, $3, $4, 'deposit', 500000, 0, 'REKENING LAIN', $5, 'POSTED', 'SYSTEM', 'LAIN-1', $6::uuid)",
                            bt2, tid, rid2, date(2026, 9, 21), je2, USER)
        for n in BERKAS:
            st, h = await impor(tid, rid, dok[n])
            assert st == 201, (n, st, h)

    ok_kand, batal = mandiri_kandidat(b)
    n_pair = sum(1 for r in b if pembalik(r))
    async with pool.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)
        n_rev = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id IS NOT NULL AND journal_number LIKE $2", tid, f"UJI1B-{rid.hex[:6]}-%")
        n_rb = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND reversed_by_id IS NOT NULL AND journal_number LIKE $2", tid, f"UJI1B-{rid.hex[:6]}-%")
    cek(f"{tid} S0 benih: {len(b)} bt, {n_pair} pasangan void (pembalik reversal_of_id, asli reversed_by_id)", n == len(b) and n_rev == n_pair == n_rb == 11, f"bt={n} pembalik={n_rev} asli={n_rb}")

    async with pool.acquire() as c:
        kand_sql = await TB.kandidat_bt(c, tid, rid, date(2026, 1, 1), date(2026, 12, 31))
        awal = await hitung(c, tid, rid)
        rows = [dict(r) for r in await c.fetch("SELECT id, tanggal, keterangan, arah, nominal, status, baris_mentah FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)]
    kand_ids = {bt_ke_mh[k["id"]] for k in kand_sql}
    cek(f"{tid} S0 kandidat SQL == hitungan mandiri (POSTED, tanpa pasangan batal, tanpa VOIDED)", kand_ids == ok_kand and len(kand_sql) == len(ok_kand),
        f"sql={len(kand_sql)} mandiri={len(ok_kand)} selisih={sorted(kand_ids ^ ok_kand)[:5]}")
    cek(f"{tid} S0 bt rekening LAIN tak ikut kandidat", str(bt2) not in {k["id"] for k in kand_sql})

    id_baris = {str(r["id"]): next(csv.reader([r["baris_mentah"]]))[-1] for r in rows}
    baris = [{"id": str(r["id"]), "tanggal": r["tanggal"], "nominal": D(str(r["nominal"])), "arah": r["arah"], "keterangan": r["keterangan"], "status": r["status"]} for r in rows]
    cek(f"{tid} S0 {len(baris)} baris impor == 1303 dgn id_baris unik", len(baris) == 1303 and len(set(id_baris.values())) == 1303)
    async with pool.acquire() as c:
        peta = await TB.tebak(c, tid, rid, baris)
    cocok_dobel = collections.Counter(x["id_mh"] for x in fx_j if x["aksi_benar"].startswith("cocok"))

    def benar(m):
        t, ib = peta[m], id_baris[m]
        return (truth[ib]["aksi_benar"] == "cocok" and t["jenis"] == "cocok" and not t["gabungan"] and len(t["bank_transaction_ids"]) == 1
                and bt_ke_mh[t["bank_transaction_ids"][0]] == truth[ib]["id_mh"] and cocok_dobel[truth[ib]["id_mh"]] == 1)
    yakin = [m for m in peta if peta[m]["keyakinan"] == "yakin"]
    salah = [(id_baris[m], truth[id_baris[m]]["aksi_benar"], peta[m]["kalimat"]) for m in yakin if not benar(m)]
    cek(f"{tid} T1 presisi YAKIN == 100% terhadap c-jawaban cocok (yakin={len(yakin)}, benar={len(yakin) - len(salah)})", yakin and not salah, str(salah[:3]))
    sebar = collections.Counter((t["jenis"], t["keyakinan"]) for t in peta.values())
    cocok_n = sum(1 for x in fx_j if x["aksi_benar"] == "cocok")
    print(f"INFO {tid} cakupan: {dict(sebar)}; jawaban cocok={cocok_n}; yakin benar={len(yakin) - len(salah)}", flush=True)

    bocor = [id_baris[m] for m in yakin if truth[id_baris[m]]["aksi_benar"] in TAK_PERNAH_YAKIN]
    cek(f"{tid} T2 keputusan_pemilik/di_luar_periode/transfer PEMILIK/setoran TOKO-CDM tak pernah yakin", not bocor, str(bocor[:5]))

    terpilih = {bt_ke_mh[i] for t in peta.values() for i in t["bank_transaction_ids"]}
    cek(f"{tid} T3 pasangan void (asli+pembalik, {len(batal)} dokumen) tak pernah dipilih tebakan", not (terpilih & batal), str(sorted(terpilih & batal)[:5]))
    muncul_calon = []
    async with pool.acquire() as c:
        for ib_mh in sorted(batal)[:24]:
            nominal = abs(D(next(r for r in b if r["id_mh"] == ib_mh)["nominal"]))
            for m in [x for x in baris if x["nominal"] == nominal][:3]:
                h = await TB.calon(c, tid, rid, m["id"], q=next(r for r in b if r["id_mh"] == ib_mh)["nomor_jurnal"])
                muncul_calon += [x["id"] for x in h["data"] if bt_ke_mh[x["id"]] in batal]
    cek(f"{tid} T3 pasangan void tak muncul di /calon (dicari dgn nomor jurnalnya)", not muncul_calon, str(muncul_calon[:3]))

    # T4: jalur SQL == jalur python murni (kandidat dari b-catatan yg sama)
    kand_py = [{"id": r["id_mh"], "tanggal": date.fromisoformat(r["tanggal"]), "nominal": abs(D(r["nominal"])), "arah": "masuk" if D(r["nominal"]) > 0 else "keluar",
                "kontak_nama": r["nama_kontak_anon"], "nomor": r["nomor_jurnal"], "jenis": r["jenis_transaksi"], "sumber": SUMBER[r["jenis_asal"]]}
               for r in b if r["id_mh"] in ok_kand]
    peta_py = {x["id"]: TB.putuskan(x, kand_py, {}) for x in baris}
    terpilih_py = {i for t in peta_py.values() for i in t["bank_transaction_ids"]}
    kosong_py = [x for x in baris if peta_py[x["id"]]["jenis"] == "kosong" and not peta_py[x["id"]]["blok"]]
    for k in (k for k in kand_py if k["id"] not in terpilih_py):
        for mid, t in TB.gabungan_baris(k, kosong_py).items():
            if peta_py[mid]["jenis"] == "kosong":
                peta_py[mid] = t
    st = {x["id"]: x["status"] for x in baris}
    peta_py = TB.tandai_layak_massal(TB.turunkan_persaingan(peta_py, st), st)
    beda = [id_baris[m] for m in peta if (peta[m]["jenis"], peta[m]["keyakinan"], sorted(bt_ke_mh[i] for i in peta[m]["bank_transaction_ids"]), peta[m]["layak_massal"]) !=
            (peta_py[m]["jenis"], peta_py[m]["keyakinan"], sorted(peta_py[m]["bank_transaction_ids"]), peta_py[m]["layak_massal"])
            and not (peta[m]["blok"] or peta_py[m]["blok"])]
    cek(f"{tid} T4 jalur SQL == jalur python murni pada {len(baris)} baris (jenis, keyakinan, dokumen, layak_massal)", not beda, f"beda={beda[:5]} ({len(beda)})")

    # T5: uji adversarial genap/ganjil
    for paruh, f in (("genap", lambda v: v % 2 == 0), ("ganjil", lambda v: v % 2 == 1)):
        ids = [m for m in peta if f(int(id_baris[m][2:]))]
        y = [m for m in ids if peta[m]["keyakinan"] == "yakin"]
        s = [m for m in y if not benar(m)]
        massal = [m for m in ids if peta[m]["layak_massal"]]
        sm = [m for m in massal if not benar(m)]
        cek(f"{tid} T5 adversarial paruh {paruh}: baris={len(ids)} yakin={len(y)} presisi 100% dan layak_massal={len(massal)} semuanya benar", y and not s and not sm, f"salah={s[:3]} massal_salah={sm[:3]}")

    # T6: layak_massal
    massal = [m for m in peta if peta[m]["layak_massal"]]
    dok_massal = collections.Counter(peta[m]["bank_transaction_ids"][0] for m in massal)
    pilih_semua = collections.Counter(i for m in peta if peta[m]["jenis"] == "cocok" and not peta[m]["gabungan"] for i in peta[m]["bank_transaction_ids"])
    cek(f"{tid} T6 layak_massal 1:1 (tiap dokumen satu baris; dokumen tak diperebutkan baris lain) -- {len(massal)} baris",
        all(dok_massal[bt_] == 1 and pilih_semua[bt_] == 1 for bt_ in dok_massal) and all(peta[m]["keyakinan"] == "yakin" and not peta[m]["gabungan"] for m in massal))
    dobel = [m for m in peta if peta[m].get("mungkin_dobel_dengan")]
    cek(f"{tid} T6 baris dgn dokumen diperebutkan tak yakin & tak massal ({len(dobel)} baris)", all(peta[m]["keyakinan"] != "yakin" and not peta[m]["layak_massal"] for m in dobel))

    # T7: daftar memuat tebakan lengkap, sama dgn penentu atas SEMUA baris, tak berubah antar halaman
    halaman = []
    for off in (0, 200, 400):
        async with pool.acquire() as c:
            h = await MR.daftar_mutasi_bank(req(tid), rid, status="untuk_ditinjau", q=None, dari=None, sampai=None, limit=200, offset=off)
        halaman += h["data"]
    lengkap = all(set(r["tebakan"]) >= {"jenis", "keyakinan", "kalimat", "bank_transaction_ids", "dokumen", "layak_massal", "layak_massal_text", "mungkin_dobel_dengan"} for r in halaman)
    sama = [r["id"] for r in halaman if (r["tebakan"]["jenis"], r["tebakan"]["keyakinan"], r["tebakan"]["layak_massal"]) != (peta[r["id"]]["jenis"], peta[r["id"]]["keyakinan"], peta[r["id"]]["layak_massal"])]
    cek(f"{tid} T7 daftar: tebakan lengkap & == penentu atas semua baris pada {len(halaman)} baris (tak berubah antar halaman)", lengkap and not sama and len(halaman) == 600, f"beda={sama[:3]}")
    contoh = next(r for r in halaman if r["tebakan"]["keyakinan"] == "yakin")
    cek(f"{tid} T7 baris yakin: dokumen berisi id/nomor, kalimat dari server", contoh["tebakan"]["dokumen"] and contoh["tebakan"]["kalimat"] and "Cocok dengan" in contoh["tebakan"]["kalimat"])

    # T8: /calon
    yk = next(m for m in yakin if benar(m))
    async with pool.acquire() as c:
        h = await TB.calon(c, tid, rid, yk)
        besar = next(x for x in baris if x["arah"] == "masuk" and abs((x["tanggal"] - date(2026, 9, 21)).days) <= 20)   # dalam rentang bt rekening lain (21 Sep)
        h_lain = await TB.calon(c, tid, rid, besar["id"], q="REKENING LAIN")
        h_q = await TB.calon(c, tid, rid, yk, q=h["data"][0]["nomor"][:6])
        try:
            await TB.calon(c, tid, rid, uuid.uuid4())
            nf = None
        except HTTPException as e:
            nf = e.status_code
    cek(f"{tid} T8 /calon: baris yakin -> calon pertama = dokumen yakin, nominal persis", h["data"] and h["data"][0]["id"] == peta[yk]["bank_transaction_ids"][0] and h["data"][0]["keyakinan"] == "yakin" and h["data"][0]["selisih_nominal"] is None)
    cek(f"{tid} T8 /calon: bt rekening lain tak bocor (dicari dgn namanya) & mutasi tak ada 404", not h_lain["data"] and nf == 404)
    cek(f"{tid} T8 /calon: ?q= melebar (nomor)", h_q["jumlah"] >= 1)

    # T9: akun bawaan F2
    async with pool.acquire() as c:
        akun = await TB.akun_bawaan(c, tid)
        sint = [{"id": "s1", "tanggal": date(2026, 10, 31), "nominal": D("11000.00"), "arah": "keluar", "keterangan": "BIAYA ADM", "status": "untuk_ditinjau"},
                {"id": "s2", "tanggal": date(2026, 10, 31), "nominal": D("5000.00"), "arah": "masuk", "keterangan": "BUNGA", "status": "untuk_ditinjau"},
                {"id": "s3", "tanggal": date(2026, 10, 31), "nominal": D("1000.00"), "arah": "keluar", "keterangan": "PAJAK BUNGA", "status": "untuk_ditinjau"}]
        t9 = await TB.tebak(c, tid, rid, sint)
    cek(f"{tid} T9 akun bawaan F2 ada di tenant: admin 5-20850, bunga 4-90000, pajak bunga 5-80000", {k: v["kode"] for k, v in akun.items()} == TB.AKUN_BAWAAN, str({k: v["kode"] for k, v in akun.items()}))
    cek(f"{tid} T9 BIAYA ADM->5-20850, BUNGA->4-90000, PAJAK BUNGA->5-80000 (catat, periksa)",
        [(t9[i]["jenis"], t9[i]["keyakinan"], t9[i]["akun"]["kode"]) for i in ("s1", "s2", "s3")] == [("catat", "periksa", "5-20850"), ("catat", "periksa", "4-90000"), ("catat", "periksa", "5-80000")])
    adm = [m for m in peta if re.match(r"^BIAYA ADM", next(x for x in baris if x["id"] == m)["keterangan"])]
    cek(f"{tid} T9 baris BIAYA ADM nyata di golden ({len(adm)}) -> catat 5-20850; biaya BI-FAST 2.500 BUKAN admin",
        len(adm) == 1 and peta[adm[0]]["jenis"] == "catat" and peta[adm[0]]["akun"]["kode"] == "5-20850"
        and not any(peta[x["id"]]["jenis"] == "catat" for x in baris if "BIAYA TXN" in x["keterangan"]))

    # T10: nol tulisan
    async with pool.acquire() as c:
        akhir = await hitung(c, tid, rid)
    cek(f"{tid} T10 NOL tulisan oleh tebak/daftar/calon (jurnal, bank_transactions, tautan, mutasi_bank, status)", akhir == awal, f"awal={awal} akhir={akhir}")


async def utama():
    perlu = BERKAS + ["b-catatan-milkyhoop.csv", "c-jawaban-benar.csv", "d-kasus-pengecualian.csv"]
    for n in perlu:
        if not os.path.isfile(f"{FIX}/{n}"):
            print(f"TOLAK: fixture golden hilang: {n}")
            return 4
    fx_mut = {n: open(f"{FIX}/{n}", "rb").read() for n in BERKAS}
    fx_b, fx_j = baca_csv("b-catatan-milkyhoop.csv"), baca_csv("c-jawaban-benar.csv")
    pool = await MR.get_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1b"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid, fx_b, fx_j, fx_mut)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
