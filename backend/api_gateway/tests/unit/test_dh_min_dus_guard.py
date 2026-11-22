"""Celah 339 (QUO-2611-0004): penjaga harga_minimum.hitung memakai SATU penentu dgn resolver untuk satuan TURUNAN tanpa baris DH sendiri:
lantai = minimum baris DH satuan dasar x faktor (harga_jual._dari_daftar_dasar + minimum_turunan); tingkat qty pada qty dasar; baris satuan eksplisit menang."""
import asyncio
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

import pytest

from app.services import harga_jual as HJ
from app.services import harga_minimum as HMN
from app.services import satuan_dasar as SD

TID, PID, DAFTAR, TGL = "t-1", uuid4(), uuid4(), date(2026, 11, 22)
FAKTOR = D("24")


def baris(unit, harga, minimum, qty=1):
    return {"item_id": PID, "unit": unit, "price": D(harga), "min_price": D(minimum) if minimum is not None else None, "discount_percent": None,
            "min_quantity": D(qty), "start_date": None, "end_date": None}


class Konn:
    def __init__(self, rows):
        self.rows = rows
        self.n_muat = 0

    async def fetch(self, sql, *a):
        if "FROM price_list_items" in sql:
            return self.rows
        if "FROM products" in sql:
            return [{"id": PID, "nama_produk": "Minyak", "dasar": "Pcs"}]
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    async def alias(conn, tid):
        return {}

    async def daftar(conn, tid, c, t):
        return {"id": DAFTAR, "code": "D", "name": "D", "asal": "pelanggan"}

    async def muat(conn, tid, ids):
        conn.n_muat += 1
        return {"alias": {}}

    def hitung_sd(ktx, pid, unit, q, mode, aksi="Pengiriman"):
        return {"faktor": FAKTOR if unit == "Dus" else D(1), "blok": None}
    monkeypatch.setattr(SD, "_alias", alias)
    monkeypatch.setattr(HJ, "daftar_untuk", daftar)
    monkeypatch.setattr(SD, "muat", muat)
    monkeypatch.setattr(SD, "hitung", hitung_sd)


def jalan(rows, nilai, qty=1, unit="Dus"):
    k = Konn(rows)
    out = asyncio.run(HMN.hitung(k, TID, "c-1", TGL, [{"baris": 1, "item_id": PID, "quantity": qty, "unit": unit, "nilai": nilai, "unit_price": nilai}]))
    return k, out


DASAR = [baris(None, 10000, 9000, qty=1), baris(None, 9500, 9000, qty=120)]      # DH hanya baris dasar (Pcs): minimum dasar 9.000 -> Dus 216.000


def test_dus_turunan_di_bawah_minimum_dasar_x_faktor_peringatan():
    _, r = jalan(DASAR, D("200000"))
    assert len(r) == 1 and r[0]["code"] == "JUAL_DI_BAWAH_MINIMUM" and r[0]["butuh_konfirmasi"] is True
    assert r[0]["harga_minimum"] == 216000.0 and r[0]["selisih"] == 16000.0 and r[0]["satuan"] == "Dus" and "Rp 216.000" in r[0]["pesan"]


def test_dus_turunan_tepat_minimum_atau_di_atasnya_aman():
    assert jalan(DASAR, D("216000"))[1] == [] and jalan(DASAR, D("230000"))[1] == []


def test_baris_dus_eksplisit_menang_dan_tanpa_minimum_berarti_tanpa_lantai():
    dus_min = DASAR + [baris("Dus", 230000, 220000)]
    assert len(jalan(dus_min, D("218000"))[1]) == 1 and jalan(dus_min, D("218000"))[1][0]["harga_minimum"] == 220000.0   # lantai baris Dus, bukan 216.000
    dus_tanpa_min = DASAR + [baris("Dus", 230000, None)]
    assert jalan(dus_tanpa_min, D("100"))[1] == []                                                                        # asumsi 339: Dus eksplisit tanpa minimum = tanpa lantai


def test_tingkat_qty_pada_qty_dasar_5_dus_x_24_sama_120():
    # tingkat dasar >=120 punya minimum 8.000; 4 Dus (96 dasar) -> tingkat 1 (9.000 -> 216.000/Dus); 5 Dus (120 dasar) -> tingkat 120 (8.000 -> 192.000/Dus)
    rows = [baris(None, 10000, 9000, qty=1), baris(None, 9500, 8000, qty=120)]
    r4 = jalan(rows, D("800000"), qty=4)[1]            # 200.000/Dus < 216.000
    assert len(r4) == 1 and r4[0]["harga_minimum"] == 216000.0
    assert jalan(rows, D("1000000"), qty=5)[1] == []   # 200.000/Dus >= 192.000
    r5 = jalan(rows, D("900000"), qty=5)[1]            # 180.000/Dus < 192.000
    assert len(r5) == 1 and r5[0]["harga_minimum"] == 192000.0


def test_satuan_dasar_tak_berubah_dan_tanpa_minimum_tak_ada_peringatan():
    k, r = jalan(DASAR, D("8000"), unit="Pcs")
    assert len(r) == 1 and r[0]["harga_minimum"] == 9000.0 and k.n_muat == 0           # dasar: jalur lama, tanpa SD.muat
    assert jalan([baris(None, 10000, None)], D("1"))[1] == []                          # DH tanpa minimum sama sekali


def test_satu_batch_muat_per_hitung_bukan_per_baris():
    k = Konn(DASAR)
    ls = [{"baris": n, "item_id": PID, "quantity": 1, "unit": "Dus", "nilai": D("200000"), "unit_price": D("200000")} for n in (1, 2, 3)]
    out = asyncio.run(HMN.hitung(k, TID, "c-1", TGL, ls))
    assert len(out) == 3 and k.n_muat == 1


def test_konversi_so_ke_faktur_harga_sama_tak_mengulang():
    k = Konn(DASAR)
    out = asyncio.run(HMN.hitung(k, TID, "c-1", TGL, [{"baris": 1, "item_id": PID, "quantity": 1, "unit": "Dus", "nilai": D("200000"), "unit_price": D("200000")}],
                                 harga_asal={str(PID): D("200000")}))
    assert out == []


def test_penentu_sama_resolver_dan_penjaga_minimum_turunan():
    konv = {"min_price": D("9000")}
    assert HJ.minimum_turunan(konv, {"faktor": FAKTOR}) == D("216000.00") and HJ.minimum_turunan({"min_price": None}, {"faktor": FAKTOR}) is None
