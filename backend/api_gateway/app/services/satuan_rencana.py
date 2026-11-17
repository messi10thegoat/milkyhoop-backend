"""G3 Satuan (BE; pola gudang_rencana, 17 Nov 2026): SATU penentu untuk pratinjau DAN penulis (buat, ubah, nonaktifkan). Rencana = {blocks, peringatan, rencana, payload, nilai}.
Blok memblok; peringatan (bentuk BUG-207: code, butuh_konfirmasi, pesan, message, field) tidak memblok.

Aturan (putusan pemilik): satuan BAWAAN tak bisa diubah/dinonaktifkan (SATUAN_BAWAAN_TAK_DAPAT_DIUBAH, blok berkalimat). Satuan yang SUDAH DIPAKAI (barang/konversi, penentu
services/satuan_pakai) tak bisa diganti nama/singkatan di fase G3-a (SATUAN_TERPAKAI_UBAH): teks satuan tersimpan di barang/konversi/harga/BOM, mengubah master saja membuat
barang yatim (BUG-A); G3-b (V504): GANTI NAMA satuan terpakai kini MENYEBAR ke teks master (barang/konversi/harga/BOM; services/satuan_sebar) + nama lama jadi ALIAS, dan GABUNG
(rencana_gabung). Nonaktif satuan terpakai tetap blok (SATUAN_TERPAKAI). Dokumen historis TIDAK pernah diubah (terbaca lewat alias)."""
from typing import Optional

from ..schemas.items import DEFAULT_UNITS
from . import satuan_master as SM
from . import satuan_pakai as SP
from . import satuan_sebar as SS
from .satuan_dasar import normal
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
        jenis = jenis_id(uid)
        if jenis == "terpakai" and u.get("bawaan"):          # nama bawaan yang baru tertulis di barang (belum punya baris master): tetap BAWAAN (diblok sebagai sumber; tujuan gabung = dibuat otomatis)
            jenis = "bawaan"
        return {**u, "is_active": True, "jenis": jenis, "pemakai": pakai.get(uid, dict(SP.NOL))}
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
                                    "AND COALESCE(is_active, true) "
                                    "UNION ALL SELECT unit_id FROM product_unit_aliases WHERE tenant_id = $1 AND lower(btrim(alias)) = lower($2) AND ($3::text IS NULL OR unit_id::text <> $3) LIMIT 1",
                                    tid, singkatan, kecuali))                    # singkatan baru tak boleh = nama LAMA satuan lain (alias)


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
    extra = {}
    if nilai and not blocks:
        extra = await _sebar_ubah(conn, tid, u, nilai, perin, blocks)
    perubahan = [{"field": k, "label": LABEL[k], "before": u[k], "after": nilai[k]} for k in URUT if k in nilai]
    r = _susun(blocks, perin, "ubah", u, perubahan, f"Ubah satuan {nilai.get('name', u['name'])}", b, nilai)
    r.update(extra)
    if "penyebaran" in extra:
        r["rencana"]["penyebaran"] = extra["penyebaran"]
    return r


def _teks_dampak(j: dict) -> str:
    b = []
    for k, label in (("barang", "barang"), ("konversi", "konversi"), ("harga", "baris harga"), ("bom", "BOM/resep")):
        if j[k]:
            b.append(f"{j[k]} {label}")
    return ", ".join(b)


async def _sebar_ubah(conn, tid: str, u: dict, nilai: dict, perin: list, blocks: list) -> dict:
    """Ganti nama/singkatan: teks master ikut berganti (satuan_sebar), nama lama -> alias. Konflik data = blok bernama; dampak > 0 = peringatan butuh konfirmasi."""
    n1, a1 = nilai.get("name", u["name"]), nilai.get("abbreviation", u["abbreviation"])
    alias = await SS.alias_unit(conn, tid, u["id"])
    singk = {normal(u["abbreviation"])} | {normal(x) for x in alias}
    h = await SS.siapkan(conn, tid, singk, {normal(u["name"])}, a1, n1)
    for kode, pesan in h["blocks"]:
        blocks.append(blok(kode, pesan, "name" if "name" in nilai else "abbreviation"))
    lama = [x for x in (u["name"], u["abbreviation"]) if normal(x) not in (normal(n1), normal(a1))]
    j = h["jumlah"]
    teks = _teks_dampak(j)
    if teks and not h["blocks"]:
        perin.append(peringatan("SATUAN_NAMA_MENYEBAR", f"{teks} ikut memakai '{a1 if 'abbreviation' in nilai else n1}'. Dokumen lama tidak diubah; nama lama tetap terbaca.",
                                "abbreviation" if "abbreviation" in nilai else "name"))
    if h["hapus_konversi"] or h["hapus_harga"]:
        perin.append(peringatan("SATUAN_DUPLIKAT_DIBUANG", f"{len(h['hapus_konversi'])} konversi dan {len(h['hapus_harga'])} baris harga yang kembar akan digabung (dibuang satu).", None))
    return {"sebar": h, "alias_baru": lama, "penyebaran": {**j, "teks": teks or "Tidak ada barang/konversi/harga/BOM yang memakai nama ini."}}


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


