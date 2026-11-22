"""DAFTAR HARGA standar SO (D1, putusan pemilik 13 Nov 2026; PETA-be-daftar-harga-20261113): SATU penentu kunci aksi, teks posisi, aksi_tersedia,
dan teks baris harga untuk GET /api/price-lists (?aksi=), /summary, detail, dan respons tambah/ubah baris.

Kunci (tanggal = tanggal bisnis tenant, sama dgn services/harga_jual.status_daftar):
  kedaluwarsa   = aktif ∧ end_date < tanggal                  PILL
  belum_berlaku = aktif ∧ start_date > tanggal                PILL
  nonaktif      = NOT aktif                                   SARING (bukan pill)
  bawaan        = bawaan ∧ aktif                              SARING
Baris harga: unit_text = nama satuan ("<satuan dasar>" bila unit kosong); berlaku_text = masa berlaku baris ("Ikut daftar" bila kosong)."""
from datetime import date
from typing import Optional

from ..schemas.aksi_tersedia import aksi
from .teks_galat import rp as rp_text  # SATU pemformat Rupiah ("Rp 60.000", sen hanya bila != 0)

AKSI = ("kedaluwarsa", "belum_berlaku")
SARING = ("nonaktif", "bawaan")
SEMUA_KUNCI = AKSI + SARING
PAKAI_HARI = ("kedaluwarsa", "belum_berlaku")  # hanya predikat ini memakai tanggal bisnis (penanda parameter); selain itu tanpa parameter
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def sql_aksi(kunci: str, a: str = "p", hari: str = "$2") -> str:
    return {
        "kedaluwarsa": f"({a}.is_active AND {a}.end_date IS NOT NULL AND {a}.end_date < {hari}::date)",
        "belum_berlaku": f"({a}.is_active AND {a}.start_date IS NOT NULL AND {a}.start_date > {hari}::date)",
        "nonaktif": f"(NOT {a}.is_active)",
        "bawaan": f"({a}.is_default AND {a}.is_active)",
    }[kunci]


def tgl_text(d: date) -> str:
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def berlaku_text(mulai: Optional[date], sampai: Optional[date]) -> str:
    if mulai and sampai:
        return f"{tgl_text(mulai)} – {tgl_text(sampai)}"
    if mulai:
        return f"Mulai {tgl_text(mulai)}"
    if sampai:
        return f"Sampai {tgl_text(sampai)}"
    return "Ikut daftar"


def unit_text(unit: Optional[str], dasar: Optional[str]) -> Optional[str]:
    """Nama satuan baris; unit kosong = NAMA satuan dasar barang (mis. "Pcs"). Barang tanpa base_unit dan satuan (data cacat) = None (FE "—", tanpa teks palsu)."""
    u = " ".join((unit or "").split())
    if u:
        return u
    return " ".join((dasar or "").split()) or None


def position_text(is_active: bool, is_default: bool, status: str) -> str:
    teks = {"aktif": "Aktif", "nonaktif": "Nonaktif", "kedaluwarsa": "Kedaluwarsa", "belum_berlaku": "Belum berlaku"}[status]
    return f"{teks} · Bawaan" if (is_default and is_active and status == "aktif") else teks


def aksi_tersedia(r, pelanggan_count: int) -> dict:
    aktif, bawaan = bool(r["is_active"]), bool(r["is_default"])
    if bawaan and aktif:
        alasan_bawaan = "Daftar ini sudah bawaan."
    elif not aktif:
        alasan_bawaan = "Daftar nonaktif tidak bisa jadi bawaan."
    else:
        alasan_bawaan = None
    n = int(pelanggan_count or 0)
    return {
        "ubah": aksi(True, None),
        "jadikan_bawaan": aksi(alasan_bawaan is None, alasan_bawaan),
        "nonaktifkan": aksi(aktif, "Daftar ini sudah nonaktif."),
        "aktifkan": aksi(not aktif, "Daftar ini sudah aktif."),
        "hapus": aksi(n == 0, f"Daftar dipakai {n} pelanggan; pindahkan pelanggannya dulu."),
    }


