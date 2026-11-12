"""BF-1c1 -- uji AKSI Bank Feed + laporan tak-di-bank di SALINAN (klon milkydb) dengan golden set ANONIM di /root/fixtures/bankfeed_golden (di LUAR repo; hilang = TOLAK).
RUTE ASLI (cocokkan, kecualikan, lepaskan, riwayat, daftar, tak-di-bank); kaos + nyawit; tulis HANYA di salinan (benih b-catatan 283 bt + 11 pasangan void; impor 1303 baris).
A serentak: dua baris berbeda -> satu dokumen (satu menang), baris sama dua kali (satu 409), kunci idempotensi sama dua kali (jawaban sama).
B cocokkan 19 baris yakin: pratinjau (jurnal []) == tulis (tautan, Σ, status); NOL jurnal/bt; matched_statement_line_id TIDAK ditulis; satu riwayat per aksi.
C SUDAH_DIPUTUS, versi basi 409, dokumen tak dikenal, rekening lain 404, replay kunci idem, kunci sama isi beda 409.
D daftar tab Dicatat + riwayat urut. E lepaskan: tautan dilepas bukan dihapus, dokumen/jurnal utuh, status kembali. F kecualikan.
G laporan tak-di-bank == himpunan MANDIRI dari b-catatan/c-jawaban (sebelum dan sesudah keputusan cocok), pasangan void tak pernah muncul, d-kasus X2/X3/X7 bila terpetakan.
H penjaga void: dokumen bertaut ditolak BANK_TX_BERTAUT (semua jenis sumber terikat; pemanggil nyata kasbank_tulis.rencana_void_manual), bebas sesudah dilepas.
I NOL jurnal dan NOL bank_transactions baru sepanjang unit."""
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
from app.services import bankfeed_aksi as BA  # noqa: E402
from app.services import bankfeed_tak_di_bank as TD  # noqa: E402
from app.services import bankfeed_tebak as TB  # noqa: E402
from app.services import jaga_rekonsiliasi as J  # noqa: E402
from app.services import kasbank_tulis as KT  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402

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


def req(tid, kunci=None):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    r.state.user = {"tenant_id": tid, "user_id": USER, "username": "uji-bf1c1"}
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


from datetime import timedelta  # noqa: E402
from uuid import UUID  # noqa: E402

from app.schemas.bankfeed import AlokasiCocok, CocokkanRequest, KecualikanRequest, LepaskanRequest  # noqa: E402


def _hasil(e):
    d = e.detail
    return {"code": d.get("code"), "message": d.get("message")} if isinstance(d, dict) else {"code": getattr(e, "code", None), "message": d}


async def panggil(coro):
    try:
        return 200, await coro
    except HTTPException as e:
        return e.status_code, _hasil(e)


def _bd(ids, alokasi=None, versi=None):
    return CocokkanRequest(bank_transaction_ids=[UUID(i) for i in ids], versi=versi,
                           alokasi=[AlokasiCocok(bank_transaction_id=UUID(i), nominal=n) for i, n in (alokasi or {}).items()] or None)


async def cocok(tid, rid, mid, ids, alokasi=None, kunci=None, versi=None):
    return await panggil(MR.cocokkan(req(tid, kunci), rid, UUID(mid), _bd(ids, alokasi, versi), Response()))


async def prev_cocok(tid, rid, mid, ids, alokasi=None):
    return (await MR.pratinjau_cocokkan(req(tid), rid, UUID(mid), _bd(ids, alokasi)))["data"]


async def lepas(tid, rid, mid, versi=None):
    return await panggil(MR.lepaskan(req(tid), rid, UUID(mid), LepaskanRequest(versi=versi), Response()))


async def kecuali(tid, rid, mid, alasan="duplikat", catatan=None):
    return await panggil(MR.kecualikan(req(tid), rid, UUID(mid), KecualikanRequest(alasan=alasan, catatan=catatan), Response()))


async def hitung2(c, tid, rid):
    mut = "(SELECT id FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2)"
    return {"je": await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            "bt": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid),
            "msl": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1 AND bank_account_id = $2 AND matched_statement_line_id IS NOT NULL", tid, rid),
            "rekon": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1 AND bank_account_id = $2 AND COALESCE(is_reconciled, false)", tid, rid),
            "mb": await c.fetchval("SELECT count(*) FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid),
            "tautan_aktif": await c.fetchval(f"SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND dilepas_pada IS NULL AND mutasi_bank_id IN {mut}", tid, rid),
            "tautan_total": await c.fetchval(f"SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND mutasi_bank_id IN {mut}", tid, rid)}


