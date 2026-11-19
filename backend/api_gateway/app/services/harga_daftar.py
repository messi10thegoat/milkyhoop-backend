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
