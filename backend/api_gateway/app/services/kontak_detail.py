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


AGREGAT = {
    # K2b (putusan MASTER 4 Nov 2026): agregat dokumen di detail kontak hanya untuk yang boleh BACA modul dokumennya
    "customer": ("sales_invoice", ("total_transactions", "total_value", "total_penjualan", "jumlah_faktur", "last_invoice_date",
                                   "jumlah_dokumen", "last_transaction_at")),
    "vendor": ("purchase_invoice", ("total_pembelian", "jumlah_tagihan", "last_bill_date", "jumlah_dokumen")),
}


async def saring_agregat(jenis: str, data: dict, boleh) -> dict:
    """Pola omitted B4: tanpa izin BACA modul dokumen -> medan agregat DIBUANG (null di JSON, FE "—"), bukan 0 palsu;
    data["omitted"] = [modul]. boleh(modul) = dashboard_izin.boleh_baca (sama dengan riwayat)."""
    modul, kunci = AGREGAT[jenis]
    if await boleh(modul):
        data["omitted"] = []
        return data
    for k in kunci:
        data[k] = None
    data["omitted"] = [modul]
    return data


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