def min_price_text(v) -> Optional[str]:
    """Teks harga minimum baris; None (tak diatur) -> None."""
    return None if v is None else rp_text(v)


def diskon_text(v) -> Optional[str]:
    """Teks diskon baris: 5 -> '5%', 12.5 -> '12.5%'; None (tak diatur) -> None; 0 eksplisit -> '0%' (BUG-253)."""
    return None if v is None else f"{float(v):g}%"


async def teks_baris(conn, tid: str, line_id) -> dict:
    """{unit_text, berlaku_text, min_price_text, diskon_text} untuk SATU baris harga (respons tambah/ubah baris); bersaring tenant lewat price_lists."""
    r = await conn.fetchrow(
        """SELECT pli.unit, pli.start_date, pli.end_date, pli.min_price, pli.discount_percent, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar
           FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
           WHERE pli.id = $1 AND pl.tenant_id = $2""", line_id, tid)
    if not r:
        return {"unit_text": None, "berlaku_text": None, "min_price_text": None, "diskon_text": None}
    return {"unit_text": unit_text(r["unit"], r["dasar"]), "berlaku_text": berlaku_text(r["start_date"], r["end_date"]),
            "min_price_text": min_price_text(r["min_price"]), "diskon_text": diskon_text(r["discount_percent"])}


def _tgl(d) -> Optional[str]:
    return d.isoformat() if d else None


async def harga_daftar_barang(conn, tid: str, item_id, dasar: Optional[str], tanggal: date) -> list:
    """BUG-337: harga barang di SEMUA daftar harga AKTIF tenant yang punya baris barang itu (baca-saja; server tak menyaring tingkat/berjangka -- FE memilih).
    Satu kueri (price_list_items JOIN price_lists, baris aktif = predikat harga_saran COALESCE(is_active,true)). berlaku_hari_ini = harga_jual._berlaku
    (tanggal bisnis tenant) pada masa berlaku BARIS; status daftar = harga_jual.status_daftar. Urut: bawaan dulu, priority, nama, id; baris: unit, min_quantity, mulai, id."""
    from .harga_jual import _berlaku, status_daftar
    rows = await conn.fetch(
        """SELECT p.id AS pl_id, p.name AS pl_name, p.code AS pl_code, p.is_default, p.is_active, p.start_date AS pl_start, p.end_date AS pl_end,
                  pli.unit, pli.price, pli.min_quantity, pli.start_date, pli.end_date
           FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id AND p.tenant_id = $1
           WHERE pli.item_id = $2 AND p.is_active AND COALESCE(pli.is_active, true)
           ORDER BY p.is_default DESC, p.priority, p.name, p.id, pli.unit NULLS FIRST, pli.min_quantity, pli.start_date NULLS FIRST, pli.id""",
        tid, item_id)
    out, idx = [], {}
    for r in rows:
        k = str(r["pl_id"])
        if k not in idx:
            st = status_daftar({"is_active": r["is_active"], "start_date": r["pl_start"], "end_date": r["pl_end"]}, tanggal)
            idx[k] = {"price_list_id": k, "nama": r["pl_name"], "kode": r["pl_code"], "is_default": bool(r["is_default"]), "aktif": bool(r["is_active"]),
                      "status": st["status"], "status_text": st["status_text"], "baris": []}
            out.append(idx[k])
        idx[k]["baris"].append({"unit": r["unit"], "unit_text": unit_text(r["unit"], dasar), "min_quantity": float(r["min_quantity"]), "harga": r["price"],
                                "berlaku_mulai": _tgl(r["start_date"]), "berlaku_sampai": _tgl(r["end_date"]),
                                "berlaku_hari_ini": _berlaku(r, tanggal)})
    return out
