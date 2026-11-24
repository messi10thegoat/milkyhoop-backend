"""Teks & aksi Stok Opname DARI SERVER (kontrak FE, 24 Nov 2026; pola "server menentukan", tanpa migrasi).
aksi_tersedia{mulai,isi,post,batal,void,hapus}:{tersedia,alasan}, position_text, selisih + nilai BERTANDA (minus U+2212, sesuai BUG-228-3).
Angka tetap numerik; teks hanya tampilan. Murni (tanpa DB): dipakai daftar, detail, dan /post/preview."""
from decimal import Decimal
from typing import Optional

from . import stok_sesuai as SS

AKSI = ("mulai", "isi", "post", "batal", "void", "hapus")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def _fmt(d: Decimal) -> str:
    s = format(_d(d).normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def aksi_tersedia(status: str, jumlah_baris: int = 0) -> dict:
    ok = lambda: {"tersedia": True, "alasan": None}
    tak = lambda s: {"tersedia": False, "alasan": s}
    draf, hitung, posted = status == "draft", status == "hitung", status == "posted"
    selesai = {"void": "Opname sudah dibatalkan (void).", "batal": "Opname sudah dibatalkan."}.get(status)
    return {
        "mulai": (ok() if jumlah_baris else tak("Opname belum punya barang untuk dihitung.")) if draf
        else tak(selesai or ("Opname sudah dimulai." if hitung else "Opname sudah selesai.")),
        "isi": ok() if (draf or hitung) else tak(selesai or "Opname sudah selesai; hasil hitung tidak bisa diubah."),
        "post": ok() if hitung else tak("Opname belum dimulai; mulai hitung dulu." if draf else (selesai or "Opname sudah selesai.")),
        "batal": ok() if hitung else tak("Draf dihapus, bukan dibatalkan." if draf else (selesai or "Opname sudah selesai; gunakan void.")),
        "void": ok() if posted else tak("Hanya opname yang sudah selesai yang bisa di-void." if (draf or hitung) else (selesai or "Opname sudah dibatalkan.")),
        "hapus": ok() if draf else tak("Hanya draf yang bisa dihapus; yang sedang dihitung dibatalkan, yang selesai di-void."),
    }


def position_text(status: str, jumlah_baris: int = 0, sudah_dihitung: int = 0, umur_hari: Optional[int] = None) -> str:
    if status == "draft":
        return "Draf · belum dimulai"
    if status == "hitung":
        hari = f" · hari ke-{int(umur_hari) + 1}" if umur_hari is not None else ""
        return f"Sedang dihitung · {sudah_dihitung}/{jumlah_baris} barang{hari}"
    if status == "posted":
        return "Selesai"
    if status == "void":
        return "Dibatalkan (void)"
    return "Dibatalkan sebelum selesai"


def selisih_text(selisih, unit: Optional[str]) -> Optional[str]:
    """'+3 pcs' / '−2 pcs' / '0 pcs'; None (belum dihitung) -> None."""
    if selisih is None:
        return None
    d = _d(selisih)
    u = f" {unit}" if unit else ""
    return f"+{_fmt(d)}{u}" if d > 0 else (f"−{_fmt(abs(d))}{u}" if d < 0 else f"0{u}")


def medan_baris(selisih, magnitudo_nilai, unit: Optional[str]) -> dict:
    """selisih = qty bertanda (None = belum dihitung); magnitudo_nilai = |selisih| x harga pokok. -> medan tambahan baris."""
    if selisih is None:
        return {"selisih": None, "selisih_text": None, "nilai_bertanda": None, "nilai_bertanda_text": None}
    nb = SS.nilai_bertanda(selisih, magnitudo_nilai or 0)
    return {"selisih": _d(selisih), "selisih_text": selisih_text(selisih, unit), "nilai_bertanda": nb, "nilai_bertanda_text": SS.teks_bertanda(nb)}


def total_bertanda(total) -> dict:
    t = _d(total)
    return {"total_nilai_bertanda": t, "total_nilai_bertanda_text": SS.teks_bertanda(t)}
