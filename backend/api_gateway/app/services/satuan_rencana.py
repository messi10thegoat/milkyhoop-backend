"""G3 Satuan (BE; pola gudang_rencana, 17 Nov 2026): SATU penentu untuk pratinjau DAN penulis (buat, ubah, nonaktifkan). Rencana = {blocks, peringatan, rencana, payload, nilai}.
Blok memblok; peringatan (bentuk BUG-207: code, butuh_konfirmasi, pesan, message, field) tidak memblok.

Aturan (putusan pemilik): satuan BAWAAN tak bisa diubah/dinonaktifkan (SATUAN_BAWAAN_TAK_DAPAT_DIUBAH, blok berkalimat). Satuan yang SUDAH DIPAKAI (barang/konversi, penentu
services/satuan_pakai) tak bisa diganti nama/singkatan di fase G3-a (SATUAN_TERPAKAI_UBAH): teks satuan tersimpan di barang/konversi/harga/BOM, mengubah master saja membuat
barang yatim (BUG-A); ganti nama-menyebar + gabung = fase G3-b (tabel alias). Nonaktif satuan terpakai = blok (SATUAN_TERPAKAI). Dokumen historis TIDAK pernah diubah."""
from typing import Optional

from ..schemas.items import DEFAULT_UNITS
from . import satuan_master as SM
from . import satuan_pakai as SP
from .galat_berkode import GalatBerkode

LABEL = {"name": "Nama", "abbreviation": "Singkatan", "is_active": "Status"}
URUT = ("name", "abbreviation")
TEKS_BAWAAN = "Satuan bawaan tidak bisa diubah, digabung, atau dinonaktifkan."
MAKS_NAMA, MAKS_SINGKATAN = 50, 20


def blok(kode: str, pesan: str, field: Optional[str] = None, status: int = 400) -> dict:
    return {"code": kode, "message": pesan, "field": field, "status": status}


def peringatan(kode: str, pesan: str, field: Optional[str] = None) -> dict:
    return {"code": kode, "butuh_konfirmasi": True, "pesan": pesan, "message": pesan, "field": field}


def _bagian(p: dict) -> str:
    b = [f"{p['jumlah_barang']} barang"] if p["jumlah_barang"] else []
    if p["jumlah_konversi"]:
        b.append(f"{p['jumlah_konversi']} konversi")
    return " dan ".join(b)


def pemakai_text(p: dict) -> str:
    return f"Dipakai {_bagian(p)}" if _bagian(p) else "Belum dipakai"


def jenis_id(uid: str) -> str:
    return "bawaan" if uid.startswith(SM.ID_BAWAAN) else "terpakai" if uid.startswith(SM.ID_TERPAKAI) else "master"


async def muat(conn, tid: str, uid: str) -> Optional[dict]:
    """Satuan menurut id (UUID master, 'bawaan:x', 'terpakai:x'). -> {id, name, abbreviation, is_system, is_active, jenis, pemakai} | None."""
    semua = await SM.daftar(conn, tid)
    u = next((x for x in semua if x["id"] == uid), None)
    if u is not None:
        pakai = await SP.muat_pemakai(conn, tid, semua)
        return {**u, "is_active": True, "jenis": jenis_id(uid), "pemakai": pakai.get(uid, dict(SP.NOL))}
    if jenis_id(uid) != "master":
        return None
    baris = await conn.fetchrow("SELECT id::text AS id, name, abbreviation, is_system, COALESCE(is_active, true) AS is_active FROM product_units "
                                "WHERE id::text = $1 AND tenant_id = $2", uid, tid)
    return None if baris is None else {**dict(baris), "bawaan": False, "jenis": "master", "pemakai": dict(SP.NOL)}   # nonaktif: pemakai tak dihitung


def _susun(blocks, perin, aksi, u, perubahan, teks, payload, nilai) -> dict:
    r = {"aksi": aksi, "satuan": {"id": u["id"] if u else None, "nama": nilai.get("name", (u or {}).get("name")),
                                   "singkatan": nilai.get("abbreviation", (u or {}).get("abbreviation"))},
         "perubahan": perubahan, "teks": teks}
    if u:
        r["pemakai"] = {**u["pemakai"], "teks": pemakai_text(u["pemakai"])}
    return {"blocks": blocks, "peringatan": perin, "rencana": r, "payload": payload, "nilai": nilai}


