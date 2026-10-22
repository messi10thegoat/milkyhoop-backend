"""Bundel Pembayaran Keluar: jangkar draf/batal paid = balance = null (pola bundel UM vendor); posted = string 2dp (23 Okt 2026, temuan WORKSPACE)."""
import inspect

from app.routers import bill_payments as BP


def test_jangkar_paid_balance_hanya_posted():
    s = inspect.getsource(BP.get_payment_documents)
    assert """"paid": f"{Decimal(str(f['dialokasikan'])):.2f}" if p["status"] == "posted" else None""" in s
    assert """"balance": f"{Decimal(str(f['belum_dialokasikan'])):.2f}" if p["status"] == "posted" else None""" in s
