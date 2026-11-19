"""BUG-307 BE (19 Nov 2026): tiap baris harga memuat `min_price_text` ("Rp 60.000") dan `diskon_text` ("5%") dari SERVER (FE tak memformat).
None -> None; 0 eksplisit -> "0%" (BUG-253). Rupiah = SATU pemformat (services/teks_galat.rp, dipakai ulang sebagai HD.rp_text; riwayat tak punya salinan)."""
import asyncio
import inspect
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import price_lists as R
from app.schemas.price_lists import PriceListItemResponse
from app.services import harga_daftar as HD, harga_riwayat as HR, teks_galat as TG

APP = Path(HD.__file__).resolve().parent.parent


@pytest.mark.parametrize("v,teks", [(None, None), (0, "Rp 0"), (Decimal("60000"), "Rp 60.000"), (60000.5, "Rp 60.000,50"),
                                     (Decimal("1250000"), "Rp 1.250.000"), (Decimal("1250.5"), "Rp 1.250,50")])
def test_min_price_text(v, teks):
    assert HD.min_price_text(v) == teks


@pytest.mark.parametrize("v,teks", [(None, None), (0, "0%"), (0.0, "0%"), (Decimal("0"), "0%"), (5, "5%"), (12.5, "12.5%"), (Decimal("5.00"), "5%")])
def test_diskon_text(v, teks):
    assert HD.diskon_text(v) == teks


def test_rp_text_satu_pemformat_dan_keluaran_riwayat_tak_berubah():
    assert HD.rp_text is TG.rp
    assert not hasattr(HR, "_rp")                                    # salinan riwayat dihapus
    for v in (Decimal("60000"), Decimal("60000.50"), Decimal("1250000"), 0, Decimal("-5000")):
        assert HD.rp_text(v) == "Rp " + (f"{Decimal(str(v)):,.0f}" if Decimal(str(v)) == Decimal(str(v)).to_integral_value()
                                         else f"{Decimal(str(v)):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def test_skema_baris_memuat_dua_medan_opsional():
    f = PriceListItemResponse.model_fields
    assert "min_price_text" in f and "diskon_text" in f and f["min_price_text"].default is None and f["diskon_text"].default is None


def test_detail_memuat_dua_teks_dari_penentu_bersama():
    fn = inspect.getsource(R.get_price_list)
    assert 'HD.min_price_text(i["min_price"])' in fn and 'HD.diskon_text(i["discount_percent"])' in fn


class _Konek:
    def __init__(self, r):
        self.r = r

    async def fetchrow(self, *a):
        return self.r


def _teks(r):
    return asyncio.new_event_loop().run_until_complete(HD.teks_baris(_Konek(r), "t", "id"))


def test_respons_tambah_ubah_baris_memuat_dua_teks():
    d = _teks({"unit": "Pcs", "dasar": "Pcs", "start_date": None, "end_date": None, "min_price": Decimal("60000"), "discount_percent": Decimal("5")})
    assert d["min_price_text"] == "Rp 60.000" and d["diskon_text"] == "5%" and {"unit_text", "berlaku_text"} <= set(d)
    d = _teks({"unit": None, "dasar": "Pcs", "start_date": date(2026, 11, 1), "end_date": None, "min_price": None, "discount_percent": Decimal("0")})
    assert d["min_price_text"] is None and d["diskon_text"] == "0%"
    assert _teks(None) == {"unit_text": None, "berlaku_text": None, "min_price_text": None, "diskon_text": None}


def test_tak_ada_pemformat_rupiah_ganda_di_harga_daftar_dan_riwayat():
    for rel in ("services/harga_daftar.py", "services/harga_riwayat.py"):
        assert not re.search(r"def _rp\b", (APP / rel).read_text()), rel
