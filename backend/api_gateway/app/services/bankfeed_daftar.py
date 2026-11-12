"""Daftar mutasi bank per rekening (BF-1a; KONTRAK-bankfeed §8): tab Untuk ditinjau / Dicatat / Dikecualikan (+ Mungkin duplikat), cacah per status
(Σ server), total masuk/keluar Decimal, teks status/keputusan dari server (FE hanya memformat). Baris induk hasil Pisah (status `dipisah`) selalu
tersembunyi. `tebakan` dari penentu SATU (bankfeed_tebak; BF-1b, baca saja) untuk baris untuk_ditinjau/mungkin_duplikat; baris dicatat/dikecualikan = kosong.
`aksi_tersedia`/`jenis_tersedia` diisi BF-1c (aksinya belum ada)."""
import re
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from . import bankfeed_dokumen as BDK
from . import bankfeed_tebak as TB
from . import bankfeed_tersedia as TS
from . import kasbank_ringkas as KR
from . import teks_galat as tg
from .nama_pengguna import nama_untuk
from .so_posisi import BULAN
from ..utils.tanggal_tenant import zona_tenant

STATUS_TEKS = {"untuk_ditinjau": "Untuk ditinjau", "mungkin_duplikat": "Mungkin duplikat", "dicatat": "Dicatat", "dikecualikan": "Dikecualikan"}
CARA_KATA = {"cocok": "Dicocokkan", "catat": "Dicatat", "pribadi": "Ditandai pribadi", "kecualikan": "Dikecualikan"}
TEBAKAN_KOSONG = TB.ringkas_tebakan({**TB._kosong(), "layak_massal": False, "layak_massal_text": None})   # bentuk SAMA dgn tebakan hidup; salinan per baris

SQL_REKENING_ADA = "SELECT 1 FROM bank_accounts WHERE id = $1 AND tenant_id = $2"
# dokumen yang tertaut AKTIF ke baris dicatat/dicocokkan (BF-1e): satu bentuk dengan tebakan.dokumen dan tak-di-bank.dokumen; nomor/pihak dari SQL_JOIN_DOKUMEN (satu definisi dengan register bank)
SQL_TERTAUT = f"""SELECT t.mutasi_bank_id, bt.id AS bt_id, je.source_type AS sumber, je.source_id AS dokumen_id, COALESCE(dok.nomor_dokumen, bt.transaction_number, bt.reference_number) AS nomor
FROM mutasi_bank_tautan t
JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id
JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id
{KR.SQL_JOIN_DOKUMEN}
WHERE t.tenant_id = $1 AND t.mutasi_bank_id = ANY($2::uuid[]) AND t.dilepas_pada IS NULL
ORDER BY t.dibuat_pada, t.id"""


def ringkas_keterangan(teks, maks: int = 80) -> str:
    """Keterangan untuk daftar: buang kode transaksi BCA yang bervariasi (huruf asli dipertahankan), spasi tunggal, potong rapi."""
    s = re.sub(r"\b\d{4}/FTSCY/WS\d+\b|\bFTSCY/WS\d+\b|\bWS\d{4,}\b", " ", str(teks or ""), flags=re.I)
    s = " ".join(s.split())
    return s if len(s) <= maks else s[: maks - 1].rstrip() + "…"


def _suka(teks: str) -> str:
    return "%" + teks.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _syarat(rekening_id, tid: str, dari, sampai, q) -> tuple:
    kondisi, param = ["tenant_id = $1", "bank_account_id = $2", "status <> 'dipisah'"], [tid, rekening_id]
    if dari:
        param.append(dari)
        kondisi.append(f"tanggal >= ${len(param)}")
    if sampai:
        param.append(sampai)
        kondisi.append(f"tanggal <= ${len(param)}")
    if q and q.strip():
        param.append(_suka(q.strip()))
        kondisi.append(f"(keterangan ILIKE ${len(param)} ESCAPE '\\' OR nominal::text LIKE ${len(param)} ESCAPE '\\')")
    return " AND ".join(kondisi), param


def _waktu(zona, t) -> str:
    j = t.astimezone(zona)
    return f"{j.day} {BULAN[j.month - 1]} {j.year} {j.hour:02d}.{j.minute:02d}"


