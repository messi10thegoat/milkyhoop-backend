"""Impor mutasi bank (BF-1a; KONTRAK-bankfeed §3). Pratinjau READ ONLY == tulis (penentu SATU: `rencana`); tulis atomik di transaksi PEMANGGIL.

Berkas = jalur Lampiran bersama G2: FE mengunggah lewat POST /api/documents/upload -> document_id; di sini diverifikasi milik tenant, belum dihapus,
diunggah <= 24 jam oleh PENGGUNA YANG SAMA (tak boleh memakai berkas orang lain), lalu dibaca server dari penyimpanan (FE tak mem-parsing).
NOL jurnal (impor hanya mencatat baris rekening koran). Anti-duplikat: sidik unik (tenant, rekening, sidik) + ON CONFLICT DO NOTHING; baris yang
mirip lintas format (tanggal, arah, nominal, saldo sama; sidik beda) masuk `mungkin_duplikat` (tak dibuang diam-diam). Isi berkas dijaga hash
(`hash_isi` di payload pratinjau): berkas berubah antara Tinjau dan Simpan -> 409 BERKAS_BERUBAH."""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from . import bankfeed_parser as P
from .kasbank_tulis import akun as akun_rekening
from .so_riwayat import catat_riwayat

logger = logging.getLogger(__name__)
UMUR_MAKS = timedelta(hours=24)
UKURAN_MAKS = 10 * 1024 * 1024   # sama dengan aturan lampiran bersama
PESAN_TAK_TERSEDIA = "Berkas tak tersedia di penyimpanan. Unggah ulang."

SQL_REKENING = "SELECT id, account_name, account_number, account_type, is_active FROM bank_accounts WHERE id = $1 AND tenant_id = $2"
SQL_DOKUMEN = ("SELECT id, file_name, file_type, file_extension, file_size, storage_type, file_path, uploaded_at, uploaded_by FROM documents "
               "WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL")
SQL_SIDIK_ADA = "SELECT sidik FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND sidik = ANY($3::text[])"
SQL_LINTAS_FORMAT = ("SELECT id, tanggal, arah, nominal, saldo_berkas, keterangan FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 "
                     "AND tanggal BETWEEN $3 AND $4 AND sidik <> ALL($5::text[])")
SQL_IMPOR = """INSERT INTO mutasi_bank_impor (id, tenant_id, bank_account_id, document_id, format, peta_kolom, tanggal_awal, tanggal_akhir,
    saldo_awal_berkas, saldo_akhir_berkas, jumlah_baru, jumlah_duplikat, jumlah_dilewati, dibuat_oleh)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13, $14::uuid)"""
SQL_BARIS = """INSERT INTO mutasi_bank (tenant_id, bank_account_id, impor_id, tanggal, keterangan, keterangan_normal, arah, nominal, saldo_berkas,
    sidik, baris_ke, baris_mentah, duplikat_dari, status)
    SELECT $1, $2, $3, x.tanggal, x.keterangan, x.kn, x.arah, x.nominal, x.saldo, x.sidik, x.baris_ke, x.mentah, x.dup, x.status
    FROM unnest($4::date[], $5::text[], $6::text[], $7::text[], $8::numeric[], $9::numeric[], $10::text[], $11::int[], $12::text[], $13::uuid[],
                $14::text[]) AS x(tanggal, keterangan, kn, arah, nominal, saldo, sidik, baris_ke, mentah, dup, status)
    ON CONFLICT (tenant_id, bank_account_id, sidik) DO NOTHING RETURNING id"""
SQL_LAMPIRAN = """INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id, attachment_type, display_order, attached_by)
    VALUES ($1, $2, 'mutasi_bank_impor', $3, 'statement', 0, $4::uuid)
    ON CONFLICT (document_id, entity_type, entity_id) DO NOTHING"""


def _blok(daftar: list, code: str, message: str) -> None:
    daftar.append({"code": code, "message": message})


