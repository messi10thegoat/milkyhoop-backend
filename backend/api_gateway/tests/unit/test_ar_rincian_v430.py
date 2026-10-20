"""S1-U3 (20 Okt 2026): payment_summary detail faktur dari compute_ar_rincian (V430). compute_ar_outstanding TIDAK disentuh.

Gerbang nyata: scripts/gate_v430_rincian.sh (badan fungsi vs compute_ar_outstanding, semua faktur semua tenant) dan
scripts/ops/uji-v430-rincian.py detail (payment_summary == amount_paid/amount_due detail)."""
import inspect
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import sales_invoices as SI
from app.services import faktur_daftar as FD

MIG = Path(SI.__file__).resolve().parents[3] / "migrations"


def _baris(total, receipt, dp, cn):
    paid = receipt + dp + cn
    return {"invoice_total": total, "receipt_amount": receipt, "dp_applied_amount": dp, "credit_note_amount": cn,
            "paid_amount": paid, "outstanding": total - paid}


def test_ringkasan_terbit():
    r = FD.ringkasan_bayar_faktur("partial", Decimal("1000"), _baris(Decimal("1000"), Decimal("300"), Decimal("200"), Decimal("50")))
    assert r == {"invoice_total": 1000.0, "receipt_amount": 300.0, "dp_applied_amount": 200.0, "credit_note_amount": 50.0,
                 "paid_amount": 550.0, "remaining_amount": 450.0}


@pytest.mark.parametrize("status,sisa", [("draft", 1000.0), ("void", 0.0)])
def test_ringkasan_draf_void(status, sisa):
    r = FD.ringkasan_bayar_faktur(status, Decimal("1000"), None)
    assert r["paid_amount"] == 0.0 and r["remaining_amount"] == sisa


def test_detail_memakai_fungsi_jurnal():
    src = inspect.getsource(SI.get_invoice)
    assert "compute_ar_rincian($1) WHERE invoice_id = $2" in src
    assert "_fd.ringkasan_bayar_faktur(" in src and '"payment_summary"' in src


def test_v430_tak_menyentuh_compute_ar_outstanding():
    mig = (MIG / "V430__compute_ar_rincian.sql").read_text()
    assert "compute_ar_outstanding(" not in re.sub(r"--[^\n]*", "", mig)   # hanya disebut di komentar
    assert "CREATE OR REPLACE FUNCTION public.compute_ar_rincian(p_tenant_id text)" in mig
    assert mig.count("-- BADAN-MULAI") == 1 and mig.count("-- BADAN-SELESAI") == 1
    rb = (MIG / "V430__compute_ar_rincian_ROLLBACK.sql").read_text()
    assert "DROP FUNCTION IF EXISTS public.compute_ar_rincian(text)" in rb and "compute_ar_outstanding" not in rb.split("\n", 1)[1]
