"""DOKUMEN-BATAL-FIELD (15 Okt 2026, MASTER): bundel GET /sales-orders/{id}/documents membawa `batal: bool` per dokumen, dari
penentu YANG SAMA dengan 409 DOKUMEN_BATAL share (dokumen.dokumen_batal). Aditif; penawaran selalu False (penentu sendiri)."""
import inspect

import pytest

from app.routers import dokumen as DK


@pytest.mark.parametrize("kind,status,harap", [
    ("invoice", "void", True), ("delivery", "voided", True), ("proforma", "cancelled", True), ("rekap", "cancelled", True),
    ("receipt", "VOID", True), ("credit_note", "void", True), ("invoice", "posted", False), ("rekap", "confirmed", False),
    ("quotation", "cancelled", False), ("proforma", None, False),
])
def test_penentu_tunggal(kind, status, harap):
    assert DK.dokumen_batal(kind, status) is harap


def test_tolak_batal_dan_bundel_memakai_penentu_yang_sama():
    assert "dokumen_batal(kind, dok.get(\"status\"))" in inspect.getsource(DK.tolak_batal)
    from app.services import dokumen_bundel as _DBN  # S1-U2: penyelesai bersama rampungkan()
    src = inspect.getsource(_DBN.rampungkan)
    assert "DB.rampungkan(conn, tid, kelompok)" in inspect.getsource(DK.susun_dokumen)
    assert 'd["batal"] = bool(row) and dokumen_batal(d["kind"], row["status"])' in src
    assert "_baris_dokumen(conn, tid, d[\"kind\"]" in src
    assert "_baris_dokumen(conn, tid, kind, uid)" in inspect.getsource(DK._dokumen_ada)   # satu pembaca status