def _rapi(v) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


async def _bentrok(conn, tid: str, singkatan: str, kecuali=None) -> bool:
    if SM.kunci(singkatan) in {SM.kunci(x) for x in DEFAULT_UNITS}:
        return True
    return bool(await conn.fetchval("SELECT id FROM product_units WHERE tenant_id = $1 AND lower(abbreviation) = lower($2) AND ($3::text IS NULL OR id::text <> $3) "
                                    "AND COALESCE(is_active, true)", tid, singkatan, kecuali))


async def rencana_buat(conn, tid: str, b: dict) -> dict:
    blocks, perin = [], []
    nama = _rapi(b.get("name"))
    singkatan = _rapi(b.get("abbreviation")) or nama
    if not nama:
        blocks.append(blok("SATUAN_NAMA_KOSONG", "Nama satuan wajib diisi.", "name"))
    elif len(nama) > MAKS_NAMA:
        blocks.append(blok("SATUAN_NAMA_TERLALU_PANJANG", f"Nama satuan maksimal {MAKS_NAMA} karakter.", "name"))
    if len(singkatan) > MAKS_SINGKATAN:
        blocks.append(blok("SATUAN_SINGKATAN_TERLALU_PANJANG", f"Singkatan satuan maksimal {MAKS_SINGKATAN} karakter.", "abbreviation"))
    elif singkatan and await _bentrok(conn, tid, singkatan):
        blocks.append(blok("SATUAN_SUDAH_ADA", f"Satuan '{singkatan}' sudah ada.", "abbreviation"))
    elif singkatan:
        semua = await SM.daftar(conn, tid)
        ada = next((x for x in semua if x["id"].startswith(SM.ID_TERPAKAI) and SM.kunci(x["abbreviation"]) == SM.kunci(singkatan)), None)
        if ada:
            p = (await SP.muat_pemakai(conn, tid, semua)).get(ada["id"], dict(SP.NOL))
            perin.append(peringatan("SATUAN_TERPAKAI_DIDAFTARKAN", f"'{singkatan}' sudah tertulis di barang/konversi ({pemakai_text(p).lower()}); satuan ini akan didaftarkan ke master.", "abbreviation"))
    nilai = {"name": nama, "abbreviation": singkatan}
    perubahan = [{"field": k, "label": LABEL[k], "before": None, "after": nilai[k]} for k in URUT if nilai[k]]
    return _susun(blocks, perin, "buat", None, perubahan, f"Buat satuan {nama} ({singkatan})" if nama else "Buat satuan", b, nilai)


def _bawaan(u: dict) -> bool:
    return bool(u.get("is_system") or u.get("bawaan")) or u["jenis"] == "bawaan"    # `bawaan` = nama ada di DEFAULT_UNITS, juga bila tenant punya baris dengan singkatan itu


def _blok_bawaan() -> dict:
    return blok("SATUAN_BAWAAN_TAK_DAPAT_DIUBAH", TEKS_BAWAAN, None, 409)


def _blok_belum(pesan: str) -> dict:
    return blok("SATUAN_BELUM_TERDAFTAR", pesan, None, 409)


