"""T-G1 + T-G3 (uji nyata grosir 5 Nov 2026, BACKEND2; brief MASTER fix/tg-pesan).

T-G1: nominal bersen pada presisi 0 ditolak dengan galat BERKODE NOMINAL_HARUS_RUPIAH + kalimat uang_presisi di SEMUA jalur
(terima: buat_penerimaan/rute HTTP · bayar keluar: POST /bill-payments · bayar tagihan: POST /bills/{id}/payments).
T-G3: pesan respons Terima pembayaran berbahasa Indonesia (format nomor RCV TIDAK diubah: putusan pemilik nanti).
"""
import asyncio
import inspect
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import bills as RB
from app.routers import receive_payments as RP
from app.services import bayar_keluar_rencana as BKR
from app.services import uang_presisi as UP
from app.services.galat_berkode import GalatBerkode

BADAN = {"detail": UP.PESAN_NOMINAL_RUPIAH, "code": UP.KODE_NOMINAL_RUPIAH, "message": UP.PESAN_NOMINAL_RUPIAH}


# ── T-G1 bayar keluar (POST /bill-payments): blok tulis = galat berkode ───────────────────────────────────────────

def test_blok_tulis_melempar_galat_berkode_nominal():
    with pytest.raises(GalatBerkode) as e:
        BKR.Blok(False).galat(UP.KODE_NOMINAL_RUPIAH, 422, UP.PESAN_NOMINAL_RUPIAH)
    assert e.value.status_code == 422 and e.value.badan() == BADAN


def test_blok_tulis_detail_bukan_kalimat_tetap_http_biasa():
    with pytest.raises(HTTPException) as e:
        BKR.Blok(False).galat("X", 400, {"code": "X", "rincian": [1]})
    assert not isinstance(e.value, GalatBerkode) and e.value.detail == {"code": "X", "rincian": [1]}


# ── T-G1 bayar tagihan (POST /bills/{id}/payments): blok penentu -> result.code -> galat berkode ─────────────────────

class _K:
    """Tagihan posted bersisa 1.000; nominal bersen 500,50 pada presisi 0."""

    async def fetchrow(self, sql, *a):
        assert "FROM bills" in sql
        return {"id": uuid4(), "amount": Decimal("1000"), "amount_paid": Decimal("0"), "status": "posted", "status_v2": "posted",
                "ap_id": None, "vendor_id": uuid4(), "vendor_name": "V", "invoice_number": "BILL-X",
                "total_tagihan": Decimal("1000"), "tax_amount": Decimal("0")}

    async def fetchval(self, sql, *a):
        return Decimal("1000")


def test_bayar_tagihan_bersen_blok_berkode_sampai_galat_rute(monkeypatch):
    async def _p0(conn, tid):
        return 0
    monkeypatch.setattr(UP, "presisi_tenant", _p0)
    r = asyncio.run(BKR.rencana_bayar_tagihan(_K(), "t", uuid4(), {"amount": Decimal("500.50")}))
    assert [(b["code"], b["status"], b["message"]) for b in r["blocks"]] == [(UP.KODE_NOMINAL_RUPIAH, 422, UP.PESAN_NOMINAL_RUPIAH)]
    # bills_service meneruskan kode blok pertama; rute mengubahnya jadi galat berkode
    bs = inspect.getsource(__import__("app.services.bills_service", fromlist=["x"]).BillsService.record_payment)
    assert '"code": _b["code"]' in bs
    e = RB._galat_bayar({"success": False, "message": UP.PESAN_NOMINAL_RUPIAH, "code": UP.KODE_NOMINAL_RUPIAH, "status_code": 422})
    assert isinstance(e, GalatBerkode) and e.status_code == 422 and e.badan() == BADAN


def test_galat_bayar_tanpa_kode_tetap_kalimat_polos():
    e = RB._galat_bayar({"success": False, "message": "Gagal."})
    assert type(e) is HTTPException and (e.status_code, e.detail) == (400, "Gagal.")


def test_rute_bayar_dan_bayar_penuh_memakai_galat_bayar():
    src = inspect.getsource(RB)
    assert src.count("raise _galat_bayar(result)") == 2


# ── T-G1 terima (buat_penerimaan = rute HTTP + SO/SI terima bayar): galat berkode + handler main.py merender kode ─────

def test_terima_bersen_galat_berkode_dan_handler_merender_kode():
    s = inspect.getsource(RP._periksa_penerimaan)
    assert "raise GalatBerkode(422, UP.KODE_NOMINAL_RUPIAH, _tolak)" in s
    assert "await _periksa_penerimaan(conn, ctx, body)" in inspect.getsource(RP.buat_penerimaan)
    from app.main import _galat_berkode_handler
    resp = asyncio.run(_galat_berkode_handler(None, GalatBerkode(422, UP.KODE_NOMINAL_RUPIAH, UP.PESAN_NOMINAL_RUPIAH)))
    import json
    assert resp.status_code == 422 and json.loads(resp.body) == BADAN


# ── T-G3 pesan respons Terima pembayaran ──────────────────────────────────────────────────────────────────────────

def test_pesan_terima_bahasa_indonesia():
    s = inspect.getsource(RP.buat_penerimaan)
    assert 'result["message"] = "Penerimaan dibuat dan diposting"' in s
    assert "Receive payment created and posted" not in s
