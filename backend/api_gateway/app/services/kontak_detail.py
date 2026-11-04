"""Detail KONTAK (Pelanggan / Vendor) -- B2 (4 Nov 2026, kontrak CONVERSATIONAL; desain disetujui MASTER): teks server + aksi
[{kunci, label, utama?, bahaya?, boleh, alasan?}] (pola medan aksi SO). boleh/alasan dari pemeriksa YANG SAMA dengan penulisnya
(hapus = smart_delete.cek_jejak, saldo awal = pemeriksa rute saldo awal). Angka saldo / lewat tempo = services/kontak_daftar.
"""
from typing import Optional

TIPE = {"BADAN": "Badan usaha", "ORANG_PRIBADI": "Orang pribadi"}


def tipe_text(kode: Optional[str]) -> Optional[str]:
    return TIPE.get((kode or "").upper()) if kode else None


def termin_text(hari) -> str:
    n = int(hari or 0)
    return "Tunai" if n <= 0 else f"Net {n} hari"


def aksi_detail(is_active: bool, alasan_hapus: Optional[str], saldo_awal_ada: Optional[str]) -> list:
    """Urutan tampil tetap; boleh=False membawa alasan berkalimat (sama dengan galat penulisnya)."""
    def a(kunci, label, boleh=True, alasan=None, **kw):
        return {"kunci": kunci, "label": label, **kw, "boleh": boleh, **({"alasan": alasan} if alasan else {})}
    out = [a("ubah", "Ubah", utama=True),
           a("saldo_awal", "Saldo awal", boleh=not saldo_awal_ada,
             alasan=f"Saldo awal sudah dicatat ({saldo_awal_ada}). Batalkan dulu untuk menggantinya." if saldo_awal_ada else None),
           a("gabung", "Gabungkan")]
    out.append(a("nonaktifkan", "Nonaktifkan") if is_active else a("aktifkan", "Aktifkan"))
    out.append(a("hapus", "Hapus", bahaya=True, boleh=alasan_hapus is None, alasan=alasan_hapus))
    return out