async def baca_isi_dokumen(row) -> bytes:
    """Isi berkas dari penyimpanan (boto3 di thread). Dipisah supaya uji bisa mengganti sumbernya."""
    if (row["storage_type"] or "").lower() != "s3" or not row["file_path"]:
        raise HTTPException(status_code=404, detail={"code": "BERKAS_TAK_TERSEDIA", "message": PESAN_TAK_TERSEDIA})
    from .storage_service import get_storage_service
    st = get_storage_service()

    def ambil() -> bytes:
        return st.client.get_object(Bucket=st.config.bucket, Key=row["file_path"])["Body"].read(UKURAN_MAKS + 1)
    try:
        isi = await asyncio.to_thread(ambil)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Gagal membaca berkas mutasi bank: {type(e).__name__}")
        raise HTTPException(status_code=404, detail={"code": "BERKAS_TAK_TERSEDIA", "message": PESAN_TAK_TERSEDIA}) from e
    if len(isi) > UKURAN_MAKS:
        raise HTTPException(status_code=413, detail={"code": "BERKAS_TERLALU_BESAR", "message": "Berkas lebih dari 10 MB."})
    return isi


async def _muat_berkas(conn, tid: str, user_id, document_id, blocks: list) -> Optional[bytes]:
    row = await conn.fetchrow(SQL_DOKUMEN, document_id, tid)
    if not row:
        _blok(blocks, "BERKAS_TAK_DITEMUKAN", "Berkas tidak ditemukan. Unggah ulang berkas mutasi.")
        return None
    if str(row["uploaded_by"]) != str(user_id):
        _blok(blocks, "BERKAS_BUKAN_MILIK_ANDA", "Berkas ini diunggah pengguna lain. Unggah sendiri berkas mutasinya.")
        return None
    diunggah = row["uploaded_at"]
    if diunggah is not None and datetime.now(timezone.utc) - (diunggah if diunggah.tzinfo else diunggah.replace(tzinfo=timezone.utc)) > UMUR_MAKS:
        _blok(blocks, "BERKAS_KEDALUWARSA", "Berkas diunggah lebih dari 24 jam lalu. Unggah ulang untuk melanjutkan.")
        return None
    ekstensi = (row["file_extension"] or (row["file_name"] or "").rsplit(".", 1)[-1]).lower().lstrip(".")
    if ekstensi == "pdf":
        _blok(blocks, "PDF_BELUM_DIDUKUNG", "PDF e-statement menyusul. Unduh Mutasi dari KlikBCA dalam format CSV.")
        return None
    if ekstensi != "csv":
        _blok(blocks, "FORMAT_TAK_DIDUKUNG", f"Berkas .{ekstensi or '?'} belum didukung. Pakai CSV.")
        return None
    if row["file_size"] and row["file_size"] > UKURAN_MAKS:
        _blok(blocks, "BERKAS_TERLALU_BESAR", "Berkas lebih dari 10 MB.")
        return None
    return await baca_isi_dokumen(row)


def _cek_rekening(h: P.HasilBaca, rek, blocks: list, catatan: list) -> None:
    angka_berkas = re.sub(r"\D", "", h.rekening_berkas or "")
    angka_rek = re.sub(r"\D", "", rek["account_number"] or "")
    if not angka_berkas:
        return
    if not angka_rek:
        catatan.append("Nomor rekening di MilkyHoop belum diisi; kecocokan rekening berkas tidak diperiksa.")
        return
    if not (angka_rek.endswith(angka_berkas) or angka_berkas.endswith(angka_rek) or angka_rek[-4:] == angka_berkas[-4:]):
        _blok(blocks, "REKENING_BERKAS_BEDA",
              f"Berkas ini untuk rekening …{angka_berkas[-4:]}, sedangkan rekening yang dipilih …{angka_rek[-4:]}. Pilih rekening yang sesuai.")


