"""Laporan UMUR piutang/hutang -- teks tampil SERVER (gelombang 4 L2, 10 Nov 2026; kontrak KONTRAK-laporan-teks-server-rupiah-20261110.md).

SATU pembantu untuk GET /api/reports/aging-receivable DAN /aging-payable (aditif; medan lama tetap):
  per_tanggal + per_text (tanggal BISNIS tenant -- dulu FE memakai tanggal browser), ember[] {kunci, label, total, persen} (SATU peta
  label -- dulu 3 versi FE berbeda), belum_jatuh_tempo_total / jatuh_tempo_total, jumlah_pihak(_text); per pihak jatuh_tempo_total;
  per dokumen nomor_text ("Kredit tanpa dokumen" bila tanpa nomor), jatuh_tempo_text, telat_text.
Uang = angka yang SAMA dengan medan lama (compute_ar/ap_outstanding, Law 16); teks hanya penyajian. Decimal, persen 1 desimal HALF_UP.
"""
from decimal import ROUND_HALF_UP, Decimal

from . import teks_galat as tg

EMBER = ("current", "1-30", "31-60", "61-90", "90+")
LABEL_EMBER = {"current": "Belum jatuh tempo", "1-30": "1–30 hari", "31-60": "31–60 hari", "61-90": "61–90 hari", "90+": "> 90 hari"}
TANPA_DOKUMEN = "Kredit tanpa dokumen"
_SEN, _PERSEN = Decimal("0.01"), Decimal("0.1")


def nilai(x) -> str:
    return str(Decimal(str(x or 0)).quantize(_SEN, rounding=ROUND_HALF_UP))


def persen(bagian, total) -> str:
    total = Decimal(str(total or 0))
    if total == 0:
        return "0.0"
    return str((Decimal(str(bagian or 0)) * 100 / total).quantize(_PERSEN, rounding=ROUND_HALF_UP))


def tambah_tampilan(hasil: dict, kunci_pihak: str, kunci_dok: str, kunci_nomor: str, label_pihak: str, hari_ini) -> dict:
    """Melengkapi respons umur (customers/invoices atau vendors/bills) dengan teks tampil server. Mengubah `hasil` di tempat."""
    summary, total = hasil["summary"], Decimal(str(hasil["total_outstanding"] or 0))
    hasil["per_tanggal"] = hari_ini.isoformat()
    hasil["per_text"] = f"Per {tg.tanggal_panjang(hari_ini)}"
    hasil["ember"] = [{"kunci": k, "label": LABEL_EMBER[k], "total": nilai(summary[k]), "persen": persen(summary[k], total)} for k in EMBER]
    belum = Decimal(str(summary["current"] or 0))
    hasil["belum_jatuh_tempo_total"] = nilai(belum)
    hasil["jatuh_tempo_total"] = nilai(total - belum)
    hasil["jumlah_pihak"] = len(hasil[kunci_pihak])
    hasil["jumlah_pihak_text"] = f"{len(hasil[kunci_pihak])} {label_pihak}"
    for p in hasil[kunci_pihak]:
        p["jatuh_tempo_total"] = nilai(Decimal(str(p["total"] or 0)) - Decimal(str(p["totals"]["current"] or 0)))
        for d in p[kunci_dok]:
            d["nomor_text"] = d.get(kunci_nomor) or TANPA_DOKUMEN
            d["jatuh_tempo_text"] = f"jatuh tempo {tg.tanggal_pendek(d['due_date'])}" if d.get("due_date") else None
            d["telat_text"] = f"telat {d['days_overdue']} hari" if (d.get("days_overdue") or 0) > 0 else None
    return hasil
