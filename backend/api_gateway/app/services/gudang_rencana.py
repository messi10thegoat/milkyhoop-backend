"""G3 Gudang (P6 BE; KONTRAK-gudang-g3-aksi-tulis-20261115 + putusan MASTER 15 Nov 2026): SATU penentu untuk pratinjau DAN penulis (buat, ubah, status,
jadikan utama). Rencana = {blocks, peringatan, rencana, payload, nilai}; nilai = yang DITULIS. Blok memblok; peringatan (bentuk BUG-207: code, butuh_konfirmasi,
pesan, message, field) tidak memblok. Aturan: nonaktif gudang UTAMA = BLOK; nonaktif gudang BER-STOK = PERINGATAN; Transit (sistem) tak bisa dibuat/diubah/dinonaktifkan/diutamakan."""
from typing import Optional

from . import gudang_daftar as GD
from . import gudang_jenis as GJ
from .galat_berkode import GalatBerkode

TEKS_UTAMA_TAK_NONAKTIF = "Jadikan gudang lain sebagai Utama dulu."
KODE_OTOMATIS = "GDG"
AWALAN_SISTEM = "SYS-"
LABEL = {"name": "Nama", "code": "Kode", "address": "Alamat", "city": "Kota", "province": "Provinsi", "postal_code": "Kode pos", "country": "Negara",
         "phone": "Telepon", "email": "Email", "manager_name": "Penanggung jawab", "is_branch": "Jenis", "branch_code": "Kode cabang",
         "is_default": "Gudang utama", "is_active": "Status"}
TEKS_OPSIONAL = ("address", "city", "province", "postal_code", "phone", "email", "manager_name", "branch_code")
URUT = ("code", "name", "address", "city", "province", "postal_code", "country", "phone", "email", "manager_name", "is_default", "is_active", "is_branch", "branch_code")
TEKS_BOOL = {"is_branch": (GJ.TEKS_CABANG, GJ.TEKS_BIASA), "is_active": ("Aktif", "Nonaktif"), "is_default": ("Utama", "Bukan utama")}


def blok(kode: str, pesan: str, field: Optional[str] = None, status: int = 400) -> dict:
    return {"code": kode, "message": pesan, "field": field, "status": status}


def peringatan(kode: str, pesan: str, field: Optional[str] = None) -> dict:
    return {"code": kode, "butuh_konfirmasi": True, "pesan": pesan, "message": pesan, "field": field}


def _bersih(v):
    """Teks opsional: None tetap None; spasi/"" -> None (NULL); selain itu di-trim."""
    if v is None:
        return None
    return v.strip() or None if isinstance(v, str) else v


async def kode_otomatis(conn, tid: str) -> str:
    rows = await conn.fetch("SELECT code FROM warehouses WHERE tenant_id = $1 AND code ~ '^GDG-[0-9]+$'", tid)
    return f"{KODE_OTOMATIS}-{max((int(r['code'][4:]) for r in rows), default=0) + 1:03d}"


