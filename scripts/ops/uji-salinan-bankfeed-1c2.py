"""BF-1c2 -- uji CATAT (uang_masuk/uang_keluar/pribadi) + BATALKAN-CATATAN + biaya BI-FAST di SALINAN (klon milkydb), golden ANONIM di /root/fixtures/bankfeed_golden (LUAR repo).
DUA POHON KODE: sisi LAMA (pohon sebelum ekstraksi, uji-salinan-bankfeed-1c2-lama.py) dan BARU (pohon c2) menjalankan Uang Masuk/Keluar + void deterministik pada rekening kembar;
tanda tangan jurnal/bank_transactions HARUS identik (bukti ekstraksi penulis). Lalu aksi Bank Feed lewat RUTE asli pada baris golden: pratinjau==tulis, jurnal sama dgn rute lama,
pribadi (Prive/Modal Pemilik, putusan pemilik), BI-FAST biaya (pola ketat; transfer BI-FAST biasa bukan biaya), blok berkode, batal = jurnal pembalik sama dgn void lama, serentak,
replay idempotensi, void langsung dokumen bertaut = 409 BANK_TX_BERTAUT (rute nyata), invariants. Tulis HANYA di salinan.
"""
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
    r.state.user = {"tenant_id": tid, "user_id": USER, "username": "uji-bf1c2"}
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


import json  # noqa: E402

sys.path.insert(0, "/tanda")
import uji_c2_tanda as UT  # noqa: E402
from app.routers import bank_accounts as BAR  # noqa: E402
from app.routers import kasbank_v2 as KV2  # noqa: E402
from app.schemas.bank_accounts import CreateManualTransactionRequest  # noqa: E402
from app.schemas.bankfeed import BatalkanCatatanRequest, CatatRequest  # noqa: E402
from app.schemas.kasbank_v2 import VoidTransactionRequest  # noqa: E402
from app.services import bankfeed_daftar as BDF  # noqa: E402

USER = UT.USER      # created_by/voided_by SAMA dengan sisi lama (tanda tangan membandingkan penanda void)


async def catat(tid, rid, mid, jenis, akun_id=None, kontak=None, catatan=None, kunci=None, versi=None):
    b = CatatRequest(jenis=jenis, akun_id=UUID(str(akun_id)) if akun_id else None, kontak=kontak, catatan=catatan, versi=versi)
    return await panggil(MR.catat(req(tid, kunci), rid, UUID(mid), b, Response()))


async def prev_catat(tid, rid, mid, jenis, akun_id=None, catatan=None):
    b = CatatRequest(jenis=jenis, akun_id=UUID(str(akun_id)) if akun_id else None, catatan=catatan)
    return (await MR.pratinjau_catat(req(tid), rid, UUID(mid), b))["data"]


async def batalkan(tid, rid, mid, alasan, versi=None, kunci=None):
    return await panggil(MR.batalkan_catatan(req(tid, kunci), rid, UUID(mid), BatalkanCatatanRequest(alasan=alasan, versi=versi), Response()))


async def prev_batalkan(tid, rid, mid, alasan):
    return (await MR.pratinjau_batalkan_catatan(req(tid), rid, UUID(mid), BatalkanCatatanRequest(alasan=alasan)))["data"]


def tanpa_rb(bt):
    """bank_transactions tanpa saldo berjalan (rekening berbeda memiliki riwayat berbeda; saldo berjalan diperiksa terpisah)."""
    return {k: v for k, v in bt.items() if k != "running_balance"}


