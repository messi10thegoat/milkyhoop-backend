"""NK POTONGAN NOMINAL (21 Okt 2026, MASTER butir c): alasan discount / pricing_error tanpa `items` -> 422 CN_BARIS_WAJIB di
pratinjau (bentuk "dari faktur" mengisi SEMUA baris faktur -- akar NK Rp 2.340.000) dan di inti buat. Alasan lain tak berubah."""
import asyncio
import inspect
from datetime import date

import pytest
from fastapi import HTTPException

from app.routers import credit_notes as CN


@pytest.mark.parametrize("reason", ["discount", "pricing_error"])
@pytest.mark.parametrize("items", [None, []])
def test_potongan_tanpa_baris_ditolak(reason, items):
    with pytest.raises(HTTPException) as e:
        CN.tolak_potongan_tanpa_baris(reason, items)
    assert e.value.status_code == 422 and e.value.detail["code"] == "CN_BARIS_WAJIB"
    assert "wajib berisi baris nominal" in e.value.detail["message"]


@pytest.mark.parametrize("reason", ["return", "damaged", "other"])
def test_alasan_lain_boleh_dari_faktur(reason):
    CN.tolak_potongan_tanpa_baris(reason, None)  # bentuk "dari faktur" tetap sah untuk retur


def test_potongan_dengan_baris_lolos():
    CN.tolak_potongan_tanpa_baris("discount", [{"description": "Potongan keterlambatan"}])


def test_pratinjau_menolak_SEBELUM_koneksi(monkeypatch):
    """422 sebelum pool/transaksi: nol kueri, nol tulisan."""
    async def tak_boleh():
        raise AssertionError("pool tak boleh disentuh")
    monkeypatch.setattr(CN, "get_pool", tak_boleh)

    class _Req:
        class state:
            user = {"tenant_id": "t1", "user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c"}
    monkeypatch.setattr(CN, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c"})
    body = CN.CreditNotePreviewRequest(reason="discount", original_invoice_id="3f1c0e64-0000-4000-8000-000000000001",
                                       credit_note_date=date(2026, 10, 21))
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.preview_credit_note(_Req(), body))
    assert e.value.status_code == 422


def test_inti_buat_memanggil_pagar():
    assert "tolak_potongan_tanpa_baris(body.reason, body.items)" in inspect.getsource(CN.buat_nota_kredit)
    assert "tolak_potongan_tanpa_baris(body.reason, body.items)" in inspect.getsource(CN.preview_credit_note)