async def rencana(conn, tid: str, user_id, rekening_id, body: dict) -> tuple:
    """Penentu SATU untuk pratinjau dan tulis. -> (hasil untuk respons, dalam: bahan tulis)."""
    rek = await conn.fetchrow(SQL_REKENING, rekening_id, tid)
    if not rek:
        raise HTTPException(status_code=404, detail={"code": "REKENING_TAK_ADA", "message": "Rekening tidak ditemukan."})
    blocks, catatan = [], []
    hasil = {"ok": False, "blocks": blocks, "format": None, "rentang": None, "baru": 0, "duplikat": 0, "mungkin_duplikat": [],
             "dilewati": [], "contoh": [], "saldo_berkas": None, "saldo_buku_pada": None,
             "tebakan": {"yakin": 0, "periksa": 0, "kurang_data": 0}, "payload": None, "catatan": catatan}
    if not rek["is_active"]:
        _blok(blocks, "REKENING_NONAKTIF", "Rekening ini nonaktif; aktifkan dulu sebelum mengimpor mutasi.")
    isi = await _muat_berkas(conn, tid, user_id, body["document_id"], blocks)
    if isi is None:
        return hasil, None
    h = P.baca(isi, "", body.get("peta_kolom"))
    blocks.extend(h.blocks)
    _cek_rekening(h, rek, blocks, catatan)
    hash_isi = hashlib.sha256(isi).hexdigest()
    hasil["format"] = h.format
    hasil["dilewati"] = h.dilewati
    hasil["payload"] = {"document_id": str(body["document_id"]), "peta_kolom": h.peta_kolom if body.get("peta_kolom") else None, "hash_isi": hash_isi}
    if h.peta_kolom is not None:
        hasil["peta_kolom"] = h.peta_kolom
    dalam = {"format": h.format, "peta_kolom": h.peta_kolom if body.get("peta_kolom") else None, "document_id": body["document_id"], "hash_isi": hash_isi,
             "baris": [], "duplikat": 0, "dilewati_n": sum(d["n"] for d in h.dilewati), "saldo_awal": h.saldo_awal, "saldo_akhir": h.saldo_akhir,
             "tanggal_awal": None, "tanggal_akhir": None}
    if blocks or not h.baris:
        return hasil, dalam
    sidik = P.beri_sidik(tid, rekening_id, h.baris)
    ada = {r["sidik"] for r in await conn.fetch(SQL_SIDIK_ADA, tid, rekening_id, sidik)}
    dari, sampai = min(b.tanggal for b in h.baris), max(b.tanggal for b in h.baris)
    lama = {}
    for r in await conn.fetch(SQL_LINTAS_FORMAT, tid, rekening_id, dari, sampai, sidik):
        if r["saldo_berkas"] is not None:
            lama.setdefault((r["tanggal"], r["arah"], r["nominal"], r["saldo_berkas"]), r)
    terlihat, baru, mungkin, duplikat = set(), [], [], 0
    for b, s in zip(h.baris, sidik):
        if s in ada or s in terlihat:
            duplikat += 1
            continue
        terlihat.add(s)
        mirip = lama.get((b.tanggal, b.arah, b.nominal, b.saldo)) if b.saldo is not None else None
        baru.append({"b": b, "sidik": s, "norma": P.normalisasi_keterangan(b.keterangan), "dup": mirip["id"] if mirip else None,
                     "status": "mungkin_duplikat" if mirip else "untuk_ditinjau"})
        if mirip:
            mungkin.append({"baru": {"baris_ke": b.baris_ke, "tanggal": b.tanggal.isoformat(), "keterangan": b.keterangan, "nominal": str(b.nominal)},
                            "lama": {"id": str(mirip["id"]), "tanggal": mirip["tanggal"].isoformat(), "keterangan": mirip["keterangan"]}})
    hasil.update({"baru": len(baru), "duplikat": duplikat, "mungkin_duplikat": mungkin, "rentang": {"dari": dari.isoformat(), "sampai": sampai.isoformat()},
                  "contoh": [{"tanggal": x["b"].tanggal.isoformat(), "keterangan": x["b"].keterangan, "arah": x["b"].arah, "nominal": str(x["b"].nominal),
                              "saldo": str(x["b"].saldo) if x["b"].saldo is not None else None} for x in baru[:3]],
                  "saldo_berkas": {"awal": str(h.saldo_awal) if h.saldo_awal is not None else None,
                                   "akhir": str(h.saldo_akhir) if h.saldo_akhir is not None else None},
                  "tebakan": {"yakin": 0, "periksa": 0, "kurang_data": len(baru)}})
    buku = await akun_rekening(conn, tid, rekening_id, sampai)
    if buku is not None:
        hasil["saldo_buku_pada"] = {"tanggal": sampai.isoformat(), "nilai": str(buku["saldo"])}
    if duplikat:
        catatan.append(f"{duplikat} baris sudah pernah diimpor dan dilewati.")
    if h.dilewati:
        catatan.append("Baris PEND belum dibukukan bank; impor lagi setelah dibukukan.")
    hasil["ok"] = True
    dalam.update({"baris": baru, "duplikat": duplikat, "tanggal_awal": dari, "tanggal_akhir": sampai})
    return hasil, dalam