async def rencana_gabung(conn, tid: str, s: dict, t: dict, ke_id: str) -> dict:
    """Gabung satuan SUMBER s ke TUJUAN t: teks master sumber -> teks tujuan (satuan_sebar), nama lama sumber -> alias tujuan, sumber dinonaktifkan.
    Sumber = satuan master kustom ATAU nama yang hanya tertulis di barang ('terpakai:'); tujuan = satuan master atau bawaan (baris master dibuat otomatis saat tulis)."""
    blocks, perin = [], []
    payload = {"ke_unit_id": ke_id}
    if _bawaan(s):
        blocks.append(_blok_bawaan())
    elif s["jenis"] == "master" and not s.get("is_active", True):
        blocks.append(blok("SATUAN_SUDAH_NONAKTIF", "Satuan sumber sudah nonaktif.", None))
    if t["jenis"] == "terpakai":
        blocks.append(blok("SATUAN_TUJUAN_BELUM_TERDAFTAR", "Satuan tujuan belum terdaftar di master (hanya tertulis di barang). Daftarkan dulu lewat Buat satuan.", "ke_unit_id", 409))
    elif t["jenis"] == "master" and not t.get("is_active", True):
        blocks.append(blok("SATUAN_TUJUAN_NONAKTIF", "Satuan tujuan nonaktif.", "ke_unit_id"))
    if s["id"] == t["id"]:                       # kunci_mirip sama (mis. "Pcs." dan "Pcs") justru kasus gabung yang dimaksud: hanya satuan yang SAMA diblok
        blocks.append(blok("SATUAN_GABUNG_SAMA", "Pilih satuan tujuan yang berbeda dari satuan yang digabung.", "ke_unit_id"))
    extra = {}
    if not blocks:
        alias = await SS.alias_unit(conn, tid, s["id"]) if s["jenis"] == "master" else []
        singk = {normal(s["abbreviation"])} | {normal(x) for x in alias}
        h = await SS.siapkan(conn, tid, singk, {normal(s["name"])}, t["abbreviation"], t["name"])
        for kode, pesan in h["blocks"]:
            blocks.append(blok(kode, pesan, "ke_unit_id"))
        j = h["jumlah"]
        teks = _teks_dampak(j)
        if not h["blocks"]:
            perin.append(peringatan("SATUAN_GABUNG_DAMPAK", f"{teks + ' ikut ' if teks else 'Tidak ada barang/konversi/harga/BOM yang ikut '}memakai '{t['abbreviation']}'. "
                                                          f"Satuan '{s['name']}' tak dipakai lagi; dokumen lama tidak diubah dan nama lamanya tetap terbaca.", "ke_unit_id"))
            if h["hapus_konversi"] or h["hapus_harga"]:
                perin.append(peringatan("SATUAN_DUPLIKAT_DIBUANG", f"{len(h['hapus_konversi'])} konversi dan {len(h['hapus_harga'])} baris harga yang kembar akan digabung (dibuang satu).", None))
        extra = {"sebar": h, "penyebaran": {**j, "teks": teks or "Tidak ada barang/konversi/harga/BOM yang memakai nama ini."}}
    r = _susun(blocks, perin, "gabung", s, [{"field": "gabung", "label": "Gabung", "before": s["name"], "after": t["name"]}],
               f"Gabung satuan {s['name']} ke {t['name']}", payload, {"is_active": False})
    r["rencana"]["tujuan"] = {"id": t["id"], "nama": t["name"], "singkatan": t["abbreviation"], "dibuat_otomatis": t["jenis"] == "bawaan"}
    r["tujuan"] = t
    r.update(extra)
    if "penyebaran" in extra:
        r["rencana"]["penyebaran"] = extra["penyebaran"]
    return r


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