async def tautan_aktif(c, tid, rid, mid):
    return [(str(r["bank_transaction_id"]), r["nominal_alokasi"]) for r in await c.fetch(
        "SELECT bank_transaction_id, nominal_alokasi FROM mutasi_bank_tautan WHERE tenant_id = $1 AND mutasi_bank_id = $2 AND dilepas_pada IS NULL ORDER BY bank_transaction_id", tid, UUID(mid))]


async def status_mutasi(c, tid, mid):
    return await c.fetchrow("SELECT status, cara, alasan, diputus_oleh, diputus_pada FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, UUID(mid))


async def laporan(tid, rid):
    return await MR.tak_di_bank(req(tid), rid, dari=None, sampai=None)


def harapan_tak_di_bank(b, ok_kand, awal, akhir):
    batas = akhir - timedelta(days=3)
    return {r["id_mh"] for r in b if r["id_mh"] in ok_kand and awal <= date.fromisoformat(r["tanggal"]) <= batas}


async def periksa(pool, tid, fx_b, fx_j, fx_mut, fx_d):
    b = fx_b
    truth = {x["id_baris"]: x for x in fx_j}
    async with pool.acquire() as c:
        rid = await rekening_uji(c, tid)
        rid2 = await rekening_uji(c, tid, "8888883333")
        dok = {n: await siapkan_dokumen(c, tid, n, fx_mut[n]) for n in BERKAS}
        bt_ke_mh, mh_ke_bt = await benih(c, tid, rid, b)
    for n in BERKAS:
        st, h = await impor(tid, rid, dok[n])
        assert st == 201, (n, st, h)
    async with pool.acquire() as c:
        rows = [dict(r) for r in await c.fetch("SELECT id, tanggal, keterangan, arah, nominal, status, baris_mentah FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)]
    id_baris = {str(r["id"]): next(csv.reader([r["baris_mentah"]]))[-1] for r in rows}
    mid_ib = {v: k for k, v in id_baris.items()}
    nominal_mid = {str(r["id"]): D(str(r["nominal"])) for r in rows}
    baris = [{"id": str(r["id"]), "tanggal": r["tanggal"], "nominal": D(str(r["nominal"])), "arah": r["arah"], "keterangan": r["keterangan"], "status": r["status"]} for r in rows]
    awal_t, akhir_t = min(r["tanggal"] for r in rows), max(r["tanggal"] for r in rows)
    ok_kand, batal = mandiri_kandidat(b)
    async with pool.acquire() as c:
        peta = await TB.tebak(c, tid, rid, baris)
        nol = await hitung2(c, tid, rid)
    cek(f"{tid} S0 benih+impor: {len(rows)} baris, {nol['bt']} bt tenant, tautan 0, matched_statement_line_id 0", len(rows) == 1303 and nol["tautan_total"] == 0 and nol["msl"] == 0)
    yakin = sorted((m for m in peta if peta[m]["keyakinan"] == "yakin"), key=lambda m: id_baris[m])

    # ---------------------------------------------------------------- A serentak (dulu, sebelum keadaan berubah banyak)
    saingan = next((m for m in sorted(peta, key=lambda m: id_baris[m]) if peta[m].get("mungkin_dobel_dengan") and peta[m]["bank_transaction_ids"]
                    and nominal_mid[m] == nominal_mid[peta[m]["mungkin_dobel_dengan"][0]]
                    and peta[peta[m]["mungkin_dobel_dengan"][0]]["bank_transaction_ids"] == peta[m]["bank_transaction_ids"]), None)
    if saingan:
        m2, bt_s = peta[saingan]["mungkin_dobel_dengan"][0], peta[saingan]["bank_transaction_ids"][0]
        (s1, h1), (s2, h2) = await asyncio.gather(cocok(tid, rid, saingan, [bt_s]), cocok(tid, rid, m2, [bt_s]))
        async with pool.acquire() as c:
            jml = await c.fetchval("SELECT COALESCE(sum(nominal_alokasi), 0) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND bank_transaction_id = $2 AND dilepas_pada IS NULL", tid, UUID(bt_s))
        menang = saingan if s1 == 200 else m2
        cek(f"{tid} A serentak: dua BARIS berbeda mengalokasikan dokumen yang SAMA -> tepat satu menang, Σ alokasi == nominal dokumen",
            sorted([s1, s2]) == [200, 422] and jml == nominal_mid[saingan], f"status={s1},{s2} kode={h1.get('code') if s1 != 200 else h2.get('code')} Σ={jml}")
        await lepas(tid, rid, menang)
    else:
        cek(f"{tid} A serentak dua baris/dokumen sama: fixture tak menyediakan pasangan", False, "tak ada pasangan bersaing di peta")
    r_ser = yakin[0]
    bt_ser = peta[r_ser]["bank_transaction_ids"][0]
    (s1, h1), (s2, h2) = await asyncio.gather(cocok(tid, rid, r_ser, [bt_ser]), cocok(tid, rid, r_ser, [bt_ser]))
    async with pool.acquire() as c:
        ta = await tautan_aktif(c, tid, rid, r_ser)
    cek(f"{tid} A serentak: aksi sama pada baris SAMA dua kali -> satu 200 + satu 409 SUDAH_DIPUTUS, satu tautan", sorted([s1, s2]) == [200, 409] and len(ta) == 1
        and (h1 if s1 == 409 else h2)["code"] == "SUDAH_DIPUTUS", f"{s1},{s2} tautan={len(ta)}")
    await lepas(tid, rid, r_ser)
    (s1, h1), (s2, h2) = await asyncio.gather(cocok(tid, rid, r_ser, [bt_ser], kunci="kunci-ser-1"), cocok(tid, rid, r_ser, [bt_ser], kunci="kunci-ser-1"))
    async with pool.acquire() as c:
        ta = await tautan_aktif(c, tid, rid, r_ser)
    cek(f"{tid} A serentak: kunci idempotensi SAMA dua kali -> keduanya 200 dgn jawaban sama, satu tautan", s1 == s2 == 200 and h1 == h2 and len(ta) == 1, f"tautan={len(ta)}")
    await lepas(tid, rid, r_ser)

    # ---------------------------------------------------------------- B cocokkan semua baris yakin (pratinjau == tulis, NOL jurnal)
    async with pool.acquire() as c:
        sebelum_b = await hitung2(c, tid, rid)
        audit_sebelum_b = await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'mutasi_bank' AND \"eventType\" = 'BANK_FEED_COCOKKAN' AND entity_id = ANY($2::uuid[])", tid, [UUID(m) for m in yakin])
    gagal, beda_alokasi, beda_versi, tak_jurnal_prev = [], [], [], True
    for m in yakin:
        ids = peta[m]["bank_transaction_ids"]
        pv = await prev_cocok(tid, rid, m, ids)
        tak_jurnal_prev &= pv["ok"] and pv["jurnal"] == [] and pv["blocks"] == []
        st, h = await cocok(tid, rid, m, ids, versi=pv["payload"]["versi"])
        if st != 200:
            gagal.append((id_baris[m], st, h))
            continue
        async with pool.acquire() as c:
            ta = await tautan_aktif(c, tid, rid, m)
            sm = await status_mutasi(c, tid, m)
        if sorted((p["bank_transaction_id"], D(p["alokasi"])) for p in pv["pasangan"]) != sorted(ta) or sum(a for _, a in ta) != nominal_mid[m] or sm["status"] != "dicatat" or sm["cara"] != "cocok":
            beda_alokasi.append(id_baris[m])
    async with pool.acquire() as c:
        sesudah = await hitung2(c, tid, rid)
    cek(f"{tid} B cocokkan {len(yakin)} baris yakin: semua pratinjau ok (jurnal []) dan tulis 200", not gagal and tak_jurnal_prev and len(yakin) == 19, str(gagal[:2]))
    cek(f"{tid} B pratinjau == tulis: tautan di DB == alokasi pratinjau, Σ == nominal baris, status dicatat/cocok", not beda_alokasi, str(beda_alokasi[:3]))
    cek(f"{tid} B NOL jurnal & bank_transactions baru; matched_statement_line_id TIDAK ditulis; is_reconciled utuh; tautan aktif == {len(yakin)} (+ jejak tautan lepas dari uji serentak)",
        sesudah["je"] == nol["je"] and sesudah["bt"] == nol["bt"] and sesudah["msl"] == 0 and sesudah["rekon"] == nol["rekon"] and sesudah["tautan_aktif"] == len(yakin)
        and sesudah["tautan_total"] == sebelum_b["tautan_total"] + len(yakin), f"sebelum={sebelum_b} sesudah={sesudah}")
    async with pool.acquire() as c:
        n_audit = await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'mutasi_bank' AND \"eventType\" = 'BANK_FEED_COCOKKAN' AND entity_id = ANY($2::uuid[])", tid, [UUID(m) for m in yakin])
    cek(f"{tid} B tiap cocokkan menulis tepat satu riwayat (audit append-only; selisih terhadap sebelum langkah B)", n_audit - audit_sebelum_b == len(yakin), f"sebelum={audit_sebelum_b} sesudah={n_audit}")

    # ---------------------------------------------------------------- C idempotensi, versi basi, baris sudah diputus
    m0 = yakin[0]
    st, h = await cocok(tid, rid, m0, peta[m0]["bank_transaction_ids"])
    cek(f"{tid} C cocokkan baris yang sudah dicocokkan -> 409 SUDAH_DIPUTUS dengan siapa dan kapan", st == 409 and h["code"] == "SUDAH_DIPUTUS" and "Dicocokkan oleh" in h["message"], str(h)[:160])
    dipakai_yakin = {peta[m]["bank_transaction_ids"][0] for m in yakin}
    tak = next(m for m in sorted(peta, key=lambda m: id_baris[m]) if m not in yakin and peta[m]["bank_transaction_ids"] and not peta[m]["gabungan"] and peta[m]["jenis"] == "cocok"
               and peta[m]["keyakinan"] == "periksa" and peta[m]["bank_transaction_ids"][0] not in dipakai_yakin and peta[m]["jumlah_calon"] == 1)
    st, h = await cocok(tid, rid, tak, peta[tak]["bank_transaction_ids"][:1], versi="versi-basi")
    async with pool.acquire() as c:
        sm = await status_mutasi(c, tid, tak)
        ta = await tautan_aktif(c, tid, rid, tak)
    cek(f"{tid} C versi pratinjau basi -> 409 RENCANA_BERUBAH, nol tulisan", st == 409 and h["code"] == "RENCANA_BERUBAH" and sm["status"] == "untuk_ditinjau" and not ta, str(h)[:120])
    st, h = await cocok(tid, rid, tak, [str(uuid.uuid4())])
    cek(f"{tid} C dokumen tak dikenal -> 422 DOKUMEN_TAK_BISA_DICOCOKKAN", st == 422 and h["code"] == "DOKUMEN_TAK_BISA_DICOCOKKAN", str(h)[:120])
    st, h = await panggil(MR.cocokkan(req(tid), rid2, UUID(tak), _bd(peta[tak]["bank_transaction_ids"][:1]), Response()))
    cek(f"{tid} C rekening lain di path -> 404 MUTASI_TAK_ADA (pagar rekening+tenant)", st == 404 and h["code"] == "MUTASI_TAK_ADA", str(h)[:120])
    s_a, ha = await cocok(tid, rid, tak, peta[tak]["bank_transaction_ids"][:1], kunci="kunci-uji-1")
    s_b, hb = await cocok(tid, rid, tak, peta[tak]["bank_transaction_ids"][:1], kunci="kunci-uji-1")
    async with pool.acquire() as c:
        ta = await tautan_aktif(c, tid, rid, tak)
    cek(f"{tid} C replay kunci idempotensi sama -> jawaban SAMA, tak menautkan dua kali", s_a == s_b == 200 and ha == hb and len(ta) == 1, f"{s_a},{s_b} tautan={len(ta)}")
    s_c, hc = await cocok(tid, rid, tak, peta[tak]["bank_transaction_ids"][:1], kunci="kunci-uji-1", versi="lain")
    cek(f"{tid} C kunci sama + isi beda -> 409 IDEMPOTENCY_KEY_REUSED", s_c == 409 and hc["code"] == "IDEMPOTENCY_KEY_REUSED", str(hc)[:120])
    await lepas(tid, rid, tak)

    # ---------------------------------------------------------------- D daftar + riwayat
    async with pool.acquire() as c:
        d = await MR.daftar_mutasi_bank(req(tid), rid, status="dicatat", q=None, dari=None, sampai=None, limit=200, offset=0)
    cek(f"{tid} D daftar: tab Dicatat = {len(yakin)} baris 'Dicocokkan oleh …'; cacah untuk_ditinjau = {1303 - len(yakin)}",
        len(d["data"]) == len(yakin) and all(r["diputus_text"].startswith("Dicocokkan oleh") and r["status_text"] == "Dicatat" for r in d["data"])
        and d["cacah"]["dicatat"] == len(yakin) and d["cacah"]["untuk_ditinjau"] == 1303 - len(yakin), str(d["cacah"]))
    rw = (await MR.riwayat_mutasi_bank(req(tid), rid, UUID(yakin[-1])))["data"]
    cek(f"{tid} D riwayat baris: Diimpor -> Dicocokkan (urut waktu, nama & waktu dari server)", [x["aksi"] for x in rw] == ["diimpor", "cocokkan"] and all(x["oleh_text"] and x["pada_text"] for x in rw), str([x["aksi"] for x in rw]))

    # ---------------------------------------------------------------- E lepaskan (tautan dilepas, tak dihapus; dokumen tak disentuh)
    tiga = yakin[1:4]      # yakin[0] dipakai uji serentak (riwayatnya sengaja lebih panjang)
    async with pool.acquire() as c:
        sebelum = await hitung2(c, tid, rid)
    ok_lepas, kal_ok = True, True
    for m in tiga:
        pv = (await MR.pratinjau_lepaskan(req(tid), rid, UUID(m), LepaskanRequest()))["data"]
        kal_ok &= pv["ok"] and "tetap tercatat" in pv["kalimat"] and pv["jurnal"] == [] and pv["kembali_ke"] == "untuk_ditinjau"
        st, h = await lepas(tid, rid, m, versi=pv["payload"]["versi"])
        ok_lepas &= st == 200
    async with pool.acquire() as c:
        setelah = await hitung2(c, tid, rid)
        sm = await status_mutasi(c, tid, tiga[0])
        rw = (await MR.riwayat_mutasi_bank(req(tid), rid, UUID(tiga[0])))["data"]
    cek(f"{tid} E lepaskan 3 baris: pratinjau 'dokumen tetap tercatat', tulis 200", ok_lepas and kal_ok)
    cek(f"{tid} E tautan DILEPAS bukan dihapus (total tetap, aktif −3), bt & jurnal tak berubah, status kembali untuk_ditinjau, cara kosong",
        setelah["tautan_total"] == sebelum["tautan_total"] and setelah["tautan_aktif"] == sebelum["tautan_aktif"] - 3 and setelah["bt"] == sebelum["bt"] and setelah["je"] == sebelum["je"]
        and sm["status"] == "untuk_ditinjau" and sm["cara"] is None and sm["diputus_pada"] is None, str(setelah))
    cek(f"{tid} E riwayat: Diimpor -> Dicocokkan -> Dilepas", [x["aksi"] for x in rw] == ["diimpor", "cocokkan", "lepaskan"], str([x["aksi"] for x in rw]))
    pv = await prev_cocok(tid, rid, tiga[0], peta[tiga[0]]["bank_transaction_ids"])
    st, h = await cocok(tid, rid, tiga[0], peta[tiga[0]]["bank_transaction_ids"], versi=pv["payload"]["versi"])
    cek(f"{tid} E dicocokkan ulang sesudah dilepas boleh (tautan lama tetap sebagai jejak)", st == 200)
    st, h = await lepas(tid, rid, tak)
    cek(f"{tid} E lepaskan baris yang belum diputus -> 409 BELUM_DIPUTUS", st == 409 and h["code"] == "BELUM_DIPUTUS", str(h)[:100])

    # ---------------------------------------------------------------- F kecualikan
    ku = [m for m in sorted(peta, key=lambda m: id_baris[m]) if m not in yakin and peta[m]["jenis"] == "kosong"][:2]
    pv = (await MR.pratinjau_kecualikan(req(tid), rid, UUID(ku[0]), KecualikanRequest(alasan="lain")))["data"]
    cek(f"{tid} F kecualikan 'lain' tanpa catatan -> blok CATATAN_WAJIB (pratinjau)", not pv["ok"] and pv["blocks"][0]["code"] == "CATATAN_WAJIB")
    st1, h1 = await kecuali(tid, rid, ku[0], "duplikat")
    st2, h2 = await kecuali(tid, rid, ku[1], "lain", "salah impor bulan lalu")
    async with pool.acquire() as c:
        s1_, s2_ = await status_mutasi(c, tid, ku[0]), await status_mutasi(c, tid, ku[1])
        a_ = await hitung2(c, tid, rid)
    cek(f"{tid} F kecualikan: status dikecualikan/cara kecualikan, alasan tersimpan, NOL jurnal", st1 == st2 == 200 and s1_["status"] == "dikecualikan" and s2_["alasan"] == "lain: salah impor bulan lalu"
        and a_["je"] == nol["je"] and a_["bt"] == nol["bt"], f"{st1},{st2}")
    st, h = await cocok(tid, rid, ku[0], [str(uuid.uuid4())])
    cek(f"{tid} F baris dikecualikan tak bisa dicocokkan -> 409 SUDAH_DIPUTUS", st == 409 and h["code"] == "SUDAH_DIPUTUS")
    await lepas(tid, rid, ku[0])
    await lepas(tid, rid, ku[1])
    async with pool.acquire() as c:
        s1_ = await status_mutasi(c, tid, ku[0])
    cek(f"{tid} F lepaskan kecualikan -> kembali untuk_ditinjau", s1_["status"] == "untuk_ditinjau" and s1_["alasan"] is None)

    # ---------------------------------------------------------------- G laporan tak-di-bank terhadap himpunan MANDIRI
    async with pool.acquire() as c:
        ikat = [str(r["id"]) for r in await c.fetch("SELECT id FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND status = 'dicatat' AND cara = 'cocok'", tid, rid)]
    for m in ikat:
        await lepas(tid, rid, m)
    lap0 = await laporan(tid, rid)
    ada0 = {bt_ke_mh[x["bt_id"]] for x in lap0["data"]}
    hr0 = harapan_tak_di_bank(b, ok_kand, awal_t, akhir_t)
    cek(f"{tid} G laporan tanpa satu pun tautan == semua catatan POSTED tanpa pasangan batal dalam rentang berkas −3 hari ({len(hr0)} dokumen)", ada0 == hr0 and len(hr0) > 200, f"api={len(ada0)} mandiri={len(hr0)} selisih={sorted(ada0 ^ hr0)[:5]}")
    cek(f"{tid} G rentang dinilai = berkas {awal_t}..{akhir_t} dikurangi 3 hari; catatan mengatakannya; total Decimal Σ server",
        lap0["rentang_berkas"] == {"dari": str(awal_t), "sampai": str(akhir_t)} and lap0["dinilai"]["sampai"] == str(akhir_t - timedelta(days=3)) and any("3 hari terakhir" in x for x in lap0["catatan"])
        and D(lap0["total"]["masuk"]) == sum((D(x["nominal"]) for x in lap0["data"] if x["arah"] == "masuk"), D(0)) and lap0["total"]["jumlah"] == len(lap0["data"]))
    cek(f"{tid} G pasangan void (22 dokumen asli+pembalik) TAK PERNAH masuk laporan", not (ada0 & batal))
    # keputusan pemilik diterapkan lewat aksi: cocokkan tiap baris c-jawaban 'cocok' ke dokumennya
    cocok_dobel = collections.Counter(x["id_mh"] for x in fx_j if x["aksi_benar"].startswith("cocok") and x["id_mh"])
    pakai, gagal2, rinci = 0, collections.Counter(), []
    for x in sorted((x for x in fx_j if x["aksi_benar"].startswith("cocok") and x["id_mh"]), key=lambda x: x["id_baris"]):
        m, bt = mid_ib[x["id_baris"]], mh_ke_bt[x["id_mh"]]
        st, h = await cocok(tid, rid, m, [bt], alokasi={bt: nominal_mid[m]} if cocok_dobel[x["id_mh"]] > 1 else None)
        if st == 200:
            pakai += 1
        else:
            gagal2[h["code"]] += 1
            rinci.append((x["id_baris"], x["id_mh"], h["code"], str(nominal_mid[m]), next(r["nominal"] for r in b if r["id_mh"] == x["id_mh"]), x["aksi_benar"]))
    print(f"INFO {tid} jawaban benar 'cocok' diterapkan lewat aksi: {pakai} berhasil, diblok: {dict(gagal2)}", flush=True)
    for rr in rinci:
        print(f"INFO {tid}   diblok {rr[0]} dok={rr[1]} kode={rr[2]} nominal_baris={rr[3]} nominal_dok={rr[4]} jawaban={rr[5]}", flush=True)
    async with pool.acquire() as c:
        terikat = {bt_ke_mh[str(r["bank_transaction_id"])] for r in await c.fetch(
            "SELECT bank_transaction_id FROM mutasi_bank_tautan WHERE tenant_id = $1 AND dilepas_pada IS NULL AND mutasi_bank_id IN (SELECT id FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2)", tid, rid)}
    lap1 = await laporan(tid, rid)
    ada1 = {bt_ke_mh[x["bt_id"]] for x in lap1["data"]}
    hr1 = hr0 - terikat
    cek(f"{tid} G sesudah {pakai} keputusan cocok: laporan == himpunan mandiri (dokumen tanpa pasangan bank) -- {len(ada1)} dokumen", ada1 == hr1 and not (ada1 & terikat), f"api={len(ada1)} mandiri={len(hr1)} selisih={sorted(ada1 ^ hr1)[:6]}")
    ada_bank = {x["id_mh"] for x in fx_j if x["aksi_benar"].startswith("cocok") and x["id_mh"]}
    print(f"INFO {tid} laporan {len(ada1)} dokumen; dokumen tanpa jawaban cocok di golden (dalam rentang): {len(hr0 - ada_bank)}; irisan laporan∩jawaban-cocok: {len(ada1 & ada_bank)} (dokumen yang aksinya diblok)", flush=True)
    # d-kasus X2/X3/X7 (hanya bila terpetakan ke nomor dokumen b-catatan)
    peta_d = {}
    for dk in fx_d:
        if dk["kode"] in ("X2", "X3", "X7"):
            token = re.findall(r"[A-Z]{2,4}-\d{4}-\d{2,4}", dk["dokumen_mh"])
            peta_d[dk["kode"]] = sorted({r["id_mh"] for r in b for t in token if t in (r["nomor_jurnal"], r["nomor_referensi"], r["faktur_terkait"], r["tagihan_terkait"])})
    for kode, idm in peta_d.items():
        if not idm:
            print(f"INFO {tid} d-kasus {kode}: TAK TERPETAKAN ke catatan b-catatan (nomor dokumen d-kasus tak sama dengan nomor jurnal) -- tidak diuji", flush=True)
            continue
        muncul = [i for i in idm if i in ada1]
        cek(f"{tid} G d-kasus {kode} terpetakan ({len(idm)} dokumen) dan MUNCUL di laporan bila tak punya pasangan bank", set(muncul) == {i for i in idm if i in hr1}, f"dokumen={idm} muncul={muncul}")
    # lepaskan satu -> dokumennya muncul kembali
    if terikat:
        async with pool.acquire() as c:
            t0 = await c.fetchrow("SELECT t.bank_transaction_id, t.mutasi_bank_id FROM mutasi_bank_tautan t WHERE t.tenant_id = $1 AND t.dilepas_pada IS NULL AND t.mutasi_bank_id IN "
                                  "(SELECT id FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2) ORDER BY t.id LIMIT 1", tid, rid)
        sebelum_n = {x["bt_id"] for x in (await laporan(tid, rid))["data"]}
        await lepas(tid, rid, str(t0["mutasi_bank_id"]))
        sesudah_n = {x["bt_id"] for x in (await laporan(tid, rid))["data"]}
        cek(f"{tid} G melepaskan keputusan membuat dokumennya muncul lagi di laporan", str(t0["bank_transaction_id"]) not in sebelum_n and str(t0["bank_transaction_id"]) in sesudah_n)
    kosong = await MR.tak_di_bank(req(tid), rid2, dari=None, sampai=None)
    cek(f"{tid} G rekening tanpa berkas impor -> daftar kosong + catatan 'Belum ada berkas' (bukan 'hilang semua')", kosong["data"] == [] and kosong["rentang_berkas"] is None and "Belum ada berkas" in kosong["catatan"][0])

    # ---------------------------------------------------------------- H penjaga void
    async with pool.acquire() as c:
        terikat_bt = [str(r["bank_transaction_id"]) for r in await c.fetch(
            "SELECT bank_transaction_id FROM mutasi_bank_tautan WHERE tenant_id = $1 AND dilepas_pada IS NULL AND mutasi_bank_id IN (SELECT id FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2)", tid, rid)]
    per_jenis = {}
    for bt in terikat_bt:
        per_jenis.setdefault(next(r["jenis_asal"] for r in b if r["id_mh"] == bt_ke_mh[bt]), bt)
    hasil_j = {}
    async with pool.acquire() as c:
        for jenis, bt in per_jenis.items():
            try:
                await J.tolak_void_bila_terekonsiliasi(c, tid, bt)
                hasil_j[jenis] = "LOLOS"
            except HTTPException as e:
                hasil_j[jenis] = e.detail["code"]
    cek(f"{tid} H penjaga void menolak dokumen bertaut di Bank Feed untuk SETIAP jenis sumber yang terikat ({sorted(hasil_j)}) -> BANK_TX_BERTAUT",
        len(hasil_j) >= 3 and all(v == "BANK_TX_BERTAUT" for v in hasil_j.values()), str(hasil_j))
    async with pool.acquire() as c:
        jml_tautan = {x: await c.fetchval("SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND bank_transaction_id = $2 AND dilepas_pada IS NULL", tid, UUID(x)) for x in terikat_bt}
    satu = [x for x in terikat_bt if jml_tautan[x] == 1]
    bt_manual = per_jenis["BANK_TRANSACTION"] if per_jenis.get("BANK_TRANSACTION") in satu else satu[0]
    async with pool.acquire() as c:
        bl = Blok(True)
        await KT.rencana_void_manual(c, tid, UUID(bt_manual), "uji", bl)
        kode_bl = [x["code"] for x in bl.daftar]
    cek(f"{tid} H pemanggil NYATA kasbank_tulis.rencana_void_manual: blok BANK_TX_BERTAUT untuk bt bertaut", "BANK_TX_BERTAUT" in kode_bl, str(kode_bl))
    async with pool.acquire() as c:
        mid_terikat = str(await c.fetchval("SELECT mutasi_bank_id FROM mutasi_bank_tautan WHERE tenant_id = $1 AND bank_transaction_id = $2 AND dilepas_pada IS NULL LIMIT 1", tid, UUID(bt_manual)))
    await lepas(tid, rid, mid_terikat)
    async with pool.acquire() as c:
        bl = Blok(True)
        await KT.rencana_void_manual(c, tid, UUID(bt_manual), "uji", bl)
        kode_bl2 = [x["code"] for x in bl.daftar]
        try:
            await J.tolak_void_bila_terekonsiliasi(c, tid, bt_manual)
            dilepas = "LOLOS"
        except HTTPException as e:
            dilepas = e.detail["code"]
    cek(f"{tid} H sesudah dilepas: penjaga tak lagi menolak (BANK_TX_BERTAUT hilang dari blok dan fungsi)", "BANK_TX_BERTAUT" not in kode_bl2 and dilepas != "BANK_TX_BERTAUT", f"{kode_bl2} {dilepas}")

    # ---------------------------------------------------------------- I tak ada jurnal/bt baru sepanjang unit
    async with pool.acquire() as c:
        akhir = await hitung2(c, tid, rid)
    cek(f"{tid} I NOL jurnal dan NOL bank_transactions baru sepanjang cocokkan/kecualikan/lepaskan; matched_statement_line_id tetap 0; is_reconciled utuh",
        akhir["je"] == nol["je"] and akhir["bt"] == nol["bt"] and akhir["msl"] == 0 and akhir["rekon"] == nol["rekon"] and akhir["mb"] == nol["mb"], f"awal={nol} akhir={akhir}")


async def utama():
    perlu = BERKAS + ["b-catatan-milkyhoop.csv", "c-jawaban-benar.csv", "d-kasus-pengecualian.csv"]
    for n in perlu:
        if not os.path.isfile(f"{FIX}/{n}"):
            print(f"TOLAK: fixture golden hilang: {n}")
            return 4
    fx_mut = {n: open(f"{FIX}/{n}", "rb").read() for n in BERKAS}
    fx_b, fx_j, fx_d = baca_csv("b-catatan-milkyhoop.csv"), baca_csv("c-jawaban-benar.csv"), baca_csv("d-kasus-pengecualian.csv")
    pool = await MR.get_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1c1"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid, fx_b, fx_j, fx_mut, fx_d)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
