"""BUG-040 (b): peringatan JUAL_DI_BAWAH_MINIMUM di Penawaran/SO/Faktur (putusan pemilik via MASTER 16 Nov, bukti Accurate PT BIS): peringatan konfirmasi
(BUKAN blok), konversi SO->Faktur tak mengulang bila harga baris tak berubah, pesan menyebut minimum + selisih dari server."""
import asyncio
import inspect
import pathlib
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services import harga_jual as HJ
from app.services import harga_minimum as HMN
from app.services import satuan_dasar as SD

GW = pathlib.Path(__file__).resolve().parents[2]
TID, PID, DAFTAR, TGL = "t-1", uuid4(), uuid4(), date(2026, 11, 20)


def baris(harga_min, unit=None, qty=1, mulai=None, akhir=None, harga=10000):
    return {"item_id": PID, "unit": unit, "price": Decimal(harga), "min_price": Decimal(harga_min) if harga_min is not None else None,
            "min_quantity": Decimal(qty), "start_date": mulai, "end_date": akhir}


class Konn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM price_list_items" in sql:
            return self.rows
        if "FROM products" in sql:
            return [{"id": PID, "nama_produk": "Minyak", "dasar": "Pcs"}]
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    async def alias(conn, tid):
        return {}

    async def daftar(conn, tid, customer_id, tanggal):
        return {"id": DAFTAR, "code": "D", "name": "D", "asal": "pelanggan"}
    monkeypatch.setattr(SD, "_alias", alias)
    monkeypatch.setattr(HJ, "daftar_untuk", daftar)


def hitung(rows, lines, asal=None, mode=None):
    return asyncio.run(HMN.hitung(Konn(rows), TID, "c-1", TGL, lines, asal, mode))


def baris_dok(nilai, qty=1, unit=None, unit_price=None):
    return {"baris": 1, "item_id": PID, "quantity": qty, "unit": unit, "nilai": nilai, "unit_price": unit_price if unit_price is not None else nilai}


def test_di_bawah_minimum_peringatan_konfirmasi_dengan_minimum_dan_selisih():
    r = hitung([baris(9500)], [baris_dok(9000)])
    assert len(r) == 1 and r[0]["code"] == "JUAL_DI_BAWAH_MINIMUM" and r[0]["butuh_konfirmasi"] is True and r[0]["blok"] is False
    assert "Rp 9.000" in r[0]["pesan"] and "Rp 9.500" in r[0]["pesan"] and "selisih Rp 500" in r[0]["pesan"] and r[0]["pesan"] == r[0]["message"]
    assert r[0]["harga_minimum"] == 9500.0 and r[0]["selisih"] == 500.0 and r[0]["harga"] == 9000.0 and r[0]["product_id"] == str(PID) and r[0]["baris"] == 1


def test_sama_dengan_minimum_atau_di_atasnya_aman():
    assert hitung([baris(9500)], [baris_dok(9500)]) == [] and hitung([baris(9500)], [baris_dok(12000)]) == []


def test_harga_satuan_efektif_nilai_dibagi_qty_setelah_diskon():
    assert hitung([baris(9500)], [baris_dok(nilai=Decimal("47000"), qty=5)]) != []       # 47.000/5 = 9.400 < 9.500
    assert hitung([baris(9500)], [baris_dok(nilai=Decimal("48000"), qty=5)]) == []


def test_tanpa_lantai_tanpa_daftar_atau_mode_mati_tak_ada_peringatan(monkeypatch):
    assert hitung([baris(None)], [baris_dok(1)]) == []
    assert hitung([baris(9500)], [baris_dok(1)], mode="mati") == []

    async def tanpa(conn, tid, c, t):
        return None
    monkeypatch.setattr(HJ, "daftar_untuk", tanpa)
    assert hitung([baris(9500)], [baris_dok(1)]) == []


def test_baris_yang_menang_menentukan_lantai_tingkat_qty_dan_berjangka():
    rows = [baris(9500, qty=1), baris(8000, qty=10), baris(7000, qty=10, mulai=date(2026, 11, 25)), baris(9900, qty=1, mulai=date(2026, 11, 1))]
    assert hitung(rows, [baris_dok(8500, qty=1)]) != []                                   # qty 1: mulai terbaru <= tgl = 9.900 (bukan 9.500)
    assert hitung(rows, [baris_dok(nilai=Decimal("85000"), qty=10)]) == []                # qty 10: lantai 8.000 (baris 7.000 belum mulai) -> 8.500 aman
    assert hitung(rows, [baris_dok(nilai=Decimal("75000"), qty=10)]) != []                # 7.500 < 8.000


def test_satuan_beda_dengan_baris_daftar_dilewati_tanpa_menebak():
    assert hitung([baris(9500, unit="Dus")], [baris_dok(100, unit=None)]) == []
    assert hitung([baris(9500, unit="Pcs")], [baris_dok(9000, unit=None)]) != []           # satuan kosong == satuan dasar Pcs