async def pratinjau(pool, tid: str, user_id, rekening_id, body: dict) -> dict:
    """Pratinjau B8: transaksi READ ONLY (penentu tak bisa menulis)."""
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            hasil, _ = await rencana(conn, tid, user_id, rekening_id, body)
            return {"success": True, "data": hasil}


async def tulis(conn, ctx: dict, rekening_id, body: dict) -> dict:
    """Tulis di transaksi PEMANGGIL: kunci per (tenant, rekening) -> penentu yang SAMA -> impor + baris + lampiran + audit. NOL jurnal."""
    tid, uid = ctx["tenant_id"], ctx["user_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"MUTASI_IMPOR:{tid}:{rekening_id}")
    hasil, dalam = await rencana(conn, tid, uid, rekening_id, body)
    if hasil["blocks"]:
        raise HTTPException(status_code=422, detail={"code": "IMPOR_DITOLAK", "message": hasil["blocks"][0]["message"], "blocks": hasil["blocks"]})
    if body.get("hash_isi") and body["hash_isi"] != dalam["hash_isi"]:
        raise HTTPException(status_code=409, detail={"code": "BERKAS_BERUBAH", "message": "Isi berkas berbeda dari yang ditinjau. Tinjau ulang sebelum menyimpan."})
    baru = dalam["baris"]
    impor_id = uuid.uuid4()
    await conn.execute(SQL_IMPOR, impor_id, tid, rekening_id, dalam["document_id"], dalam["format"],
                       json.dumps(dalam["peta_kolom"]) if dalam["peta_kolom"] else None, dalam["tanggal_awal"], dalam["tanggal_akhir"],
                       dalam["saldo_awal"], dalam["saldo_akhir"], len(baru), dalam["duplikat"], dalam["dilewati_n"], uid)
    if baru:
        masuk = await conn.fetch(
            SQL_BARIS, tid, rekening_id, impor_id,
            [x["b"].tanggal for x in baru], [x["b"].keterangan for x in baru], [x["norma"] for x in baru], [x["b"].arah for x in baru],
            [x["b"].nominal for x in baru], [x["b"].saldo for x in baru], [x["sidik"] for x in baru], [x["b"].baris_ke for x in baru],
            [x["b"].mentah for x in baru], [x["dup"] for x in baru], [x["status"] for x in baru])
        if len(masuk) != len(baru):
            raise HTTPException(status_code=409, detail={"code": "IMPOR_BENTROK", "message": "Impor bentrok dengan impor lain yang serentak. Coba lagi."})
    await conn.execute(SQL_LAMPIRAN, tid, dalam["document_id"], impor_id, str(uid))
    await catat_riwayat(conn, tid, "mutasi_bank_impor", impor_id, None, "BANK_FEED_IMPOR", uid,
                        f"Impor mutasi bank: {len(baru)} baru, {dalam['duplikat']} sudah ada",
                        meta={"rekening_id": str(rekening_id), "baru": len(baru), "duplikat": dalam["duplikat"], "format": dalam["format"],
                              "hash_isi": dalam["hash_isi"]})
    return {"id": str(impor_id), "baru": len(baru), "duplikat": dalam["duplikat"], "mungkin_duplikat": len(hasil["mungkin_duplikat"]),
            "dilewati": hasil["dilewati"]}
