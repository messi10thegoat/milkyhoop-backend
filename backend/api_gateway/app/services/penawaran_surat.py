"""Isian SURAT Penawaran (6 Okt 2026, MASTER; putusan pemilik, acuan "Surat Penawaran Harga" Accurate).

SNAPSHOT (pola "default bisa ditimpa" 30 Sep): dokumen menyimpan NILAINYA sendiri + sumber per medan
(quotes.field_sources {"<medan>": "default"|"manual"}); setelan diubah TIDAK menyentuh dokumen lama.
Aturan tulis (kontrak WORKSPACE 6 Okt):
- medan + `<medan>_source` eksplisit -> disimpan apa adanya;
- medan bernilai tanpa `_source` -> 'manual';
- `<medan>_source: 'default'` tanpa nilai -> nilai bawaan SAAT INI disalin (tetap snapshot; cadangan klien lama);
- CREATE tanpa medan -> nilai bawaan + 'default'; PATCH tanpa medan -> tak berubah;
- "" -> kosong disengaja (bagian tak tercetak).
Bawaan = services/default_dokumen.default_penawaran (SATU penentu untuk form, tulis, dan cadangan).
"""
from typing import Optional

from fastapi import HTTPException

TEKS = ("attention_name", "attention_title", "opening_text", "closing_text", "notes", "terms")
TTD = ("signer_name", "signer_title", "signer_phone", "signer_email")
MEDAN = TEKS + TTD
SUMBER = ("default", "manual")
_BATAS = {"attention_name": 255, "attention_title": 255, "signer_name": 255, "signer_title": 255,
          "signer_phone": 50, "signer_email": 255}


def _norm(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def nilai_bawaan(bawaan: dict) -> dict:
    """{medan: nilai|None} + signer_user_id dari keluaran default_penawaran."""
    out = {m: ((bawaan.get(m) or {}).get("value") if isinstance(bawaan.get(m), dict) else None) for m in TEKS}
    s = bawaan.get("signer") or {}
    out.update({"signer_name": s.get("name"), "signer_title": s.get("title"), "signer_phone": s.get("phone"),
                "signer_email": s.get("email"), "signer_user_id": s.get("user_id")})
    return out


def gabung(dikirim: dict, sumber_dikirim: dict, bawaan: dict, buat: bool) -> tuple:
    """Murni. dikirim = {medan: nilai} HANYA medan yang dikirim klien; sumber_dikirim = {medan: 'default'|'manual'}.
    -> (nilai {medan: v} yang DITULIS, sumber {medan: s}). Medan tak ada di hasil = tak berubah (PATCH)."""
    b = nilai_bawaan(bawaan)
    nilai, sumber = {}, {}
    for m in MEDAN:
        if m in dikirim or m in sumber_dikirim:
            s = sumber_dikirim.get(m) or "manual"
            nilai[m] = _norm(dikirim[m]) if m in dikirim else _norm(b.get(m))
            sumber[m] = s
        elif buat:
            nilai[m], sumber[m] = _norm(b.get(m)), "default"
    for m, n in _BATAS.items():
        if nilai.get(m) and len(nilai[m]) > n:
            raise HTTPException(status_code=422, detail=f"Isian {m} maksimal {n} karakter.")
    return nilai, sumber


async def anggota_aktif(conn, tenant_id: str, user_id) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM user_tenant_roles WHERE tenant_id = $1 AND user_id = $2::uuid
             AND upper(COALESCE(status, 'ACTIVE')) = 'ACTIVE' LIMIT 1""", tenant_id, str(user_id)))


async def terapkan(conn, tenant_id: str, quote_id, body, bawaan: dict, buat: bool) -> list:
    """Tulis isian surat ke quotes (dalam transaksi pemanggil). -> daftar medan yang berubah (riwayat)."""
    terkirim = body.model_fields_set
    dikirim = {m: getattr(body, m) for m in MEDAN if m in terkirim}
    sumber_dikirim = {m: getattr(body, m + "_source") for m in MEDAN
                      if m + "_source" in terkirim and getattr(body, m + "_source")}
    nilai, sumber = gabung(dikirim, sumber_dikirim, bawaan, buat)
    uid = None
    ubah_uid = False
    if "signer_user_id" in terkirim:
        uid, ubah_uid = body.signer_user_id or None, True
    elif buat or (sumber_dikirim.get("signer_name") == "default" and "signer_name" not in dikirim):
        uid, ubah_uid = nilai_bawaan(bawaan).get("signer_user_id"), True
    if uid and not await anggota_aktif(conn, tenant_id, uid):
        raise HTTPException(status_code=422, detail="Penanda tangan harus pengguna aktif di usaha ini.")
    if not nilai and not ubah_uid:
        return []
    sets, params = [], [quote_id, tenant_id]
    for m, v in nilai.items():
        params.append(v)
        sets.append(f"{m} = ${len(params)}")
    if ubah_uid:
        params.append(str(uid) if uid else None)
        sets.append(f"signer_user_id = ${len(params)}::uuid")
    if sumber:
        import json
        params.append(json.dumps(sumber))
        sets.append(f"field_sources = field_sources || ${len(params)}::jsonb")
    await conn.execute(f"UPDATE quotes SET {', '.join(sets)} WHERE id = $1 AND tenant_id = $2", *params)
    return sorted(set(nilai) | ({"signer_user_id"} if ubah_uid else set()))


def keluaran(row) -> dict:
    """Medan surat + `<medan>_source` untuk GET detail (penawaran lama: sumber null)."""
    import json
    fs = row["field_sources"] or {}
    if isinstance(fs, str):
        fs = json.loads(fs)
    out = {m: row[m] for m in MEDAN}
    out.update({m + "_source": fs.get(m) for m in MEDAN})
    out["signer_user_id"] = str(row["signer_user_id"]) if row["signer_user_id"] else None
    return out