def test_baris_kedaluwarsa_atau_belum_berlaku_tak_dihitung():
    assert hitung([baris(9500, akhir=date(2026, 11, 1))], [baris_dok(100)]) == []
    assert hitung([baris(9500, mulai=date(2026, 12, 1))], [baris_dok(100)]) == []


def test_konversi_so_ke_faktur_tak_mengulang_bila_harga_baris_tak_berubah_diulang_bila_berubah():
    rows = [baris(9500)]
    asal = {str(PID): Decimal("9000")}
    assert hitung(rows, [baris_dok(9000, unit_price=Decimal("9000"))], asal) == []           # harga sama dengan SO -> diam
    assert hitung(rows, [baris_dok(8800, unit_price=Decimal("8800"))], asal) != []           # harga diubah di faktur -> peringatan


def test_qty_nol_atau_tanpa_barang_atau_tanpa_nilai_dilewati():
    assert hitung([baris(9500)], [baris_dok(100, qty=0)]) == []
    assert hitung([baris(9500)], [{**baris_dok(100), "item_id": None}]) == []
    assert hitung([baris(9500)], [{**baris_dok(100), "nilai": None}]) == []


def test_jalan_pintas_menelan_galat_dan_tak_membuka_transaksi_sendiri():
    class Rusak:
        async def fetch(self, *a):
            raise RuntimeError("galat baca")
    assert asyncio.run(HMN.peringatan_jual(Rusak(), TID, "c", TGL, [baris_dok(1)])) == []          # galat = isyarat hilang, bukan kalkulasi rusak
    assert asyncio.run(HMN.peringatan_tersimpan(Rusak(), TID, "so", "x")) == []
    assert "conn.transaction()" not in inspect.getsource(HMN)                                       # pola jual_bawah_hpp: ikut transaksi pemanggil (pratinjau READ ONLY)


def test_baris_dari_hasil_membawa_harga_satuan_asal():
    r = HMN.baris_dari_hasil([{"item_id": "x", "quantity": 2, "unit": "Pcs", "dpp_harga_jual": Decimal(100), "unit_price": 60}])
    assert r == [{"baris": 1, "item_id": "x", "quantity": 2, "unit": "Pcs", "nilai": Decimal(100), "unit_price": 60}]


def test_terpasang_di_tiga_dokumen_pada_titik_yang_sama_dengan_jual_di_bawah_hpp():
    r = GW / "app/routers"
    so, qt, inv = (r / "sales_orders.py").read_text(), (r / "quotes.py").read_text(), (r / "sales_invoices.py").read_text()
    assert "HMN.peringatan_jual(conn, ctx[\"tenant_id\"], body.customer_id, body.order_date" in so and 'HMN.peringatan_tersimpan(conn, ctx["tenant_id"], "so", order_id)' in so
    assert "HMN.peringatan_jual(conn, ctx[\"tenant_id\"], body.customer_id, body.quote_date" in qt and '"penawaran", quote_id' in qt
    assert 'body.customer_id, body.invoice_date' in inv and 'HMN.harga_asal_so(_conn, ctx["tenant_id"], getattr(body, "sales_order_id", None))' in inv
    assert 'HMN.peringatan_tersimpan(conn, ctx["tenant_id"], "faktur", invoice_id)' in inv


def test_tersimpan_memakai_kalkulator_bersama_dan_saring_tenant():
    s = inspect.getsource(HMN._tersimpan)
    assert "compute_document" in s and "tenant_id = $2" in s and "JENIS_TERSIMPAN" in s
    assert HMN.JENIS_TERSIMPAN["faktur"][2] == "sales_order_id" and HMN.JENIS_TERSIMPAN["so"][2] is None


# ---------------------------------------------------------------- BUG-273 kontrak peringatan dokumen: `pesan` DAN `message` (kalimat sama)
def test_peringatan_dokumen_mengirim_pesan_dan_message_sama_untuk_hpp_terima_dan_minimum():
    from app.services import jual_bawah_hpp as JB
    mentah = [{"kode": JB.KODE_JUAL, "baris": 1, "product_id": "p", "nama": "Minyak", "satuan": "Pcs", "harga": Decimal(90), "hpp": Decimal(100), "selisih": Decimal(10)},
              {"kode": JB.KODE_TERIMA, "baris": 2, "product_id": "q", "nama": "Beras", "satuan": "Kg", "harga": Decimal(50), "hpp": Decimal(100), "selisih": Decimal(50), "murah": True}]
    for boleh in (True, False):                                           # dengan dan tanpa izin lihat HPP: kunci SAMA
        for o in JB.sajikan(mentah, boleh):
            assert o["pesan"] and o["message"] == o["pesan"] and o["butuh_konfirmasi"] is True and o["code"] in (JB.KODE_JUAL, JB.KODE_TERIMA)
    m = HMN.periksa(9000, 10000, "Minyak")
    assert m["pesan"] and m["message"] == m["pesan"]                      # JUAL_DI_BAWAH_MINIMUM sudah dari awal
