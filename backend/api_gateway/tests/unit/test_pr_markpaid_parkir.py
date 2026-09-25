"""payment_request mark-paid DIPARKIR (25 Sep 2026, putusan MASTER).

Diukur: jurnal mark_paid ber-kolom hantu (gagal tiap panggilan), akun lewat
LIKE kode (bukan peran), akun tak ketemu -> POSTED tanpa jurnal, bayar tagihan
tanpa melunasi tagihan. 0 baris payment_requests. FE masih punya tombolnya
(PaymentRequestListPage -> markPaid) -> 409 berpesan arah pengganti.
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.routers import payment_requests as PR
from app.services import fitur_parkir as FP


def _rute(path, method):
    for r in PR.router.routes:
        if r.path == path and method in r.methods:
            return r
    raise AssertionError(f"rute {method} {path} tak ada")


def _dep_parkir(route):
    return [d.call for d in route.dependant.dependencies
            if getattr(d.call, "__qualname__", "").startswith("fitur_belum_tersedia_dengan")]


def test_mark_paid_diparkir_409_berpesan():
    deps = _dep_parkir(_rute("/api/payment-requests/{request_id}/mark-paid", "POST"))
    assert len(deps) == 1
    with pytest.raises(HTTPException) as e:
        asyncio.run(deps[0]())
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "FEATURE_NOT_AVAILABLE"
    assert "Pembayaran Tagihan" in e.value.detail["message"]


@pytest.mark.parametrize("path,method", [
    ("/api/payment-requests", "GET"),
    ("/api/payment-requests", "POST"),
    ("/api/payment-requests/{request_id}", "GET"),
    ("/api/payment-requests/{request_id}/approve", "POST"),
    ("/api/payment-requests/{request_id}/reject", "POST"),
    ("/api/payment-requests/{request_id}/cancel", "POST"),
])
def test_rute_lain_tidak_diparkir(path, method):
    assert _dep_parkir(_rute(path, method)) == []


def test_pabrik_dependensi_pesan_sendiri():
    dep = FP.fitur_belum_tersedia_dengan("X")
    with pytest.raises(HTTPException) as e:
        asyncio.run(dep())
    assert e.value.detail == {"code": "FEATURE_NOT_AVAILABLE", "message": "X"}
