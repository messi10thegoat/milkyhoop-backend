"""Pill AKSI daftar Proforma (14 Okt 2026, MASTER). Keanggotaan ditentukan SERVER; satu aturan untuk baris daftar,
?aksi= dan /summary (Python murni: terbayar proforma dihitung per-SO oleh services/proforma_terbayar -- bukan SQL).

  perlu_diterbitkan = status draft
  menunggu_bayar    = terbit, SO tidak batal, terbayar < nominal, jatuh tempo kosong ATAU > hari ini + 3
  segera_tempo      = terbit, SO tidak batal, terbayar < nominal, hari ini <= jatuh tempo <= hari ini + N (pengingat.ambang_segera; bawaan 7, dulu 3)
  lewat_tempo       = terbit, SO tidak batal, terbayar < nominal, jatuh tempo < hari ini
  (lunas / batal / kedaluwarsa / terbit pada SO batal = bukan aksi)

`due_date` proforma = JATUH TEMPO BAYAR (tak ada masa berlaku; tak ada kunci "berlaku" yang dikarang). Tak ada kunci
"perlu_dikonversi": proforma tak punya tautan/fungsi konversi ke faktur (Law 34). Hari ini = tanggal BISNIS tenant.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

AMBANG_SEGERA_TEMPO = 7  # bawaan == pengingat.AMBANG_BAWAAN; nilai tenant = pengingat.ambang_segera
AKSI = ("perlu_diterbitkan", "menunggu_bayar", "segera_tempo", "lewat_tempo")
# 23 Okt 2026 (KONTRAK-menunggu-bayar §2): kunci SARING (BUKAN pill; partisi AKSI tetap) "Belum dibayar" = SEMUA proforma terbit
# belum lunas pada SO tak batal = gabungan tiga kunci terbit (pola Zoho Retainer "Unpaid"; "lewat tempo" = subset). CATATAN: pill
# partisi `menunggu_bayar` di atas = belum jatuh tempo (label FE "Belum jatuh tempo"), BUKAN semua yang belum dibayar.
BELUM_DIBAYAR = ("menunggu_bayar", "segera_tempo", "lewat_tempo")
SARING = ("belum_dibayar",)
TOLERANSI_LUNAS = Decimal("0.005")


def proforma_lunas(nominal, terbayar) -> bool:
    """SATU rumus lunas (== serialize_proforma / kelas_bayar_terbit): terbayar + 0,005 >= nominal."""
    return Decimal(str(terbayar or 0)) + TOLERANSI_LUNAS >= Decimal(str(nominal or 0))


def aksi_proforma(status: Optional[str], so_status: Optional[str], lunas: bool, due_date: Optional[date],
                  hari_ini: date, ambang: int = AMBANG_SEGERA_TEMPO) -> Optional[str]:
    if status == "draft":
        return "perlu_diterbitkan"
    if status != "issued" or so_status == "cancelled" or lunas:
        return None
    if due_date is None:
        return "menunggu_bayar"
    if due_date < hari_ini:
        return "lewat_tempo"
    if due_date <= hari_ini + timedelta(days=ambang):
        return "segera_tempo"
    return "menunggu_bayar"
