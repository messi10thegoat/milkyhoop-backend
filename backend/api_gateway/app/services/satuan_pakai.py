"""S-1 (14 Nov 2026): SATU penentu pemakai satuan master + ringkasan SATU angka + aksi_tersedia seragam.

BUG-233: dua angka "satuan" berbeda = dua definisi. Di sini hanya ada satu: pemakai = barang (base_unit/satuan, tak terhapus) dan
konversi aktif yang menyebut satuan itu, dicocokkan lewat kunci normal (huruf kecil, spasi tunggal, titik/koma di ujung dibuang) dan
alias nama<->singkatan kamus. Dipakai daftar, /units/summary, filter ?aksi=, detail, dan penolak hapus. Hanya MEMBACA."""
from typing import Iterable, Optional

from .satuan_master import kunci as _kunci

AKSI = ["ubah_nama", "gabung", "nonaktifkan"]
SARING = ("semua", "terpakai", "tak_terpakai", "duplikat")
NOL = {"jumlah_barang": 0, "jumlah_konversi": 0}

SQL_BARANG = """SELECT COALESCE(NULLIF(btrim(base_unit), ''), satuan) AS u, COUNT(*) AS n FROM products
                WHERE tenant_id = $1 AND deleted_at IS NULL GROUP BY 1"""
SQL_KONVERSI = """SELECT conversion_unit AS u, COUNT(*) AS n FROM unit_conversions
                  WHERE tenant_id = $1 AND COALESCE(is_active, true) GROUP BY 1"""


def kunci_mirip(teks: Optional[str]) -> str:
    return _kunci(teks).strip(" .,")


def _peta(daftar: list) -> dict:
    """{kunci_mirip(nama|singkatan): id satuan} -- satuan pertama menang bila dua baris berbagi kunci."""
    peta = {}
    for u in daftar:
        for t in (u["name"], u["abbreviation"]):
            k = kunci_mirip(t)
            if k:
                peta.setdefault(k, u["id"])
    return peta


def tetapkan_pemakai(daftar: list, produk: Iterable, konv: Iterable) -> dict:
    """produk/konv = [(teks_satuan, jumlah)] -> {id satuan: {jumlah_barang, jumlah_konversi}} (satuan tanpa pemakai tetap ada, 0/0)."""
    peta = _peta(daftar)
    hasil = {u["id"]: dict(NOL) for u in daftar}
    for kolom, baris in (("jumlah_barang", produk), ("jumlah_konversi", konv)):
        for teks, n in baris:
            uid = peta.get(kunci_mirip(teks))
            if uid is not None:
                hasil[uid][kolom] += int(n)
    return hasil


async def muat_pemakai(conn, tenant_id: str, daftar: list) -> dict:
    produk = [(r["u"], r["n"]) for r in await conn.fetch(SQL_BARANG, tenant_id)]
    konv = [(r["u"], r["n"]) for r in await conn.fetch(SQL_KONVERSI, tenant_id)]
    return tetapkan_pemakai(daftar, produk, konv)


def _aksi(u: dict, p: dict) -> dict:
    ada = lambda: {"tersedia": True, "alasan": None}
    tak = lambda s: {"tersedia": False, "alasan": s}
    pakai = p["jumlah_barang"] + p["jumlah_konversi"]
    bawaan = bool(u.get("bawaan"))
    kunci_sistem = u.get("is_system")
    a = {}
    bagian = [f"{p['jumlah_barang']} barang"] if p["jumlah_barang"] else []
    if p["jumlah_konversi"]:
        bagian.append(f"{p['jumlah_konversi']} konversi")
    dipakai = f"Satuan masih dipakai {' dan '.join(bagian)}."
    # G3-a: aksi_tersedia = hasil penentu yang SAMA dengan pratinjau/penulis (services/satuan_rencana); gabung belum ada (G3-b, tabel alias).
    if kunci_sistem or bawaan or u["id"].startswith("bawaan:"):
        a["ubah_nama"] = tak("Satuan bawaan tidak bisa diubah namanya.")
        a["gabung"] = tak("Satuan bawaan tidak bisa digabung.")
        a["nonaktifkan"] = tak("Satuan bawaan tidak bisa dinonaktifkan.")
    elif u["id"].startswith("terpakai:"):
        a["ubah_nama"] = a["gabung"] = a["nonaktifkan"] = tak("Satuan belum terdaftar di master; daftarkan dulu lewat Buat satuan.")
    else:
        a["ubah_nama"] = tak(f"{dipakai} Nama belum bisa diganti agar barang tidak kehilangan satuan.") if pakai else ada()
        a["gabung"] = tak("Gabung satuan belum tersedia.")
        a["nonaktifkan"] = tak(dipakai) if pakai else ada()
    return a


def susun(daftar: list, pakai: dict) -> list:
    """Baris standar: daftar master + jumlah pemakai + duplikat + aksi_tersedia/aksi_semua."""
    kel = {}
    for u in daftar:
        kel.setdefault(kunci_mirip(u["abbreviation"]) or kunci_mirip(u["name"]), []).append(u["id"])
    dup = {i: k for k, ids in kel.items() if len(ids) > 1 for i in ids}
    hasil = []
    for u in daftar:
        p = pakai.get(u["id"], NOL)
        hasil.append({**u, **p, "terpakai": bool(p["jumlah_barang"] or p["jumlah_konversi"]),
                      "duplikat": u["id"] in dup, "kelompok_duplikat": dup.get(u["id"]),
                      "aksi_tersedia": _aksi(u, p), "aksi_semua": list(AKSI)})
    return hasil


def saring(baris: list, aksi: Optional[str]) -> list:
    if aksi in (None, "", "semua"):
        return list(baris)
    if aksi == "terpakai":
        return [b for b in baris if b["terpakai"]]
    if aksi == "tak_terpakai":
        return [b for b in baris if not b["terpakai"]]
    if aksi == "duplikat":
        return [b for b in baris if b["duplikat"]]
    raise ValueError(aksi)


def ringkasan(baris: list) -> dict:
    return {k: len(saring(baris, k)) for k in SARING}
