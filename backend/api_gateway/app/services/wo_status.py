"""Teks status + penanda 'siap diselesaikan' Perintah Produksi (WO) dari SATU peta (BUG-184b, 12 Nov 2026; pola Biaya/B1: status mentah tetap, teks dari server).
`siap_diselesaikan` = status in_progress DAN output baik >= rencana; TIDAK menutup WO otomatis: WO tetap selesai lewat POST /{id}/complete."""
from decimal import Decimal
from typing import Optional

STATUS_TEKS = {
    "draft": "Draf",
    "planned": "Direncanakan",
    "released": "Dirilis",
    "in_progress": "Sedang Proses",
    "completed": "Selesai",
    "cancelled": "Dibatalkan",
}


def status_text(status: Optional[str]) -> Optional[str]:
    """Status DB tak dikenal -> None (tak ditebak)."""
    return STATUS_TEKS.get(status) if isinstance(status, str) else None


def siap_diselesaikan(status: Optional[str], completed, planned) -> bool:
    if status != "in_progress" or completed is None or planned is None:
        return False
    return Decimal(str(planned)) > 0 and Decimal(str(completed)) >= Decimal(str(planned))
