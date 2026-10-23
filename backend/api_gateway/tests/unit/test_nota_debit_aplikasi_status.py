"""ND detail applications[] membawa status penerapan V443 (23 Okt 2026, aditif; temuan ANDROID): active | reversed + reversed_at/reason.
Baris dilepas TETAP tampil (riwayat append-only), medan lama tak berubah."""
from pathlib import Path

from app.schemas.vendor_credits import VendorCreditApplicationResponse as A

SRC = (Path(__file__).resolve().parents[2] / "app/routers/vendor_credits.py").read_text()


def test_skema_aditif_bawaan_active():
    a = A(id="1", bill_id="2", amount_applied=1, application_date="2026-10-23", created_at="x")
    assert a.status == "active" and a.reversed_at is None and a.reversal_reason is None
    assert {"id", "bill_id", "bill_number", "amount_applied", "application_date", "created_at"} <= set(A.model_fields)


def test_detail_mengisi_status_tanpa_membuang_baris_dilepas():
    i = SRC.index('"applications": [')
    blok = SRC[i:SRC.index('"refunds": [', i)]
    assert '"status": app["status"] or "active"' in blok and '"reversal_reason": app["reversal_reason"]' in blok
    q = SRC[SRC.index("FROM vendor_credit_applications a"):SRC.index("ORDER BY a.application_date, a.created_at, (a.reversed_at IS NULL), a.id")]
    assert "status" not in q  # tak disaring: baris 'reversed' ikut
