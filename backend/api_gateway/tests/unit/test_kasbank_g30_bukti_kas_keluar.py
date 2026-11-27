"""Kas&Bank G30 (27 Nov 2026): GET /api/bank-transactions/{id}/pdf = Bukti Kas Keluar Uang Keluar manual (templat kwitansi keluar)."""
import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bank_transactions as BT
from app.services import cetak_kas_keluar as CK
from app.services.pdf_service import get_pdf_service


def _baris(**ubah):
    d = {"id": uuid.uuid4(), "nomor": "UK-2611-0003", "tgl": date(2026, 11, 20), "description": "Bayar listrik",
         "pihak": "PLN", "voided_at": None, "void_reason": None, "account_name": "BCA Operasional",
         "net": Decimal("-250000.00"), "lawan_kode": "6-201", "lawan_nama": "Beban Listrik", "batal": False}
    d.update(ubah)
    return d


class _Conn:
    def __init__(self, row, tid="t1"):
        self.row, self.tid, self.sql = row, tid, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if 'FROM "Tenant"' in sql:
            return {"display_name": "Usaha", "address": None, "phone": None, "logo_url": None}
        return self.row if a and a[0] == self.tid else None


def _muat(row, tid="t1", ctx_tid="t1"):
    return asyncio.run(CK.muat_pdf_kas_keluar(_Conn(row, tid), {"tenant_id": ctx_tid}, uuid.uuid4()))


def test_label_keluar_dan_jumlah_positif_dari_net():
    d = _muat(_baris())["receipt_data"]
    assert d["amount"] == Decimal("250000.00") and d["title"] == "Bukti Kas Keluar"
    assert d["bank_name"] == "BCA Operasional" and d["purpose_ref"] == "6-201 · Beban Listrik" and d["payer_name"] == "PLN"


def test_uang_masuk_409():
    with pytest.raises(HTTPException) as e:
        _muat(_baris(net=Decimal("100")))
    assert e.value.status_code == 409


def test_tenant_lain_atau_id_rusak_404():
    with pytest.raises(HTTPException) as e:
        _muat(_baris(), tid="t1", ctx_tid="t2")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(CK.muat_pdf_kas_keluar(_Conn(_baris()), {"tenant_id": "t1"}, "bukan-uuid"))
    assert e.value.status_code == 404


def test_batal_bertanda_dan_pdf_terbentuk():
    m = _muat(_baris(batal=True, voided_at=datetime(2026, 11, 21, tzinfo=timezone.utc), void_reason="salah input"))
    ps = get_pdf_service()
    r = ps.render_payment_out(m["receipt_data"], m["tenant_info"])
    assert "DIBATALKAN" in r.html and "Bukti Kas Keluar" in r.html
    assert ps.tulis_pdf(r)[:4] == b"%PDF"


def test_izin_baca_rute_pdf_dan_rute_terdaftar():
    f = PM.PermissionMiddleware(lambda *a: None, False)._find_permission
    assert f("/api/bank-transactions/X/pdf", "GET") == ("kas_bank", "R")
    assert any(getattr(r, "path", "") == "/{transaction_id}/pdf" for r in BT.router.routes)