async def daftar(conn, tid: str, rekening_id, status: Optional[str], q: Optional[str], dari, sampai, limit: int, offset: int, user: Optional[dict] = None) -> dict:
    if not await conn.fetchrow(SQL_REKENING_ADA, rekening_id, tid):
        raise HTTPException(status_code=404, detail={"code": "REKENING_TAK_ADA", "message": "Rekening tidak ditemukan."})
    dasar, param = _syarat(rekening_id, tid, dari, sampai, q)
    cacah = {k: 0 for k in STATUS_TEKS}
    for r in await conn.fetch(f"SELECT status, count(*) AS n FROM mutasi_bank WHERE {dasar} GROUP BY status", *param):
        if r["status"] in cacah:
            cacah[r["status"]] = int(r["n"])
    saring, param_s = dasar, list(param)
    if status:
        param_s.append(status)
        saring += f" AND status = ${len(param_s)}"
    jumlah = await conn.fetchrow(
        f"SELECT COALESCE(SUM(nominal) FILTER (WHERE arah = 'masuk'), 0) AS masuk, COALESCE(SUM(nominal) FILTER (WHERE arah = 'keluar'), 0) AS keluar, "
        f"count(*) AS n FROM mutasi_bank WHERE {saring}", *param_s)
    rows = await conn.fetch(
        f"SELECT id, tanggal, keterangan, arah, nominal, status, cara, diputus_oleh, diputus_pada, rekonsiliasi_id FROM mutasi_bank WHERE {saring} "
        f"ORDER BY tanggal DESC, baris_ke DESC NULLS LAST, id LIMIT ${len(param_s) + 1} OFFSET ${len(param_s) + 2}", *param_s, limit, offset)
    peta = await TB.tebak_halaman(conn, tid, rekening_id, [{"id": str(r["id"]), "tanggal": r["tanggal"], "nominal": Decimal(str(r["nominal"])), "arah": r["arah"],
                                                   "keterangan": r["keterangan"], "status": r["status"]} for r in rows])
    nama = await nama_untuk(conn, [r["diputus_oleh"] for r in rows]) if any(r["diputus_oleh"] for r in rows) else {}
    zona = await zona_tenant(conn, tid) if any(r["diputus_pada"] for r in rows) else None
    izin = await TS.izin_pengguna(user, rekening_id) if rows else TS.izin_semua()       # BF-1e: SATU panggilan peran per halaman
    ids_catat = [r["id"] for r in rows if r["status"] == "dicatat"]
    tertaut = {}
    for x in (await conn.fetch(SQL_TERTAUT, tid, ids_catat) if ids_catat else []):         # BF-1e: SATU query untuk seluruh halaman
        tertaut.setdefault(str(x["mutasi_bank_id"]), []).append(BDK.bentuk(x["sumber"], x["dokumen_id"], x["nomor"], x["bt_id"]))
    data = []
    for r in rows:
        diputus = None
        if r["diputus_pada"] and r["status"] in ("dicatat", "dikecualikan"):
            kata = CARA_KATA.get(r["cara"] or ("kecualikan" if r["status"] == "dikecualikan" else "catat"), "Dicatat")
            diputus = f"{kata} oleh {nama.get(str(r['diputus_oleh']), 'pengguna')} · {_waktu(zona, r['diputus_pada'])}"
        jenis = TS.jenis_tersedia(r["arah"], izin) if r["status"] in TS.DITINJAU else []
        data.append({"id": str(r["id"]), "tanggal": r["tanggal"].isoformat(), "keterangan": r["keterangan"],
                     "keterangan_ringkas": ringkas_keterangan(r["keterangan"]), "arah": r["arah"], "nominal": str(r["nominal"]),
                     "nominal_tampil": tg.rp(r["nominal"]), "status": r["status"], "status_text": STATUS_TEKS.get(r["status"], r["status"]),
                     "tebakan": TB.ringkas_tebakan(peta[str(r["id"])]) if str(r["id"]) in peta else dict(TEBAKAN_KOSONG), "diputus_text": diputus, "jenis_tersedia": jenis, "aksi_tersedia": TS.aksi_tersedia(r, jenis, izin), "dokumen_tertaut": tertaut.get(str(r["id"]), [])})
    return {"data": data, "cacah": cacah, "total": {"masuk": str(jumlah["masuk"]), "keluar": str(jumlah["keluar"]), "jumlah": int(jumlah["n"]),
                     "masuk_tampil": tg.rp(jumlah["masuk"]), "keluar_tampil": tg.rp(jumlah["keluar"])},
            "has_more": offset + len(data) < int(jumlah["n"])}


async def perlu_ditinjau_per_rekening(conn, tid: str, rekening_ids) -> dict:
    """{str(rekening_id): n baris untuk_ditinjau + mungkin_duplikat} untuk SEMUA rekening satu halaman daftar Kas & Bank: SATU GROUP BY (BF-1e, G6)."""
    ids = list(rekening_ids)
    if not ids:
        return {}
    rows = await conn.fetch("SELECT bank_account_id, count(*) AS n FROM mutasi_bank WHERE tenant_id = $1 AND status IN ('untuk_ditinjau', 'mungkin_duplikat') "
                            "AND bank_account_id = ANY($2::uuid[]) GROUP BY bank_account_id", tid, ids)
    return {str(r["bank_account_id"]): int(r["n"]) for r in rows}
