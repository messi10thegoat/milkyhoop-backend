"""PARITAS DETAIL == BARIS DAFTAR (21 Okt 2026): detail Penawaran/Penerimaan/Pengiriman/Proforma membawa medan baris (keadaan/teks, aksi, documents)
dari FUNGSI YANG SAMA dgn daftar. Nilai sama di semua baris kedua tenant: scripts/ops/uji-nyata-paritas-detail.sh."""
import inspect

from app.routers import deliveries as DL
from app.routers import proformas as PR
from app.routers import quotes as QR
from app.routers import receive_payments as RP
from app.schemas.quotes import QuoteDetail
from app.schemas.receive_payments import ReceivePaymentDetail


def _src(f):
    return inspect.getsource(f)


def test_penawaran_detail_memakai_fungsi_baris():
    d, l = _src(QR.get_quote_detail), _src(QR.list_quotes)
    for fn in ("aksi_penawaran(", "dokumen_penawaran"):
        assert fn in d and fn.replace("dokumen_penawaran", "dokumen_penawaran") in (d + l)
    assert "aksi_penawaran(" in l and "dokumen_penawaran" in l
    assert {"aksi", "doc_count", "documents", "kedaluwarsa_keadaan"} <= set(QuoteDetail.model_fields)


def test_penerimaan_detail_memakai_fungsi_baris():
    d = _src(RP.get_receive_payment)
    for fn in ("alokasi_fakta(", "bruto_dialokasi(", "kelompok_untuk(", "documents_baris(", "_PARITAS_JURNAL"):
        assert fn in d, fn
    assert "alokasi_fakta(" in _src(RP.list_receive_payments) and "kelompok_untuk(" in _src(RP.list_receive_payments)
    assert {"belum_dialokasikan", "aksi", "alokasi_keadaan", "alokasi_teks", "invoice_count", "doc_count", "documents"} <= set(ReceivePaymentDetail.model_fields)
    assert RP._PARITAS_JURNAL == {"belum_dialokasikan": 0.0, "aksi": None, "alokasi_keadaan": "tanpa", "alokasi_teks": "—"}


def test_pengiriman_detail_memakai_fungsi_baris():
    d, l = _src(DL.get_delivery_detail), _src(DL.list_deliveries)
    for fn in ("kelompok_untuk(", "documents_baris(", "serah_fakta("):
        assert fn in d and fn in l, fn


def test_proforma_detail_memakai_fungsi_baris():
    d, l = _src(PR.get_proforma_detail), _src(PR.list_proformas)
    for fn in ("aksi_proforma(", "kelompok_untuk(", "documents_baris(", "jatuh_tempo_fakta("):
        assert fn in d and fn in l, fn
