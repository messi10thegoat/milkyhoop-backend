"""Daftar mutasi bank per rekening (BF-1a; KONTRAK-bankfeed §8): tab Untuk ditinjau / Dicatat / Dikecualikan (+ Mungkin duplikat), cacah per status
(Σ server), total masuk/keluar Decimal, teks status/keputusan dari server (FE hanya memformat). Baris induk hasil Pisah (status `dipisah`) selalu
tersembunyi. Tebakan/aksi/jenis_tersedia diisi sub-unit berikut (BF-1b/c): di sini `tebakan` = kosong, `aksi_tersedia`/`jenis_tersedia` = kosong."""
import re
from typing import Optional

from fastapi import HTTPException

from . import teks_galat as tg
from .nama_pengguna import nama_untuk
from .so_posisi import BULAN
from ..utils.tanggal_tenant import zona_tenant

STATUS_TEKS = {"untuk_ditinjau": "Untuk ditinjau", "mungkin_duplikat": "Mungkin duplikat", "dicatat": "Dicatat", "dikecualikan": "Dikecualikan"}
CARA_KATA = {"cocok": "Dicocokkan", "catat": "Dicatat", "pribadi": "Ditandai pribadi", "kecualikan": "Dikecualikan"}
TEBAKAN_KOSONG = {"jenis": "kosong", "keyakinan": "kurang_data", "kalimat": None}

SQL_REKENING_ADA = "SELECT 1 FROM bank_accounts WHERE id = $1 AND tenant_id = $2"


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


async def daftar(conn, tid: str, rekening_id, status: Optional[str], q: Optional[str], dari, sampai, limit: int, offset: int) -> dict:
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
        f"SELECT id, tanggal, keterangan, arah, nominal, status, cara, diputus_oleh, diputus_pada FROM mutasi_bank WHERE {saring} "
        f"ORDER BY tanggal DESC, baris_ke DESC NULLS LAST, id LIMIT ${len(param_s) + 1} OFFSET ${len(param_s) + 2}", *param_s, limit, offset)
    nama = await nama_untuk(conn, [r["diputus_oleh"] for r in rows]) if any(r["diputus_oleh"] for r in rows) else {}
    zona = await zona_tenant(conn, tid) if any(r["diputus_pada"] for r in rows) else None
    data = []
    for r in rows:
        diputus = None
        if r["diputus_pada"] and r["status"] in ("dicatat", "dikecualikan"):
            kata = CARA_KATA.get(r["cara"] or ("kecualikan" if r["status"] == "dikecualikan" else "catat"), "Dicatat")
            diputus = f"{kata} oleh {nama.get(str(r['diputus_oleh']), 'pengguna')} · {_waktu(zona, r['diputus_pada'])}"
        data.append({"id": str(r["id"]), "tanggal": r["tanggal"].isoformat(), "keterangan": r["keterangan"],
                     "keterangan_ringkas": ringkas_keterangan(r["keterangan"]), "arah": r["arah"], "nominal": str(r["nominal"]),
                     "nominal_tampil": tg.rp(r["nominal"]), "status": r["status"], "status_text": STATUS_TEKS.get(r["status"], r["status"]),
                     "tebakan": dict(TEBAKAN_KOSONG), "diputus_text": diputus, "jenis_tersedia": [], "aksi_tersedia": {}})
    return {"data": data, "cacah": cacah, "total": {"masuk": str(jumlah["masuk"]), "keluar": str(jumlah["keluar"]), "jumlah": int(jumlah["n"])},
            "has_more": offset + len(data) < int(jumlah["n"])}
