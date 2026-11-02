"""PROFORMA-PRATINJAU-BUAT (2 Nov 2026; disetujui MASTER): penentu BUAT SATU (_rencana_buat) untuk POST /proformas DAN
POST /proformas/preview. Nominal dari persen dihitung SERVER (nominal_dari_persen, Decimal HALF_UP rupiah; Law 9/25); blok
memuat semua penghalang dengan kalimat = 400 buat (termasuk plafon "melebihi sisa yang bisa ditagih"); pratinjau READ ONLY
tanpa kunci; buat mengangkat blok PERTAMA (status + detail sama dengan pemeriksaan lama)."""
import asyncio
import inspect
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import proformas as P

T = "tenant-uji"


def _j(c):
    return asyncio.run(c)


def body(**k):
    d = {"sales_order_id": str(uuid.uuid4()), "purpose": "dp"}
    d.update(k)
    return P.CreateProformaRequest(**d)


def _pasang(monkeypatch, status="confirmed", total=Decimal("8025000"), sisa=Decimal("8025000")):
    async def order(conn, tid, oid):
        return {"id": oid, "order_number": "SO-1", "status": status, "total_amount": total}

    async def rincian(conn, tid, oid, ot, exclude_id=None):
        return {"order_total": ot, "issued_total": float(total - sisa), "received_total": 0.0, "received_not_billed": 0.0,
                "billable_remaining": float(sisa)}
    monkeypatch.setattr(P, "fetch_order_or_404", order)
    monkeypatch.setattr(P, "rincian_tagih", rincian)


def _r(b):
    return _j(P._rencana_buat(None, {"tenant_id": T}, b, uuid.uuid4(), pratinjau=True))


def test_persen_jadi_nominal_server(monkeypatch):
    _pasang(monkeypatch)
    r = _r(body(percent_of_order=Decimal("62.31")))
    assert r["blocks"] == [] and r["amount"] == float(P.nominal_dari_persen(Decimal("8025000"), Decimal("62.31")))
    assert r["amount"] == 5000378.0 and r["impact"]["before"]["billable_remaining"] == 8025000.0


def test_semua_blok_urut_kalimat_sama_dengan_buat(monkeypatch):
    _pasang(monkeypatch, status="draft")
    r = _r(body(purpose="xx", percent_of_order=Decimal("150")))
    assert [b["code"] for b in r["blocks"]] == ["PROFORMA_PURPOSE_INVALID", "SO_NOT_BILLABLE", "PROFORMA_PERCENT_INVALID"]
    assert r["blocks"][2]["message"] == "Persen dari pesanan harus di antara 0 dan 100."
    _pasang(monkeypatch)
    assert [b["message"] for b in _r(body())["blocks"]] == ["Isi salah satu: persen dari pesanan atau jumlah."]
    assert [b["message"] for b in _r(body(amount=Decimal("0")))["blocks"]] == ["amount harus lebih besar dari 0."]


def test_plafon_kalimat_sama_dengan_400(monkeypatch):
    _pasang(monkeypatch, sisa=Decimal("1000000"))
    r = _r(body(amount=Decimal("2000000")))
    assert [b["code"] for b in r["blocks"]] == ["PROFORMA_EXCEEDS_BILLABLE"]
    with pytest.raises(HTTPException) as e:
        _j(P.assert_within_order_total(None, T, uuid.uuid4(), 8025000.0, 2000000.0))
    assert r["blocks"][0]["message"] == e.value.detail and "melebihi sisa yang bisa ditagih" in e.value.detail


def test_buat_memakai_penentu_dan_angkat_blok_pertama():
    s = inspect.getsource(P.create_proforma)
    assert "r = await _rencana_buat(conn, ctx, body, oid)" in s and "_angkat_blok_pertama(r)" in s
    assert s.index('pg_advisory_xact_lock(hashtext($1))", f"PROFORMA_SO:') < s.index("_rencana_buat(")
    assert "nominal_dari_persen" not in s and "assert_within_order_total" not in s  # satu tempat: penentu


def test_pratinjau_baca_saja_tanpa_kunci_penentu_sama():
    s = inspect.getsource(P.preview_create_proforma)
    assert "conn.transaction(readonly=True)" in s and "_rencana_buat(conn, ctx, body, oid, pratinjau=True)" in s
    assert "pg_advisory" not in s and "INSERT" not in s