async def periksa(pool, tid, fx_b, fx_j, fx_mut):
    b = fx_b
    async with pool.acquire() as c:
        rid = await rekening_uji(c, tid)
        rid_k, coa_k = await UT.rekening_kembar(c, tid, "8888881112")
        dok = {n: await siapkan_dokumen(c, tid, n, fx_mut[n]) for n in BERKAS}
        bt_ke_mh, mh_ke_bt = await benih(c, tid, rid, b)
        coa_rid = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", rid, tid)
        id_5_20850, id_prive, id_modal, id_4_90000 = [await UT.kode_ke_id(c, tid, k) for k in ("5-20850", "3-40000", "3-10100", "4-90000")]
    for n in BERKAS:
        st, h = await impor(tid, rid, dok[n])
        assert st == 201, (n, st, h)
    async with pool.acquire() as c:
        rows = [dict(r) for r in await c.fetch("SELECT id, tanggal, keterangan, arah, nominal, status, baris_mentah FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2", tid, rid)]
    id_baris = {str(r["id"]): next(csv.reader([r["baris_mentah"]]))[-1] for r in rows}
    ket = {str(r["id"]): r["keterangan"] for r in rows}
    nominal_mid = {str(r["id"]): D(str(r["nominal"])) for r in rows}
    arah_mid = {str(r["id"]): r["arah"] for r in rows}
    tgl_mid = {str(r["id"]): r["tanggal"] for r in rows}
    baris = [{"id": str(r["id"]), "tanggal": r["tanggal"], "nominal": D(str(r["nominal"])), "arah": r["arah"], "keterangan": r["keterangan"], "status": r["status"]} for r in rows]
    async with pool.acquire() as c:
        peta = await TB.tebak(c, tid, rid, baris)

    # ---------------------------------------------------------------- A: ekstraksi penulis = jurnal IDENTIK dgn handler lama (dua pohon kode, rekening kembar)
    baru = await UT.jalankan(pool, tid, coa_k, rid_k, BAR.create_manual_transaction, KV2.void_transaction, req, Response, CreateManualTransactionRequest, VoidTransactionRequest)
    baru = json.loads(json.dumps(baru, sort_keys=True, default=str))
    lama = UT.muat(f"/sig/lama_{tid}.json")
    beda = next((i for i, (x, y) in enumerate(zip(lama, baru)) if x != y), None)
    cek(f"{tid} A LAMA vs BARU: {len(baru)} entri tanda tangan (Uang Masuk/Keluar, void, cermin) IDENTIK -- jurnal, baris jurnal, bank_transactions, penanda void",
        len(lama) == len(baru) == len(UT.OPS) + 2 * len(UT.VOIDS) and beda is None, f"entri beda #{beda}: lama={json.dumps(lama[beda], default=str)[:300] if beda is not None else ''} baru={json.dumps(baru[beda], default=str)[:300] if beda is not None else ''}")
    async with pool.acquire() as c:
        dasar = await hitung2(c, tid, rid)

    # ---------------------------------------------------------------- B: BI-FAST (B) pada data golden
    biaya_mandiri = {m for m in ket if re.match(r"^BI-FAST DB BIF BIAYA TXN\b", ket[m]) and nominal_mid[m] == D("2500") and arah_mid[m] == "keluar"}
    bf_peta = {m for m in peta if peta[m]["jenis"] == "catat" and peta[m]["dasar"] == "bawaan_bca" and re.match(r"^BI-FAST", ket[m])}
    cek(f"{tid} B BI-FAST: baris BIAYA TXN yang ditebak == himpunan mandiri ({len(biaya_mandiri)} baris, Rp{sum(nominal_mid[m] for m in biaya_mandiri):,.0f}); semuanya catat/periksa/5-20850, tak ada yang yakin atau massal",
        bf_peta == biaya_mandiri and len(biaya_mandiri) > 0 and all(peta[m]["keyakinan"] == "periksa" and peta[m]["akun"]["kode"] == "5-20850" and peta[m]["layak_massal"] is False for m in bf_peta), f"peta={len(bf_peta)} mandiri={len(biaya_mandiri)}")
    transfer_biasa = [m for m in ket if re.match(r"^BI-FAST (DB|CR) BIF TRANSFER", ket[m])]
    cek(f"{tid} B transfer BI-FAST biasa ({len(transfer_biasa)} baris DB+CR) TIDAK ada yang diusulkan sebagai biaya/catat", len(transfer_biasa) > 100 and all(peta[m]["jenis"] != "catat" for m in transfer_biasa))
    cek(f"{tid} B biaya BI-FAST tak mengubah baris yakin: tetap 19 yakin benar, tak ada catat yang yakin",
        sum(1 for m in peta if peta[m]["keyakinan"] == "yakin") == 19 and not any(peta[m]["jenis"] == "catat" and peta[m]["keyakinan"] == "yakin" for m in peta))

    # ---------------------------------------------------------------- C: catat uang_keluar (BIAYA ADM nyata) -- pratinjau == tulis, jurnal lewat penulis yang ada
    adm = next(m for m in sorted(ket, key=lambda m: id_baris[m]) if ket[m].upper().startswith("BIAYA ADM"))
    pv = await prev_catat(tid, rid, adm, "uang_keluar", akun_id=str(id_5_20850))
    nom = nominal_mid[adm]
    j0, j1 = (pv["jurnal"] + [{}, {}])[:2]
    cek(f"{tid} C pratinjau catat uang_keluar BIAYA ADM Rp{nom:,.0f}: ok, jurnal 2 baris Dr 5-20850 / Cr rekening sebesar nominal, saldo bergerak, kalimat dari server",
        pv["ok"] and len(pv["jurnal"]) == 2 and (j0["account_id"], j0["debit"], j0["credit"]) == (str(id_5_20850), str(nom), "0.00") and (j1["account_id"], j1["debit"], j1["credit"]) == (str(coa_rid), "0.00", str(nom))
        and len(pv["saldo"]) == 1 and "Uang keluar" in pv["kalimat"], str(pv["jurnal"])[:200] + str(pv["blocks"]))
    st, h = await catat(tid, rid, adm, "uang_keluar", akun_id=str(id_5_20850), versi=pv["payload"]["versi"])
    async with pool.acquire() as c:
        sm = await c.fetchrow("SELECT status, cara, akun_id FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, UUID(adm))
        ta = await tautan_aktif(c, tid, rid, adm)
        bt_adm = UUID(h["data"]["bank_transaction_id"]) if st == 200 else None
        tnd_adm = await UT.tanda_bt(c, tid, bt_adm, coa_rid) if st == 200 else None
        d1 = await hitung2(c, tid, rid)
    cek(f"{tid} C tulis catat: 200, status dicatat/cara catat/akun 5-20850, tautan ke bt HASIL sebesar nominal baris, jurnal POSTED, bt MANUAL POSTED bertanda −nominal, +1 jurnal +1 bt",
        st == 200 and sm["status"] == "dicatat" and sm["cara"] == "catat" and sm["akun_id"] == id_5_20850 and ta == [(str(bt_adm), nom)] and tnd_adm["je"]["status"] == "POSTED"
        and tnd_adm["bt"]["origin_type"] == "MANUAL" and tnd_adm["bt"]["status"] == "POSTED" and tnd_adm["bt"]["amount"] == str(-nom) and d1["je"] == dasar["je"] + 1 and d1["bt"] == dasar["bt"] + 1
        and d1["msl"] == 0 and d1["rekon"] == 0, f"{st} {h if st != 200 else ''} {d1} dasar={dasar}")
    # identik dgn rute lama (Uang Masuk/Keluar) untuk input yang sama pada rekening kembar
    async with pool.acquire() as c:
        r_lama = await BAR.create_manual_transaction(req(tid), rid_k, CreateManualTransactionRequest(direction="out", amount=int(nom), transaction_date=tgl_mid[adm], description=BDF.ringkas_keterangan(ket[adm], 500), contra_account_id=str(id_5_20850)), Response())
        t_lama = await UT.tanda_bt(c, tid, UUID(r_lama["data"]["id"]), coa_k)
    cek(f"{tid} C jurnal catat Bank Feed == jurnal rute Uang Keluar lama pada input sama (bt, jurnal, baris jurnal; saldo berjalan rekening berbeda dikecualikan)",
        tanpa_rb(tnd_adm["bt"]) == tanpa_rb(t_lama["bt"]) and tnd_adm["je"] == t_lama["je"] and tnd_adm["garis"] == t_lama["garis"], f"bf={tnd_adm['garis']} lama={t_lama['garis']}")
    async with pool.acquire() as c:
        sblm = await c.fetchval("SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_lines jl JOIN journal_entries e ON e.id = jl.journal_id WHERE e.tenant_id = $1 AND e.status = 'POSTED' AND jl.account_id = $2 AND e.id <> $3", tid, coa_rid, (await c.fetchval("SELECT journal_id FROM bank_transactions WHERE tenant_id = $1 AND id = $2", tid, bt_adm)))
    cek(f"{tid} C saldo berjalan bt hasil == saldo jurnal rekening sebelumnya + nominal bertanda (journal-derived)", D(tnd_adm["bt"]["running_balance"]) == D(sblm) - nom, f"{tnd_adm['bt']['running_balance']} vs {sblm} - {nom}")

    # ---------------------------------------------------------------- D/E/F: uang_masuk, pribadi (putusan pemilik), BI-FAST biaya
    kos_masuk = [m for m in sorted(peta, key=lambda m: id_baris[m]) if peta[m]["jenis"] == "kosong" and arah_mid[m] == "masuk" and nominal_mid[m] == nominal_mid[m].to_integral_value()]
    kos_keluar = [m for m in sorted(peta, key=lambda m: id_baris[m]) if peta[m]["jenis"] == "kosong" and arah_mid[m] == "keluar" and nominal_mid[m] == nominal_mid[m].to_integral_value()]
    m_in, m_pk, m_pm = kos_masuk[0], kos_keluar[0], kos_masuk[1]
    st, h_in = await catat(tid, rid, m_in, "uang_masuk", akun_id=str(id_4_90000), kontak="Budi uji")
    st_pk, h_pk = await catat(tid, rid, m_pk, "pribadi")
    st_pm, h_pm = await catat(tid, rid, m_pm, "pribadi")
    async with pool.acquire() as c:
        t_in = await UT.tanda_bt(c, tid, UUID(h_in["data"]["bank_transaction_id"]), coa_rid)
        t_pk = await UT.tanda_bt(c, tid, UUID(h_pk["data"]["bank_transaction_id"]), coa_rid)
        t_pm = await UT.tanda_bt(c, tid, UUID(h_pm["data"]["bank_transaction_id"]), coa_rid)
        s_pk, s_pm = await status_mutasi(c, tid, m_pk), await c.fetchrow("SELECT status, cara, akun_id FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, UUID(m_pm))
        a_pk = await c.fetchval("SELECT akun_id FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, UUID(m_pk))
    cek(f"{tid} D uang_masuk: akun lawan = PILIHAN PENGGUNA (akun_id dikirim, bukan bawaan sistem): jurnal Dr rekening / Cr akun pilihan (4-90000), bt deposit bertanda +nominal, pihak tersimpan",
        st == 200 and t_in["garis"][0][1] == "BANK" and t_in["garis"][1][1] == "4-90000" and t_in["bt"]["transaction_type"] == "deposit" and t_in["bt"]["payee_payer"] == "Budi uji"
        and t_in["bt"]["amount"] == str(nominal_mid[m_in]), str(t_in["garis"]))
    cek(f"{tid} E pribadi KELUAR (putusan pemilik): Dr 3-40000 Prive / Cr rekening; cara pribadi; akun_id = Prive",
        st_pk == 200 and t_pk["garis"][0][1] == "3-40000" and t_pk["garis"][1][1] == "BANK" and s_pk["cara"] == "pribadi" and a_pk == id_prive, str(t_pk["garis"]))
    cek(f"{tid} E pribadi MASUK (putusan pemilik): Dr rekening / Cr 3-10100 Modal Pemilik; cara pribadi; akun_id = Modal Pemilik",
        st_pm == 200 and t_pm["garis"][0][1] == "BANK" and t_pm["garis"][1][1] == "3-10100" and s_pm["cara"] == "pribadi" and s_pm["akun_id"] == id_modal, str(t_pm["garis"]))
    bf_m = sorted(biaya_mandiri, key=lambda m: id_baris[m])[0]
    pv = await prev_catat(tid, rid, bf_m, "uang_keluar", akun_id=str(id_5_20850))
    st_bf, h_bf = await catat(tid, rid, bf_m, "uang_keluar", akun_id=str(id_5_20850), versi=pv["payload"]["versi"])
    async with pool.acquire() as c:
        t_bf = await UT.tanda_bt(c, tid, UUID(h_bf["data"]["bank_transaction_id"]), coa_rid)
    cek(f"{tid} F biaya BI-FAST Rp2.500 dicatat (pengguna menekan): Dr 5-20850 Biaya Administrasi Bank / Cr rekening, bt −2500", pv["ok"] and st_bf == 200 and t_bf["garis"][0][1] == "5-20850" and t_bf["garis"][1][1] == "BANK" and t_bf["bt"]["amount"] == "-2500.00", str(t_bf["garis"]))

    # ---------------------------------------------------------------- G: blok berkode (pratinjau + tulis tak menulis)
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    cocok_row = next(m for m in sorted(peta, key=lambda m: id_baris[m]) if peta[m]["keyakinan"] == "yakin")
    await cocok(tid, rid, cocok_row, peta[cocok_row]["bank_transaction_ids"])
    ujian = [
        ("JENIS_BELUM_TERSEDIA", await catat(tid, rid, kos_keluar[2], "bayar_tagihan")),
        ("JENIS_TAK_SESUAI_ARAH", await catat(tid, rid, kos_keluar[2], "uang_masuk", akun_id=str(id_4_90000))),
        ("AKUN_WAJIB", await catat(tid, rid, kos_keluar[2], "uang_keluar")),
        ("AKUN_WAJIB", await catat(tid, rid, kos_masuk[2], "uang_masuk")),      # akun bawaan HANYA untuk pribadi dan BI-FAST; uang masuk/keluar = pilihan pengguna
        ("AKUN_TETAP", await catat(tid, rid, kos_keluar[2], "pribadi", akun_id=str(id_5_20850))),
        ("SUDAH_DIPUTUS", await catat(tid, rid, adm, "uang_keluar", akun_id=str(id_5_20850))),
        ("SUDAH_DIPUTUS", await catat(tid, rid, cocok_row, "uang_keluar", akun_id=str(id_5_20850))),
        ("PAKAI_LEPASKAN", await batalkan(tid, rid, cocok_row, "salah cocok")),
        ("BELUM_DIPUTUS", await batalkan(tid, rid, kos_keluar[2], "belum ada")),
        ("PAKAI_BATALKAN", await lepas(tid, rid, adm)),
        ("SUDAH_DIPUTUS", await cocok(tid, rid, adm, peta[cocok_row]["bank_transaction_ids"])),
    ]
    async with pool.acquire() as c:
        pasca = await hitung2(c, tid, rid)
    cek(f"{tid} G blok berkode lewat API (uang masuk/keluar tanpa akun TIDAK memakai akun bawaan): " + ", ".join(sorted({k for k, _ in ujian})), all(s != 200 and h["code"] == k for k, (s, h) in ujian), str([(k, s, h.get("code")) for k, (s, h) in ujian if s == 200 or h.get("code") != k]))
    cek(f"{tid} G blok tidak menulis jurnal/bank_transactions apa pun (selisih hanya dari cocokkan: NOL jurnal)", pasca["je"] == pra["je"] and pasca["bt"] == pra["bt"], f"{pra} -> {pasca}")
    await lepas(tid, rid, cocok_row)

    # ---------------------------------------------------------------- H: batalkan-catatan = jurnal PEMBALIK lewat void yang ada; identik dgn void lama
    pv = await prev_batalkan(tid, rid, adm, "salah catat biaya")
    cek(f"{tid} H pratinjau batalkan: tiga hal terpisah (dokumen dibatalkan / jurnal pembalik / baris kembali), jurnal pembalik menukar debit-kredit, ok",
        pv["ok"] and len(pv["kalimat_tiga"]) == 3 and pv["kalimat_tiga"][0].startswith("Dokumen MT-") and pv["kalimat_tiga"][1].startswith("Jurnal pembalik RV-MT-") and pv["kalimat_tiga"][2] == "Baris kembali ke Untuk ditinjau"
        and {j["account_id"]: (j["debit"], j["credit"]) for j in pv["jurnal"]} == {str(coa_rid): (str(nom), "0.00"), str(id_5_20850): ("0.00", str(nom))}, str(pv["kalimat_tiga"]) + str(pv["jurnal"]))
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    st, hb = await batalkan(tid, rid, adm, "salah catat biaya", versi=pv["payload"]["versi"])
    async with pool.acquire() as c:
        sm = await status_mutasi(c, tid, adm)
        ta_all = await c.fetch("SELECT dilepas_pada FROM mutasi_bank_tautan WHERE tenant_id = $1 AND mutasi_bank_id = $2", tid, UUID(adm))
        asli = await UT.tanda_bt(c, tid, bt_adm, coa_rid)
        cermin_id = await c.fetchval("SELECT id FROM bank_transactions WHERE tenant_id = $1 AND reference_type = 'manual_void' AND reference_id = $2", tid, bt_adm)
        cermin = await UT.tanda_bt(c, tid, cermin_id, coa_rid)
        pasca = await hitung2(c, tid, rid)
        rw = (await MR.riwayat_mutasi_bank(req(tid), rid, UUID(adm)))["data"]
    cek(f"{tid} H tulis batalkan: 200; bt asli VOIDED, jurnal asli TETAP POSTED + dibalik, jurnal pembalik POSTED (reversal_of), cermin bt; baris kembali untuk_ditinjau; tautan DILEPAS bukan dihapus; +1 jurnal +1 bt",
        st == 200 and asli["bt"]["status"] == "VOIDED" and asli["je"]["status"] == "POSTED" and asli["je"]["dibalik"] is True and cermin["je"]["pembalik"] is True and cermin["je"]["status"] == "POSTED"
        and sm["status"] == "untuk_ditinjau" and sm["cara"] is None and len(ta_all) == 1 and ta_all[0]["dilepas_pada"] is not None and pasca["je"] == pra["je"] + 1 and pasca["bt"] == pra["bt"] + 1, f"{st} {hb if st != 200 else ''} {pasca} {pra}")
    cek(f"{tid} H riwayat: Diimpor -> Dicatat -> Dibatalkan", [x["aksi"] for x in rw] == ["diimpor", "catat", "batalkan"], str([x["aksi"] for x in rw]))
    async with pool.acquire() as c:
        r_v = await KV2.void_transaction(req(tid), UUID(r_lama["data"]["id"]), VoidTransactionRequest(reason="salah catat biaya"), Response())
        lama_asli = await UT.tanda_bt(c, tid, UUID(r_lama["data"]["id"]), coa_k)
        lama_cermin_id = await c.fetchval("SELECT id FROM bank_transactions WHERE tenant_id = $1 AND reference_type = 'manual_void' AND reference_id = $2", tid, UUID(r_lama["data"]["id"]))
        lama_cermin = await UT.tanda_bt(c, tid, lama_cermin_id, coa_k)
    cek(f"{tid} H void lewat Bank Feed == void rute lama (jurnal pembalik, baris, cermin bt, penanda VOIDED) untuk input sama",
        tanpa_rb(asli["bt"]) == tanpa_rb(lama_asli["bt"]) and asli["je"] == lama_asli["je"] and asli["garis"] == lama_asli["garis"]
        and {"bt": tanpa_rb(cermin["bt"]), "je": cermin["je"], "garis": cermin["garis"]} == {"bt": tanpa_rb(lama_cermin["bt"]), "je": lama_cermin["je"], "garis": lama_cermin["garis"]},
        f"cermin_bf={cermin} cermin_lama={lama_cermin}")
    async with pool.acquire() as c:
        lap = await laporan(tid, rid)
    ada_lap = {x["bt_id"] for x in lap["data"]}
    cek(f"{tid} H laporan tak-di-bank: dokumen hasil Catat (bertaut) dan yang sudah dibatalkan (asli+pembalik+cermin) TIDAK muncul",
        str(bt_adm) not in ada_lap and str(cermin_id) not in ada_lap and str(UUID(h_in["data"]["bank_transaction_id"])) not in ada_lap)
    st, h2 = await catat(tid, rid, adm, "uang_keluar", akun_id=str(id_5_20850))
    cek(f"{tid} H baris yang catatannya dibatalkan bisa dicatat ulang (dokumen baru)", st == 200 and h2["data"]["bank_transaction_id"] != str(bt_adm))

    # ---------------------------------------------------------------- I: serentak + replay idempotensi
    m_ser = kos_keluar[3]
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    (s1, r1), (s2, r2) = await asyncio.gather(catat(tid, rid, m_ser, "uang_keluar", akun_id=str(id_5_20850)), catat(tid, rid, m_ser, "uang_keluar", akun_id=str(id_5_20850)))
    async with pool.acquire() as c:
        pasca = await hitung2(c, tid, rid)
        ta = await tautan_aktif(c, tid, rid, m_ser)
    cek(f"{tid} I serentak: catat baris SAMA dua kali -> satu 200 + satu 409 SUDAH_DIPUTUS; tepat SATU jurnal dan SATU bt baru, satu tautan",
        sorted([s1, s2]) == [200, 409] and (r1 if s1 == 409 else r2)["code"] == "SUDAH_DIPUTUS" and pasca["je"] == pra["je"] + 1 and pasca["bt"] == pra["bt"] + 1 and len(ta) == 1, f"{s1},{s2} {pra} -> {pasca}")
    m_idem = kos_keluar[4]
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    sa, ra = await catat(tid, rid, m_idem, "uang_keluar", akun_id=str(id_5_20850), kunci="kunci-c2-1")
    sb, rb = await catat(tid, rid, m_idem, "uang_keluar", akun_id=str(id_5_20850), kunci="kunci-c2-1")
    sc_, rc_ = await catat(tid, rid, m_idem, "uang_keluar", akun_id=str(id_5_20850), kunci="kunci-c2-1", catatan="beda")
    async with pool.acquire() as c:
        pasca = await hitung2(c, tid, rid)
    cek(f"{tid} I replay kunci idempotensi sama -> jawaban SAMA, tak membuat jurnal kedua; kunci sama + isi beda -> 409 IDEMPOTENCY_KEY_REUSED",
        sa == sb == 200 and ra == rb and pasca["je"] == pra["je"] + 1 and sc_ == 409 and rc_["code"] == "IDEMPOTENCY_KEY_REUSED", f"{sa},{sb},{sc_} {pra}->{pasca}")

    # ---------------------------------------------------------------- J: penjaga void pada rute NYATA (kasbank_v2.void_transaction) untuk dokumen yang bertaut
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    s_v, r_v2 = await panggil(KV2.void_transaction(req(tid), UUID(h_pk["data"]["bank_transaction_id"]), VoidTransactionRequest(reason="coba void langsung"), Response()))
    async with pool.acquire() as c:
        pasca = await hitung2(c, tid, rid)
    cek(f"{tid} J void LANGSUNG dokumen yang bertaut di Bank Feed lewat rute void nyata -> 409 BANK_TX_BERTAUT, nol tulisan", s_v == 409 and r_v2["code"] == "BANK_TX_BERTAUT" and pasca["je"] == pra["je"] and pasca["bt"] == pra["bt"], f"{s_v} {r_v2}")
    await batalkan(tid, rid, m_pk, "uji lalu batal lewat Bank Feed")
    async with pool.acquire() as c:
        sm = await status_mutasi(c, tid, m_pk)
    cek(f"{tid} J jalan yang benar: Batalkan catatan lewat Bank Feed melepas tautan lalu void sukses; baris kembali", sm["status"] == "untuk_ditinjau")

    # ---------------------------------------------------------------- L: KAS_MINUS pada rekening KAS di klon (saldo kecil, uang keluar melebihi saldo)
    async def mutasi_sintetik(tgl, arah, nominal, teks):
        async with pool.acquire() as c:
            mid = uuid.uuid4()
            await c.execute("INSERT INTO mutasi_bank (id, tenant_id, bank_account_id, tanggal, keterangan, keterangan_normal, arah, nominal, sidik, status) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'untuk_ditinjau')", mid, tid, rid_kas, tgl, teks, teks.lower(), arah, nominal, uuid.uuid4().hex)
            return str(mid)
    async with pool.acquire() as c:
        rid_kas, coa_kas = await UT.rekening_kembar(c, tid, "8888880099", tipe="cash", coa_baru=True)
        id_5_20900 = await UT.kode_ke_id(c, tid, "5-20900")
    await BAR.create_manual_transaction(req(tid), rid_kas, CreateManualTransactionRequest(direction="in", amount=100000, transaction_date=date(2026, 12, 1), description="modal kas uji", contra_account_id=str(id_4_90000)), Response())
    mk1 = await mutasi_sintetik(date(2026, 12, 2), "keluar", D("500000"), "BAYAR KAS MELEBIHI SALDO")
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    pv = (await MR.pratinjau_catat(req(tid), rid_kas, UUID(mk1), CatatRequest(jenis="uang_keluar", akun_id=id_5_20900)))["data"]
    s_k, h_k = await panggil(MR.catat(req(tid), rid_kas, UUID(mk1), CatatRequest(jenis="uang_keluar", akun_id=id_5_20900), Response()))
    async with pool.acquire() as c:
        pasca, sm = await hitung2(c, tid, rid), await c.fetchrow("SELECT status FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, UUID(mk1))
        tk = await c.fetchval("SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND mutasi_bank_id = $2", tid, UUID(mk1))
    cek(f"{tid} L KAS_MINUS: rekening KAS saldo Rp100.000, uang keluar Rp500.000 -> pratinjau blok KAS_MINUS; tulis ditolak KAS_MINUS; NOL jurnal/bt, baris tetap untuk_ditinjau, tanpa tautan",
        "KAS_MINUS" in [x["code"] for x in pv["blocks"]] and s_k == 400 and h_k["code"] == "KAS_MINUS" and pasca["je"] == pra["je"] and pasca["bt"] == pra["bt"] and sm["status"] == "untuk_ditinjau" and tk == 0, f"{s_k} {h_k} {pra}->{pasca}")
    mk2 = await mutasi_sintetik(date(2026, 12, 3), "keluar", D("100000"), "BAYAR KAS PAS SALDO")
    s_ok, h_ok = await panggil(MR.catat(req(tid), rid_kas, UUID(mk2), CatatRequest(jenis="uang_keluar", akun_id=id_5_20900), Response()))
    mk3 = await mutasi_sintetik(date(2026, 12, 4), "keluar", D("1"), "BAYAR KAS SATU RUPIAH")
    s_1, h_1 = await panggil(MR.catat(req(tid), rid_kas, UUID(mk3), CatatRequest(jenis="uang_keluar", akun_id=id_5_20900), Response()))
    cek(f"{tid} L kontrol: uang keluar PAS saldo (Rp100.000) lolos (saldo jadi 0), lalu Rp1 lagi -> KAS_MINUS (bukan 'selalu blok')", s_ok == 200 and s_1 == 400 and h_1["code"] == "KAS_MINUS", f"{s_ok} {s_1} {h_1}")

    # ---------------------------------------------------------------- M: PERIODE TERTUTUP (periode ditutup di klon; tanggal TIDAK digeser diam-diam)
    async def periode(tgl, status):
        async with pool.acquire() as c:
            await c.execute("UPDATE fiscal_periods SET status = $3, closed_at = CASE WHEN $3 = 'CLOSED' THEN now() ELSE NULL END WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, tgl, status)
    m_pt, m_bp = kos_keluar[5], kos_keluar[6]
    await periode(tgl_mid[m_pt], "CLOSED")
    pv = await prev_catat(tid, rid, m_pt, "uang_keluar", akun_id=str(id_5_20850))
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    s_p, h_p = await catat(tid, rid, m_pt, "uang_keluar", akun_id=str(id_5_20850))
    async with pool.acquire() as c:
        pasca, sm = await hitung2(c, tid, rid), await status_mutasi(c, tid, m_pt)
        je_asing = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND journal_date = $2 AND description LIKE '%' || $3 || '%'", tid, tgl_mid[m_pt], BDF.ringkas_keterangan(ket[m_pt], 500)[:20])
    cek(f"{tid} M catat dengan tanggal mutasi {tgl_mid[m_pt]} di periode TERTUTUP: pratinjau blok PERIODE_TERTUTUP; tulis ditolak 403; NOL jurnal/bt; baris tetap untuk_ditinjau; tak ada jurnal bertanggal lain",
        "PERIODE_TERTUTUP" in [x["code"] for x in pv["blocks"]] and s_p == 403 and h_p["code"] == "PERIODE_TERTUTUP" and pasca["je"] == pra["je"] and pasca["bt"] == pra["bt"] and sm["status"] == "untuk_ditinjau" and je_asing == 0, f"{s_p} {h_p}")
    await periode(tgl_mid[m_pt], "OPEN")
    s_ok, _ = await catat(tid, rid, m_pt, "uang_keluar", akun_id=str(id_5_20850))
    cek(f"{tid} M kontrol: periode dibuka kembali -> catat yang sama lolos (blok memang karena periode)", s_ok == 200)
    s_b, h_b = await catat(tid, rid, m_bp, "uang_keluar", akun_id=str(id_5_20850))
    bt_bp = UUID(h_b["data"]["bank_transaction_id"])
    await periode(tgl_mid[m_bp], "CLOSED")
    pv = await prev_batalkan(tid, rid, m_bp, "batal periode tertutup")
    async with pool.acquire() as c:
        pra = await hitung2(c, tid, rid)
    s_c, h_c = await batalkan(tid, rid, m_bp, "batal periode tertutup")
    async with pool.acquire() as c:
        pasca, sm = await hitung2(c, tid, rid), await status_mutasi(c, tid, m_bp)
        ta = await tautan_aktif(c, tid, rid, m_bp)
        asli = await UT.tanda_bt(c, tid, bt_bp, coa_rid)
    cek(f"{tid} M batalkan catatan yang tanggal ASLINYA ({tgl_mid[m_bp]}) kini di periode tertutup: pratinjau blok; tulis ditolak 403 PERIODE_TERTUTUP; NOL jurnal/bt (tak ada pembalik bertanggal lain); baris tetap dicatat; tautan TETAP aktif; dokumen asli tetap POSTED",
        s_b == 200 and "PERIODE_TERTUTUP" in [x["code"] for x in pv["blocks"]] and s_c == 403 and h_c["code"] == "PERIODE_TERTUTUP" and pasca["je"] == pra["je"] and pasca["bt"] == pra["bt"]
        and sm["status"] == "dicatat" and sm["cara"] == "catat" and len(ta) == 1 and asli["bt"]["status"] == "POSTED" and asli["je"]["dibalik"] is False, f"{s_c} {h_c} {pra}->{pasca} tautan={len(ta)}")
    await periode(tgl_mid[m_bp], "OPEN")
    s_bo, _ = await batalkan(tid, rid, m_bp, "batal periode dibuka")
    cek(f"{tid} M kontrol: periode dibuka kembali -> batalkan catatan yang sama lolos", s_bo == 200)

    # ---------------------------------------------------------------- K: invariants akhir
    async with pool.acquire() as c:
        akhir = await hitung2(c, tid, rid)
    cek(f"{tid} K matched_statement_line_id tetap 0 (Bank Feed tak menulis kolom rekonsiliasi lama), is_reconciled utuh, baris mutasi tetap {dasar['mb']}", akhir["msl"] == 0 and akhir["rekon"] == dasar["rekon"] == 0 and akhir["mb"] == dasar["mb"])


async def utama():
    perlu = BERKAS + ["b-catatan-milkyhoop.csv", "c-jawaban-benar.csv"]
    for n in perlu:
        if not os.path.isfile(f"{FIX}/{n}"):
            print(f"TOLAK: fixture golden hilang: {n}")
            return 4
    fx_mut = {n: open(f"{FIX}/{n}", "rb").read() for n in BERKAS}
    fx_b, fx_j = baca_csv("b-catatan-milkyhoop.csv"), baca_csv("c-jawaban-benar.csv")
    pool = await MR.get_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1c2"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        await periksa(pool, tid, fx_b, fx_j, fx_mut)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
