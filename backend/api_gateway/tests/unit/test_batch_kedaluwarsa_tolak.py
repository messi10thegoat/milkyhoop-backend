"""Flag gelap batch_kedaluwarsa_tolak: jual dari batch kedaluwarsa -> 422 BATCH_KEDALUWARSA; flag mati / batch sah = lolos."""
import asyncio
import inspect
import uuid
from datetime import date

import pytest

import app.routers.sales_invoices as SI
from app.services import batch_aksi as BA
from app.services.galat_berkode import GalatBerkode

HARI = date(2026, 11, 26)
B = uuid.uuid4()


class Kon:
    def __init__(self, flag=True, batch=None):
        self.flag, self.batch, self.n = flag, batch, 0

    async def fetchval(self, sql, *a):
        self.n += 1
        assert "tenant_features" in sql and a[1] == "batch_kedaluwarsa_tolak"
        return 1 if self.flag else None

    async def fetchrow(self, sql, *a):
        assert "item_batches" in sql
        return self.batch


def jalan(k, bid=B):
    return asyncio.run(BA.tolak_jual_kedaluwarsa(k, "t", bid, HARI))


def test_flag_mati_lolos_tanpa_baca_batch():
    k = Kon(False, {"batch_number": "L1", "status": "expired", "expiry_date": date(2026, 1, 1)})
    assert jalan(k) is None


def test_tanpa_batch_lolos_tanpa_query():
    k = Kon()
    assert jalan(k, None) is None and k.n == 0


def test_kedaluwarsa_ditolak_422_berkalimat():
    with pytest.raises(GalatBerkode) as e:
        jalan(Kon(True, {"batch_number": "L-77", "status": "active", "expiry_date": date(2026, 11, 25)}))
    assert e.value.status_code == 422 and e.value.code == "BATCH_KEDALUWARSA" and e.value.field == "batch_id"
    assert "L-77" in e.value.detail and "kedaluwarsa" in e.value.detail and "Pilih batch lain" in e.value.detail


def test_status_expired_ditolak_walau_tanpa_tanggal():
    with pytest.raises(GalatBerkode):
        jalan(Kon(True, {"batch_number": "L1", "status": "expired", "expiry_date": None}))


def test_batch_sah_lolos():
    assert jalan(Kon(True, {"batch_number": "L1", "status": "active", "expiry_date": date(2026, 11, 26)})) is None   # hari ini belum lewat
    assert jalan(Kon(True, {"batch_number": "L1", "status": "active", "expiry_date": None})) is None


def test_dipasang_sebelum_cek_kecukupan_di_penjualan():
    src = inspect.getsource(SI)
    a, b = src.index("BA.tolak_jual_kedaluwarsa(conn, tenant_id, batch_id, today)"), src.index("BUG-286: cek kecukupan batch SEBELUM kartu stok ditulis")
    assert a < b
