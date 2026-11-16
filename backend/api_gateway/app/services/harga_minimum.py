"""BUG-040 (16 Nov 2026; V501 min_price): penentu HARGA MINIMUM per baris daftar harga -- SATU sakelar perilaku sampai bukti Accurate (TESTER) tiba.
Baris daftar yang menang (harga_jual.pilih_baris) membawa `min_price`; harga satuan dokumen setelah diskon di bawahnya = pelanggaran lantai.
MODE_PENEGAKAN: "peringatan" (bawaan; bentuk BUG-207 {code, butuh_konfirmasi, pesan, message}, tulis tak diblok) | "blok" (409 JUAL_DI_BAWAH_MINIMUM) | "mati".
BELUM terpasang di SO/Faktur/Penawaran/Proforma: dipasang setelah MASTER memutuskan mode dari bukti Accurate (pola jual_bawah_hpp)."""
from decimal import Decimal
from typing import Optional

from .galat_berkode import GalatBerkode

KODE = "JUAL_DI_BAWAH_MINIMUM"
MODE_PENEGAKAN = "peringatan"
MODE_SAH = ("peringatan", "blok", "mati")


def _rp(v: Decimal) -> str:
    return f"{v:,.0f}".replace(",", ".") if v == v.to_integral_value() else f"{v:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def periksa(harga_satuan, minimum, nama: Optional[str] = None, mode: Optional[str] = None) -> Optional[dict]:
    """Harga satuan efektif vs lantai. None = aman (tanpa lantai, mode mati, atau harga >= lantai). Selain itu dict peringatan; mode 'blok' memuat blok=True."""
    mode = mode or MODE_PENEGAKAN
    if mode not in MODE_SAH:
        raise ValueError(f"mode tak dikenal: {mode}")
    if mode == "mati" or minimum is None or harga_satuan is None:
        return None
    h, m = Decimal(str(harga_satuan)), Decimal(str(minimum))
    if h >= m:
        return None
    pesan = f"Harga {nama + ' ' if nama else ''}Rp {_rp(h)} di bawah harga minimum Rp {_rp(m)}."
    return {"code": KODE, "butuh_konfirmasi": mode == "peringatan", "pesan": pesan, "message": pesan, "harga_minimum": float(m), "blok": mode == "blok"}


def tolak_bila_blok(r: Optional[dict]) -> Optional[dict]:
    """Mode blok -> 409; selain itu kembalikan peringatannya (atau None)."""
    if r and r["blok"]:
        raise GalatBerkode(409, KODE, r["pesan"])
    return r
