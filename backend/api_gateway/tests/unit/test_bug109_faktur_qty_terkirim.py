"""BUG-109 (pola NetSuite 'bill fulfilled'): faktur dari SO bawaan = qty TERKIRIM lewat Surat Jalan yang belum ditagih (bukan qty pesan);
tanpa SJ = sisa belum ditagih (alur faktur-dulu). Draf: teks posisi menyebut SJ yang ditautkan saat terbit (penentu SAMA, tanpa tulis)."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D

from app.routers import sales_orders as RSO
from app.services import sj_so as SJ

SOI = uuid.uuid4()


def test_tiga_kasus_bawaan():
    assert RSO.qty_tagih_bawaan(D("5"), D("3")) == D("3")   # SO 5, kirim 3 belum ditagih -> 3 (dulu 5)
    assert RSO.qty_tagih_bawaan(D("5"), D("0")) == D("5")   # tanpa SJ -> sisa (faktur-dulu)
    assert RSO.qty_tagih_bawaan(D("2"), D("0")) == D("2")   # kirim 3 sudah ditagih 3 -> sisa 2
    assert RSO.qty_tagih_bawaan(D("2"), D("4")) == D("2")   # dibatasi sisa


class _K:
    def __init__(self, s, alok, draf):
        self.s, self.alok, self.draf = s, alok, draf

    async def fetch(self, sql, *a):
        if "invoice_fulfillments" in sql:
            return [{"soi_id": SOI, "s": self.s, "alok": self.alok}]
        if "si.status = 'draft'" in sql:
            return [{"soi_id": SOI, "q": self.draf}] if self.draf else []
        return []


def test_terkirim_belum_ditagih_kurangi_terkait_dan_draf():
    f = RSO._terkirim_belum_ditagih
    assert asyncio.run(f(_K(D("3"), D("0"), None), "t", uuid.uuid4())) == {SOI: D("3")}
    assert asyncio.run(f(_K(D("3"), D("3"), None), "t", uuid.uuid4())) == {}        # sudah ditautkan faktur terbit
    assert asyncio.run(f(_K(D("3"), D("0"), D("3")), "t", uuid.uuid4())) == {}       # sudah ada di faktur DRAF


def test_bawaan_memakai_penentu_terkirim():
    src = inspect.getsource(RSO._buat_faktur_dari_so)
    assert "_terkirim_belum_ditagih(" in src and "qty_tagih_bawaan(" in src


def test_teks_draf_sj_akan_ditautkan(monkeypatch):
    async def rencana(conn, tid, inv, kunci=True):
        assert kunci is False   # draf: TANPA kunci/tulis
        return {}, [({}, [("i1", D("3"), D("0"), "SJ-2611-0013")], D("3"), D("0"), D("0"))]
    monkeypatch.setattr(SJ, "_rencana_alokasi", rencana)
    assert asyncio.run(SJ.teks_sj_akan_ditautkan(None, "t", "inv")) == "Surat Jalan SJ-2611-0013 (3) ditautkan saat terbit"


def test_posting_memakai_penentu_sama_dengan_kunci():
    assert "_rencana_alokasi(conn, tenant_id, invoice_id, kunci=True)" in inspect.getsource(SJ.alokasikan_saat_faktur)
