"""status_detail uang muka (MASTER GO 5 Okt 2026, opsi B): TURUNAN TAMPILAN, satu definisi untuk router uang muka,
detail SO (proforma_atribusi.uang_muka_so) dan siapa pun berikutnya. Status tersimpan (trigger) dan SEMUA penjaga uang
(status IN ('posted','partial')) TIDAK disentuh. Python (baris) dan SQL (filter/summary) WAJIB sama: dikunci tes +
harness nyata (/root/uji_dp_sd.py).
"""

SQL_STATUS_DETAIL = (
    "(CASE WHEN status = 'partial' AND COALESCE(amount_applied, 0) = 0 AND COALESCE(amount_refunded, 0) > 0 "
    "THEN 'partially_refunded' "
    "WHEN status = 'applied' AND COALESCE(amount_applied, 0) = 0 AND COALESCE(amount_refunded, 0) > 0 "
    "THEN 'refunded' ELSE status END)"
)


def status_detail_dp(status, amount_applied, amount_refunded) -> str:
    """'partially_refunded' = refund sebagian, belum dipakai; 'refunded' = habis karena refund saja; selain itu = status."""
    dipakai, kembali = (amount_applied or 0), (amount_refunded or 0)
    if dipakai == 0 and kembali > 0:
        if status == "partial":
            return "partially_refunded"
        if status == "applied":
            return "refunded"
    return status