async def rencana_ubah(conn, tid: str, u: dict, b: dict) -> dict:
    blocks, perin = [], []
    if _bawaan(u):
        return _susun([_blok_bawaan()], perin, "ubah", u, [], f"Ubah satuan {u['name']}", b, {})
    if u["jenis"] == "terpakai":
        return _susun([_blok_belum("Satuan ini belum terdaftar di master (hanya tertulis di barang). Daftarkan dulu lewat Buat satuan.")],
                      perin, "ubah", u, [], f"Ubah satuan {u['name']}", b, {})
    nilai = {}
    if b.get("name") is not None:
        v = _rapi(b["name"])
        if not v:
            blocks.append(blok("SATUAN_NAMA_KOSONG", "Nama satuan wajib diisi.", "name"))
        elif len(v) > MAKS_NAMA:
            blocks.append(blok("SATUAN_NAMA_TERLALU_PANJANG", f"Nama satuan maksimal {MAKS_NAMA} karakter.", "name"))
        elif v != u["name"]:
            nilai["name"] = v
    if b.get("abbreviation") is not None:
        v = _rapi(b["abbreviation"])
        if not v:
            blocks.append(blok("SATUAN_SINGKATAN_KOSONG", "Singkatan satuan wajib diisi.", "abbreviation"))
        elif len(v) > MAKS_SINGKATAN:
            blocks.append(blok("SATUAN_SINGKATAN_TERLALU_PANJANG", f"Singkatan satuan maksimal {MAKS_SINGKATAN} karakter.", "abbreviation"))
        elif v != u["abbreviation"]:
            if SM.kunci(v) != SM.kunci(u["abbreviation"]) and await _bentrok(conn, tid, v, u["id"]):
                blocks.append(blok("SATUAN_SUDAH_ADA", f"Satuan '{v}' sudah ada.", "abbreviation"))
            else:
                nilai["abbreviation"] = v
    p = u["pemakai"]
    if nilai and _bagian(p):
        blocks.append(blok("SATUAN_TERPAKAI_UBAH", f"Satuan masih dipakai {_bagian(p)}; nama/singkatannya belum bisa diganti agar barang tidak kehilangan satuan. "
                                                  "Buat satuan baru lalu pindahkan barangnya, atau tunggu fitur gabung.", "name" if "name" in nilai else "abbreviation"))
    perubahan = [{"field": k, "label": LABEL[k], "before": u[k], "after": nilai[k]} for k in URUT if k in nilai]
    return _susun(blocks, perin, "ubah", u, perubahan, f"Ubah satuan {nilai.get('name', u['name'])}", b, nilai)


async def rencana_status(conn, tid: str, u: dict) -> dict:
    """Nonaktifkan (hapus lunak). Aktif kembali = Buat satuan dengan singkatan yang sama (tak ada jalur aktifkan terpisah)."""
    blocks, perin = [], []
    p = u["pemakai"]
    if _bawaan(u):
        blocks.append(_blok_bawaan())
    elif u["jenis"] == "terpakai":
        blocks.append(_blok_belum("Satuan ini belum terdaftar di master (hanya tertulis di barang); tidak ada yang dinonaktifkan."))
    elif not u.get("is_active", True):
        blocks.append(blok("SATUAN_SUDAH_NONAKTIF", "Satuan ini sudah nonaktif.", "is_active"))
    elif _bagian(p):
        blocks.append(blok("SATUAN_TERPAKAI", f"Satuan masih dipakai {_bagian(p)}.", "is_active"))
    perubahan = [{"field": "is_active", "label": LABEL["is_active"], "before": u.get("is_active", True), "after": False, "before_text": "Aktif", "after_text": "Nonaktif"}]
    return _susun(blocks, perin, "nonaktifkan", u, perubahan, f"Nonaktifkan satuan {u['name']}", {"aktif": False}, {"is_active": False})


def pratinjau(r: dict, payload=None) -> dict:
    return {"success": True, "data": {"ok": not r["blocks"], "blocks": [{"code": x["code"], "message": x["message"], "field": x["field"]} for x in r["blocks"]],
                                      "peringatan": r["peringatan"], "rencana": r["rencana"], "payload": r["payload"] if payload is None else payload}}


def tolak(blocks: list) -> None:
    """Penulis: blok pertama -> galat berkode (400; 409 bawaan/belum terdaftar; detail string + code/message/field tingkat atas)."""
    if blocks:
        x = blocks[0]
        raise GalatBerkode(x["status"], x["code"], x["message"], x["field"])


def baris_teks(u: dict) -> dict:
    """Medan *_text aditif untuk baris daftar/detail (dihitung dari baris satuan_pakai.susun)."""
    j = jenis_id(u["id"])
    if u.get("is_system") or u.get("bawaan"):       # baris tenant yang namanya ada di DEFAULT_UNITS juga bawaan (sama dengan _bawaan -> diblok)
        j = "bawaan"
    return {"pemakai_text": pemakai_text(u), "jenis": j, "status_text": "Aktif",
            "jenis_text": "Bawaan" if j == "bawaan" else "Belum terdaftar" if j == "terpakai" else "Kustom"}
