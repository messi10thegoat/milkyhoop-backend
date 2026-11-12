"""Laporan pengecualian "tercatat di MilkyHoop, tak ada di bank" (BF-1c1; KONTRAK-bankfeed §8b + KONTRAK-bf1c1). BACA SAJA; anti-fraud nilai utama pemilik.
SATU fungsi `daftar(conn, tid, rekening_id, dari, sampai)` (dipakai rute DAN, nanti, rekonsiliasi BF-3). CALON = bank_transactions rekening ini yang lolos definisi kandidat
SATU (bankfeed_tebak.SQL_KANDIDAT: POSTED, jurnal-derived, tanpa pasangan batal, belum rekonsiliasi) DAN TAK punya tautan aktif ke mutasi_bank (arah apa pun).
Hanya tanggal dalam rentang yang DITUTUP berkas impor (min..max tanggal mutasi_bank rekening ini) dikurangi LAG_KLIRING hari di ujung akhir yang dinilai; selebihnya TAK dinilai
dan dikatakan di `catatan` (bukan dianggap hilang). Saldo awal/penyesuaian saldo awal bukan transaksi bank -> dikecualikan. Bermakna hanya bila saldo awal rekening benar (O6)."""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from . import bankfeed_daftar as BD
from . import bankfeed_tebak as TB
from . import kasbank_ringkas as KR
from . import teks_galat as tg
from .nama_pengguna import nama_untuk
from . import bankfeed_dokumen as BDK
from ..utils.tanggal_tenant import tanggal_dokumen

LAG_KLIRING = 3          # hari di ujung akhir berkas yang belum bisa dinilai (kliring bank)
DOBEL_HARI = 7           # kontak sama + nominal sama dalam +-7 hari = mungkin dobel (kasus Ezhra)
MAKS_DOBEL = 5
SUMBER_BUKAN_MUTASI = frozenset({"OPENING", "OPENING_BALANCE", "BANK_OPENING_BALANCE"})
SQL_RENTANG = ("SELECT min(tanggal) AS awal, max(tanggal) AS akhir, count(*) AS n FROM mutasi_bank "
               "WHERE tenant_id = $1 AND bank_account_id = $2 AND status <> 'dipisah'")
SQL_LAMPIRAN = ("SELECT entity_id, count(*) AS n FROM document_attachments WHERE tenant_id = $1 AND entity_id = ANY($2::uuid[]) GROUP BY entity_id")


def _tgl(d) -> str:
    return d.isoformat()


def _umur_text(n: int) -> str:
    return "hari ini" if n <= 0 else f"{n} hari lalu"


def _mungkin_dobel(item: dict, semua: list) -> list:
    nama = (item["kontak_nama"] or "").strip().lower()
    if not nama:
        return []
    return [x for x in semua if x["id"] != item["id"] and x["arah"] == item["arah"] and x["nominal"] == item["nominal"]
            and (x["kontak_nama"] or "").strip().lower() == nama and abs((x["tanggal"] - item["tanggal"]).days) <= DOBEL_HARI]


async def daftar(conn, tid: str, rekening_id, dari, sampai) -> dict:
    if not await conn.fetchrow(BD.SQL_REKENING_ADA, rekening_id, tid):
        raise HTTPException(status_code=404, detail={"code": "REKENING_TAK_ADA", "message": "Rekening tidak ditemukan."})
    kosong = {"rentang_berkas": None, "dinilai": None, "data": [], "total": {"masuk": "0", "keluar": "0", "jumlah": 0}}
    r = await conn.fetchrow(SQL_RENTANG, tid, rekening_id)
    if not r or not r["n"]:
        return {**kosong, "catatan": ["Belum ada berkas rekening koran yang diimpor untuk rekening ini, jadi belum ada yang bisa dinilai."]}
    awal, akhir = r["awal"], r["akhir"]
    batas = akhir - timedelta(days=LAG_KLIRING)
    d1, d2 = max(dari or awal, awal), min(sampai or batas, batas)
    catatan = [f"Dinilai hanya untuk tanggal yang tercakup berkas rekening koran: {_tgl(awal)} sampai {_tgl(akhir)}; {LAG_KLIRING} hari terakhir tidak dinilai "
               "karena kliring bank belum tentu selesai.",
               "Hasil bermakna bila saldo awal rekening sudah benar."]
    if dari and dari < awal:
        catatan.append(f"Sebelum {_tgl(awal)} tidak ada berkas, jadi tidak dinilai.")
    rentang = {"dari": _tgl(awal), "sampai": _tgl(akhir)}
    if d1 > d2:
        return {**kosong, "rentang_berkas": rentang, "catatan": catatan}
    kand = [k for k in await TB.kandidat_bt(conn, tid, rekening_id, d1, d2) if k["jenis"] != "opening" and k["sumber"] not in SUMBER_BUKAN_MUTASI]
    lampiran = {str(x["entity_id"]): int(x["n"]) for x in await conn.fetch(SQL_LAMPIRAN, tid, [UUID(str(k["dokumen_id"])) for k in kand if k["dokumen_id"]])} if kand else {}
    nama = await nama_untuk(conn, [k["dibuat_oleh"] for k in kand if k["dibuat_oleh"]]) if any(k["dibuat_oleh"] for k in kand) else {}
    hari_ini = await tanggal_dokumen(conn, tid)
    data, masuk, keluar = [], Decimal(0), Decimal(0)
    for k in kand:
        dobel = _mungkin_dobel(k, kand)
        umur = (hari_ini - k["tanggal"]).days
        data.append({"bt_id": k["id"], "tanggal": _tgl(k["tanggal"]), "arah": k["arah"], "nominal": str(k["nominal"]), "nominal_tampil": tg.rp(k["nominal"]),
                     "dokumen": BDK.bentuk(k["sumber"], k["dokumen_id"], k["nomor"], k["id"]),
                     "kontak": {"jenis": k["kontak_jenis"], "id": str(k["kontak_id"]) if k["kontak_id"] else None, "nama": k["kontak_nama"]} if k["kontak_nama"] else None,
                     "dibuat_oleh_text": nama.get(str(k["dibuat_oleh"]), "pengguna") if k["dibuat_oleh"] else "pengguna",
                     "lampiran": {"jumlah": lampiran.get(str(k["dokumen_id"]), 0) if k["dokumen_id"] else 0}, "umur_hari": umur, "umur_text": _umur_text(umur),
                     "alasan": "mungkin_dobel" if dobel else "tak_ada_di_bank",
                     "alasan_text": (f"Mungkin ganda dengan {', '.join(str(x['nomor']) for x in dobel[:MAKS_DOBEL])} (kontak dan nominal sama, paling lama {DOBEL_HARI} hari)."
                                     if dobel else "Tercatat di MilkyHoop, tidak ada di rekening koran."),
                     "mungkin_dobel_dengan": [{"bt_id": x["id"], "nomor": x["nomor"], "tanggal": _tgl(x["tanggal"])} for x in dobel[:MAKS_DOBEL]] or None})
        if k["arah"] == "masuk":
            masuk += k["nominal"]
        else:
            keluar += k["nominal"]
    return {"rentang_berkas": rentang, "dinilai": {"dari": _tgl(d1), "sampai": _tgl(d2)}, "data": data,
            "total": {"masuk": str(masuk), "keluar": str(keluar), "jumlah": len(data)}, "catatan": catatan}