async def _kode_dipakai(conn, tid: str, kode: str, kecuali=None) -> bool:
    return bool(await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND code = $2 AND ($3::uuid IS NULL OR id <> $3)", tid, kode, kecuali))


async def _utama_lain(conn, tid: str, kecuali=None):
    return await conn.fetchval("SELECT name FROM warehouses WHERE tenant_id = $1 AND is_default AND NOT is_system AND ($2::uuid IS NULL OR id <> $2) LIMIT 1", tid, kecuali)


def _utama_diganti(nama: str) -> dict:
    return peringatan("GUDANG_UTAMA_DIGANTI", f"Gudang utama saat ini ({nama}) akan digantikan.")


def _perubahan(w: dict, nilai: dict) -> list:
    out = []
    for k in URUT:
        if k in nilai and nilai[k] != w.get(k):
            p = {"field": k, "label": LABEL[k], "before": w.get(k), "after": nilai[k]}
            if k in TEKS_BOOL:
                ya, tidak = TEKS_BOOL[k]
                p["before_text"], p["after_text"] = (ya if w.get(k) else tidak), (ya if nilai[k] else tidak)
            out.append(p)
    return out


def _susun(blocks, perin, aksi, w, perubahan, teks, payload, nilai, **tambah) -> dict:
    r = {"aksi": aksi, "gudang": {"id": str(w["id"]) if w and w.get("id") else None, "kode": (nilai.get("code") if "code" in nilai else (w or {}).get("code")),
                                  "nama": nilai.get("name") if "name" in nilai else (w or {}).get("name")}, "perubahan": perubahan, "teks": teks}
    r.update(tambah)
    return {"blocks": blocks, "peringatan": perin, "rencana": r, "payload": payload, "nilai": nilai}


async def rencana_buat(conn, tid: str, b: dict) -> dict:
    blocks, perin = [], []
    nama = (b.get("name") or "").strip()
    if not nama:
        blocks.append(blok("GUDANG_NAMA_KOSONG", "Nama gudang wajib diisi.", "name"))
    kode = _bersih(b.get("code"))
    if kode and kode.upper().startswith(AWALAN_SISTEM):
        blocks.append(blok("GUDANG_SISTEM", f"Kode berawalan {AWALAN_SISTEM} dicadangkan untuk gudang sistem.", "code", 409))
    elif kode and await _kode_dipakai(conn, tid, kode):
        blocks.append(blok("GUDANG_KODE_SUDAH_ADA", f"Kode gudang '{kode}' sudah dipakai.", "code"))
    elif not kode:
        kode = await kode_otomatis(conn, tid)
    aktif, utama, cabang = b.get("is_active", True), bool(b.get("is_default")), bool(b.get("is_branch"))
    aktif = True if aktif is None else bool(aktif)
    if utama and not aktif:
        blocks.append(blok("GUDANG_NONAKTIF_TAK_BISA_UTAMA", "Gudang nonaktif tidak bisa jadi gudang utama.", "is_active"))
    elif utama and (lama := await _utama_lain(conn, tid)):
        perin.append(_utama_diganti(lama))
    nilai = {"code": kode, "name": nama, "country": _bersih(b.get("country")) or "Indonesia", "is_default": utama, "is_active": aktif, "is_branch": cabang}
    for k in TEKS_OPSIONAL:
        nilai[k] = _bersih(b.get(k))
    isi = {k: v for k, v in nilai.items() if v is not None and v is not False and k != "country"} | {"name": nama}
    perubahan = [{"field": k, "label": LABEL[k], "before": None, "after": nilai[k], **({"before_text": TEKS_BOOL[k][1], "after_text": TEKS_BOOL[k][0] if nilai[k] else TEKS_BOOL[k][1]} if k in TEKS_BOOL else {})}
                 for k in URUT if k in isi and k not in ("country",)]
    return _susun(blocks, perin, "buat", None, perubahan, f"Buat gudang {nama} ({kode})", b, nilai,
                  jenis_text=GJ.jenis_text(cabang), jenis_catatan=GJ.jenis_catatan(cabang))


async def rencana_ubah(conn, tid: str, w: dict, b: dict) -> dict:
    blocks, perin = [], []
    if w.get("is_system"):
        blocks.append(blok("GUDANG_SISTEM", "Gudang sistem (Transit) dikelola otomatis; tidak bisa diubah, dihapus, atau dijadikan utama.", None, 409))
        return _susun(blocks, perin, "ubah", w, [], f"Ubah gudang {w['name']}", b, {})
    nilai = {}
    for k in URUT:
        if k not in b or b[k] is None:
            continue                                   # null / tak dikirim = TIDAK diubah
        v = b[k]
        if k in TEKS_OPSIONAL:
            v = _bersih(v)                             # "" -> NULL (kosongkan)
        elif k in ("name", "code", "country") and isinstance(v, str):
            v = v.strip()
        if v != w.get(k):
            nilai[k] = v
    if "name" in b and b["name"] is not None and not (b["name"] or "").strip():
        blocks.append(blok("GUDANG_NAMA_KOSONG", "Nama gudang wajib diisi.", "name"))
        nilai.pop("name", None)
    if "code" in nilai:
        if not nilai["code"]:
            blocks.append(blok("GUDANG_KODE_SUDAH_ADA", "Kode gudang tidak boleh kosong.", "code"))
        elif nilai["code"].upper().startswith(AWALAN_SISTEM):
            blocks.append(blok("GUDANG_SISTEM", f"Kode berawalan {AWALAN_SISTEM} dicadangkan untuk gudang sistem.", "code", 409))
        elif await _kode_dipakai(conn, tid, nilai["code"], w["id"]):
            blocks.append(blok("GUDANG_KODE_SUDAH_ADA", f"Kode gudang '{nilai['code']}' sudah dipakai.", "code"))
    akhir_aktif = nilai.get("is_active", w["is_active"])
    akhir_utama = nilai.get("is_default", w["is_default"])
    if not akhir_aktif and akhir_utama:
        if nilai.get("is_default"):                    # dijadikan utama SEKALIGUS nonaktif
            blocks.append(blok("GUDANG_NONAKTIF_TAK_BISA_UTAMA", "Gudang nonaktif tidak bisa jadi gudang utama.", "is_active"))
        else:                                          # sudah utama, dinonaktifkan (tanpa melepas status utama)
            blocks.append(blok("GUDANG_UTAMA_TAK_BISA_NONAKTIF", TEKS_UTAMA_TAK_NONAKTIF, "is_active"))
    elif "is_active" in nilai and not nilai["is_active"] and (n := await GD.jumlah_berstok(conn, tid, w["id"])):
        perin.append(_berstok(n))
    if nilai.get("is_default") and akhir_aktif and (lama := await _utama_lain(conn, tid, w["id"])):
        perin.append(_utama_diganti(lama))
    return _susun(blocks, perin, "ubah", w, _perubahan(w, nilai), f"Ubah gudang {nilai.get('name', w['name'])}", b, nilai,
                  jenis_text=GJ.jenis_text(nilai.get("is_branch", w["is_branch"])), jenis_catatan=GJ.jenis_catatan(nilai.get("is_branch", w["is_branch"])))


def _berstok(n: int) -> dict:
    return peringatan("GUDANG_BERSTOK_DINONAKTIFKAN", f"Gudang masih berisi stok ({n} barang). Stoknya tetap tercatat; pindahkan atau sesuaikan bila tidak lagi dipakai.")


async def rencana_status(conn, tid: str, w: dict, aktif: bool) -> dict:
    blocks, perin = [], []
    payload = {"aktif": aktif}
    if w.get("is_system"):
        blocks.append(blok("GUDANG_SISTEM", "Gudang sistem (Transit) dikelola otomatis; tidak bisa diubah, dihapus, atau dijadikan utama.", None, 409))
    elif aktif and w["is_active"]:
        blocks.append(blok("GUDANG_SUDAH_AKTIF", "Gudang ini sudah aktif.", "is_active"))
    elif not aktif and not w["is_active"]:
        blocks.append(blok("GUDANG_SUDAH_NONAKTIF", "Gudang ini sudah nonaktif.", "is_active"))
    elif not aktif and w["is_default"]:
        blocks.append(blok("GUDANG_UTAMA_TAK_BISA_NONAKTIF", TEKS_UTAMA_TAK_NONAKTIF, "is_active"))
    elif not aktif and (n := await GD.jumlah_berstok(conn, tid, w["id"])):
        perin.append(_berstok(n))
    nilai = {"is_active": aktif}
    return _susun(blocks, perin, "aktifkan" if aktif else "nonaktifkan", w, _perubahan(w, nilai), f"{'Aktifkan' if aktif else 'Nonaktifkan'} gudang {w['name']}", payload, nilai)


async def rencana_utama(conn, tid: str, w: dict) -> dict:
    blocks, perin = [], []
    nilai = {"is_default": True}
    if w.get("is_system"):
        blocks.append(blok("GUDANG_SISTEM", "Gudang sistem (Transit) dikelola otomatis; tidak bisa diubah, dihapus, atau dijadikan utama.", None, 409))
    elif w["is_default"]:
        nilai = {}                                     # sudah utama = idempoten (sukses diam)
    elif not w["is_active"]:
        blocks.append(blok("GUDANG_NONAKTIF_TAK_BISA_UTAMA", "Gudang nonaktif tidak bisa jadi gudang utama.", "is_active"))
    elif (lama := await _utama_lain(conn, tid, w["id"])):
        perin.append(_utama_diganti(lama))
    teks = f"Gudang {w['name']} sudah gudang utama" if w["is_default"] else f"Jadikan {w['name']} gudang utama"
    return _susun(blocks, perin, "jadikan_utama", w, _perubahan(w, nilai), teks, {}, nilai)


def pratinjau(r: dict, payload=None) -> dict:
    return {"success": True, "data": {"ok": not r["blocks"], "blocks": [{"code": x["code"], "message": x["message"], "field": x["field"]} for x in r["blocks"]],
                                      "peringatan": r["peringatan"], "rencana": r["rencana"], "payload": r["payload"] if payload is None else payload}}


def tolak(blocks: list) -> None:
    """Penulis: blok pertama -> galat berkode (status 400 / 409 sistem; detail string + code/message/field tingkat atas)."""
    if blocks:
        x = blocks[0]
        raise GalatBerkode(x["status"], x["code"], x["message"], x["field"])
