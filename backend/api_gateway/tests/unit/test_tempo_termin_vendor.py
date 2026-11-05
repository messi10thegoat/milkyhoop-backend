"""tempo-termin-vendor (5 Nov 2026): jatuh tempo tagihan dari PO = isian pengguna -> termin vendor -> tanggal faktur.
Dulu /to-bill dan pratinjaunya memakai `due_date or bill_date` -> tagihan dari PO lewat tempo sejak besok."""
import inspect
import os
from datetime import date

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import termin_bayar as TB  # noqa: E402
from app.routers import purchase_orders as POR  # noqa: E402

TGL = date(2026, 11, 5)
VND = "40000000-0000-0000-0000-000000000001"


class _K:
    def __init__(self, hari):
        self.hari, self.q = hari, []

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        return self.hari


@pytest.mark.asyncio
async def test_isian_pengguna_selalu_menang_tanpa_baca_vendor():
    k = _K(30)
    jt = await TB.tentukan_jatuh_tempo_tagihan(k, "t", TGL, date(2026, 11, 20), VND)
    assert jt == {"due_date": date(2026, 11, 20), "due_date_source": "body", "payment_terms_days": None,
                  "due_date_text": "Diisi pengguna"}
    assert k.q == []


@pytest.mark.asyncio
async def test_termin_vendor_30_bertenant():
    k = _K(30)
    jt = await TB.tentukan_jatuh_tempo_tagihan(k, "t", TGL, None, VND)
    assert jt == {"due_date": date(2026, 12, 5), "due_date_source": "vendor_terms", "payment_terms_days": 30,
                  "due_date_text": "Termin vendor 30 hari"}
    sql, a = k.q[0]
    assert "FROM vendors" in sql and "tenant_id = $2" in sql and a == (VND, "t")  # Law 24


@pytest.mark.asyncio
@pytest.mark.parametrize("hari,vendor", [(0, VND), (None, VND), (30, None), (400, VND)])
async def test_tanpa_termin_sah_jatuh_ke_tanggal_faktur(hari, vendor):
    # 0 = vendor tunai; None = vendor tenant lain/tak ada; >365 = salah ketik -> diabaikan
    jt = await TB.tentukan_jatuh_tempo_tagihan(_K(hari), "t", TGL, None, vendor)
    assert jt == {"due_date": TGL, "due_date_source": "default", "payment_terms_days": 0, "due_date_text": "Tanggal faktur"}


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def test_to_bill_dan_pratinjau_memakai_penentu_yang_sama():
    tulis, lihat = _src(POR.convert_to_bill), _src(POR.preview_convert_to_bill)
    for src in (tulis, lihat):
        assert "await tentukan_jatuh_tempo_tagihan(" in src
        assert 'po["vendor_id"]' in src.split("tentukan_jatuh_tempo_tagihan(", 1)[1].split(")", 1)[0]
    assert "body.due_date or body.bill_date" not in tulis
    assert 'jt["due_date"], bill_total' in tulis
    assert tulis.index("tentukan_jatuh_tempo_tagihan(") < tulis.index("INSERT INTO bills")
    assert '"due_date_source": jt["due_date_source"]' in tulis


def test_pratinjau_membawa_sumber_dan_teks():
    src = _src(POR.preview_convert_to_bill)
    assert "else tgl" not in src
    for medan in ("due_date_source", "payment_terms_days", "due_date_text"):
        assert f'"{medan}": jt["{medan}"]' in src
    assert '"payload": {"bill_date": tgl.isoformat(), "due_date": jatuh.isoformat()' in src  # commit = pratinjau
