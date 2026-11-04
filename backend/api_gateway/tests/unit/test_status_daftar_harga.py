"""Status berlaku daftar harga dari SERVER (ANDROID U3b, 4 Nov 2026): FE tak menghitung; aturan == penentu harga (_berlaku)."""
import inspect
from datetime import date, timedelta
from itertools import product

import pytest

from app.routers import price_lists as PL
from app.services import harga_jual as HJ

H = date(2026, 11, 4)


@pytest.mark.parametrize("aktif,mulai,akhir,harap", [
    (True, None, None, ("aktif", "Aktif", False)),
    (False, None, None, ("nonaktif", "Nonaktif", False)),
    (True, None, H - timedelta(days=1), ("kedaluwarsa", "Kedaluwarsa", True)),
    (True, H + timedelta(days=1), None, ("belum_berlaku", "Belum berlaku", False)),
    (True, H, H, ("aktif", "Aktif", False)),                      # batas inklusif, sama dengan _berlaku
    (False, None, H - timedelta(days=1), ("nonaktif", "Nonaktif", False)),  # nonaktif menang
])
def test_status(aktif, mulai, akhir, harap):
    s = HJ.status_daftar({"is_active": aktif, "start_date": mulai, "end_date": akhir}, H)
    assert (s["status"], s["status_text"], s["is_expired"]) == harap


def test_aktif_tepat_bila_penentu_harga_memakainya():
    hari = [None, H - timedelta(days=1), H, H + timedelta(days=1)]
    for aktif, mulai, akhir in product([True, False], hari, hari):
        r = {"is_active": aktif, "start_date": mulai, "end_date": akhir}
        assert (HJ.status_daftar(r, H)["status"] == "aktif") == (aktif and HJ._berlaku(r, H)), r


def test_daftar_dan_detail_memakai_tanggal_tenant():
    for fn in (PL.list_price_lists, PL.get_price_list):
        s = inspect.getsource(fn)
        assert "HJ.status_daftar(" in s and "tanggal_dokumen(conn, ctx[\"tenant_id\"])" in s, fn.__name__
