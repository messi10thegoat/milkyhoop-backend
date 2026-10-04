"""Alokasi penerimaan VOID tetap 'active' di DB (MASTER 5 Okt: tanpa migrasi/backfill; angka piutang dari jurnal tak
bergantung status ini) -> API detail penerimaan memberi allocations[].status_detail = 'voided'. Dilepas tetap 'reversed'.
Kwitansi penerimaan void sudah berstempel DIBATALKAN (_tanda_batal mengenali 'voided')."""
import inspect

from app.routers import receive_payments as RP
from app.schemas.receive_payments import AllocationResponse
from app.services.pdf_service import PDFService


def test_status_detail_alokasi():
    s = " ".join(inspect.getsource(RP.get_receive_payment).split())
    assert '"status_detail": ("reversed" if alloc["status"] == "reversed" else "voided" if payment["status"] in ("void", "voided") else "active")' in s
    assert '"status": "reversed" if alloc["status"] == "reversed" else "active"' in s  # status lama tak berubah


def test_skema_mendeklarasikan_status_detail():
    assert "status_detail" in AllocationResponse.model_fields  # tanpa ini response_model membuangnya diam-diam


def test_kwitansi_void_berstempel():
    assert "voided" in PDFService._STATUS_BATAL
    assert "batal=self._tanda_batal(receipt_data)" in inspect.getsource(PDFService)
    svc = PDFService.__new__(PDFService)
    assert svc._tanda_batal({"status": "voided", "voided_at": "2026-10-05T01:00:00+00:00"}) is not None
    assert svc._tanda_batal({"status": "posted"}) is None
