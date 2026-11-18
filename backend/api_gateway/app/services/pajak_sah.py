"""Pajak sah untuk barang (18 Nov 2026): SATU penentu pajak yang boleh dipilih/disimpan di barang (sales_tax_id / purchase_tax_id = tax_codes.id).
Sah = baris milik tenant ini, aktif, arah cocok (output = penjualan, input = pembelian; arah kosong = PPh/Tanpa Pajak boleh di kedua arah). Tak ada saringan PKP/non-PKP
(non-PKP hanya memengaruhi kapitalisasi PPN di dokumen). Dipakai penulis barang (items.nama_pajak_item) DAN GET /items/pajak-pilihan -> daftar ≡ penulis."""
from decimal import Decimal
from typing import Optional

JENIS = {"penjualan": "output", "pembelian": "input"}


def alasan_tolak(arah: str, tc: Optional[dict]) -> Optional[str]:
    """None = sah; selain itu frasa untuk 'Pajak item {frasa}.' (kalimat penulis lama dipertahankan)."""
    if not tc:
        return "tidak ditemukan"
    if not tc["is_active"]:
        return "tidak aktif"
    if tc["direction"] and tc["direction"] != arah:
        return "bukan pajak penjualan" if arah == "output" else "bukan pajak pembelian"
    return None


def pajak_sah(arah: str, tc: dict) -> bool:
    return alasan_tolak(arah, tc) is None


def teks_pajak(nama: str, tarif) -> str:
    """'PPN 11%' (nama sudah memuat tarif) tidak digandakan; 'PPh 23' + 2 -> 'PPh 23 (2%)'; tarif 0 -> nama saja."""
    t = Decimal(str(tarif or 0)).normalize()
    t = f"{t:f}"
    return nama if (t == "0" or f"{t}%" in nama) else f"{nama} ({t}%)"


async def daftar_pajak(conn, tid: str, jenis: str) -> list:
    """Pajak pilihan tenant untuk <jenis> (penjualan|pembelian), urut kode, disaring pajak_sah -> [{id, kode, nama, tarif, text}]."""
    arah = JENIS[jenis]
    rows = await conn.fetch("SELECT id, code, name, rate, direction, is_active FROM tax_codes WHERE tenant_id = $1 AND is_active = true ORDER BY code ASC", tid)
    return [{"id": str(r["id"]), "kode": r["code"], "nama": r["name"], "tarif": f"{Decimal(str(r['rate'] or 0)).normalize():f}", "text": teks_pajak(r["name"], r["rate"])}
            for r in map(dict, rows) if pajak_sah(arah, r)]
