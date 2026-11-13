"""BUG-207 (putusan pemilik 13 Nov): peringatan konfirmasi jual di bawah HPP + biaya terima janggal; angka hanya bagi berizin REPORT R."""
import asyncio
import inspect
from decimal import Decimal as D

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import purchase_orders as PO
from app.routers import quotes as QU
from app.routers import sales_invoices as SI
from app.routers import sales_orders as SOR
from app.schemas.sales_invoices import InvoiceCalculation
from app.services import jual_bawah_hpp as JB

P1, P2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
KUTUS = {"nama": "Minyak Kutus", "dasar": "liter", "konversi": {"karton": D("60")}}
KTX = {"alias": {}, "barang": {P1: KUTUS, P2: {"nama": "Kaos", "dasar": "pcs", "konversi": {}}}}


class Kon:
    def __init__(self, hpp=None):
        self.hpp = {P1: D("10000"), P2: D("50000")} if hpp is None else hpp

    async def fetch(self, sql, *a):
        assert "FROM products p" in sql and a[0] == "T"
        return [{"id": i, "nama": KTX["barang"][i]["nama"], "hpp": h} for i, h in self.hpp.items() if i in a[1]]


@pytest.fixture(autouse=True)
def _ktx(monkeypatch):
    async def muat(conn, tid, ids):
        return KTX
    monkeypatch.setattr(JB.SD, "muat", muat)


def jalan(aw):
    return asyncio.run(aw)


def jual(baris, hpp=None):
    return jalan(JB.hitung_jual(Kon(hpp), "T", baris))


def terima(baris, hpp=None):
    return jalan(JB.hitung_terima(Kon(hpp), "T", baris))


def test_jual_bawah_hpp_dihitung_per_satuan_dasar_bukan_per_satuan_baris():
    # 1 karton = 60 L, HPP 10.000/L. 375.000/karton = 6.250/L (< HPP) -> peringatan; 700.000/karton = 11.667/L -> tidak
    m = jual([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("375000")}])
    assert len(m) == 1 and m[0]["kode"] == JB.KODE_JUAL and m[0]["harga"] == D("6250") and m[0]["hpp"] == D("10000")
    assert m[0]["satuan"] == "liter" and m[0]["selisih"] == D("37.5")
    assert jual([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("700000")}]) == []
    assert jual([{"item_id": P1, "quantity": D("2"), "unit": "liter", "nilai": D("19999")}])[0]["harga"] == D("9999.5")   # satuan dasar apa adanya
    assert jual([{"item_id": P1, "quantity": D("2"), "unit": "liter", "nilai": D("20000")}]) == []                       # tepat HPP = tidak rugi


def test_jual_harga_nol_hadiah_tetap_diperingatkan_dan_hpp_nol_atau_bukan_stok_dilewati():
    assert len(jual([{"item_id": P2, "quantity": D("1"), "unit": "pcs", "nilai": D("0")}])) == 1                          # hadiah: konfirmasi sengaja
    assert jual([{"item_id": P1, "quantity": D("1"), "unit": "liter", "nilai": D("1")}], hpp={P1: D("0")}) == []          # HPP 0: tak menebak
    assert jual([{"item_id": P1, "quantity": D("1"), "unit": "liter", "nilai": D("1")}], hpp={}) == []                    # bukan goods ber-stok (tak ada di hasil query)
    assert jual([{"item_id": None, "quantity": D("1"), "unit": "pcs", "nilai": D("1")}]) == []                            # baris tanpa barang
    assert jual([{"item_id": P1, "quantity": D("1"), "unit": "ember", "nilai": D("1")}]) == []                            # satuan tak terkonversi: penjaga satuan yang menolak


def test_terima_janggal_ambang_30_persen_tepat_dua_arah():
    def t(nilai):
        return terima([{"item_id": P2, "quantity": D("1"), "unit": "pcs", "nilai": D(nilai)}])   # HPP 50.000
    assert t("35000") == [] and t("65000") == []                          # tepat 30%: tidak
    murah, mahal = t("34999"), t("65001")
    assert len(murah) == 1 and murah[0]["murah"] is True and len(mahal) == 1 and mahal[0]["murah"] is False
    assert murah[0]["kode"] == JB.KODE_TERIMA


def test_terima_kasus_kutus_salah_konversi_tertangkap_dan_hpp_nol_dilewati():
    m = terima([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("375000")}])    # 6.250/L vs 10.000/L = 37,5% murah
    assert len(m) == 1 and m[0]["harga"] == D("6250")
    assert terima([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("375000")}], hpp={P1: D("0")}) == []


def test_sajikan_berizin_memuat_angka_tak_berizin_tanpa_angka_dan_kunci_absen():
    m = jual([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("375000")}])
    ada = JB.sajikan(m, True)[0]
    assert ada["code"] == "JUAL_DI_BAWAH_HPP" and ada["butuh_konfirmasi"] is True and ada["baris"] == 1
    assert ada["harga_jual"] == "6250.00" and ada["hpp"] == "10000.00" and ada["selisih_pct"] == "37.50" and "Rp 6.250" in ada["pesan"] and "Rp 10.000" in ada["pesan"]
    tak = JB.sajikan(m, False)[0]
    assert set(tak) == {"code", "butuh_konfirmasi", "baris", "product_id", "nama", "pesan"}                              # kunci angka ABSEN (bukan null)
    assert "Rp" not in tak["pesan"] and not any(c.isdigit() for c in tak["pesan"]) and tak["butuh_konfirmasi"] is True
    t2 = JB.sajikan(terima([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("375000")}]), False)[0]
    assert set(t2) == {"code", "butuh_konfirmasi", "baris", "product_id", "nama", "pesan"} and not any(c.isdigit() for c in t2["pesan"])
    t3 = JB.sajikan(terima([{"item_id": P1, "quantity": D("1"), "unit": "karton", "nilai": D("375000")}]), True)[0]
    assert t3["code"] == "BIAYA_TERIMA_JANGGAL" and t3["biaya"] == "6250.00" and "periksa satuan" in t3["pesan"]


def test_izin_lihat_hpp_adalah_baca_laporan_penentu_rute_yang_sama(monkeypatch):
    assert izin_untuk_jalur(JB.JALUR_IZIN_HPP, "GET") == ("reports", "R")                                              # REPORT R, bukan modul lain
    dipanggil = []

    async def izin(user, jalur, metode="GET"):
        dipanggil.append((user["user_id"], tuple(jalur), metode))
        return {jalur[0]: user["user_id"] == "boleh"}
    monkeypatch.setattr("app.middleware.permission_middleware.izin_rute_pengguna", izin)
    assert jalan(JB.boleh_lihat_hpp({"user_id": "boleh", "tenant_id": "T"})) is True
    assert jalan(JB.boleh_lihat_hpp({"user_id": "tidak", "tenant_id": "T"})) is False
    assert jalan(JB.boleh_lihat_hpp(None)) is False and len(dipanggil) == 2                                              # tanpa pengguna: tak memanggil, TIDAK


def test_peringatan_jual_gagal_hitung_tak_merusak_kalkulasi(monkeypatch):
    async def rusak(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(JB, "hitung_jual", rusak)
    assert jalan(JB.peringatan_jual(Kon(), "T", {"user_id": "u", "tenant_id": "T"}, [{"item_id": P1}])) == []


def test_baris_dari_hasil_memakai_dpp_harga_jual():
    assert JB.baris_dari_hasil([{"item_id": P1, "quantity": 2, "unit": "liter", "dpp_harga_jual": D("19000"), "unit_price": D("99999")}]) == \
        [{"item_id": P1, "quantity": 2, "unit": "liter", "nilai": D("19000")}]                                           # sesudah diskon baris + dokumen, bukan harga satuan


def test_peringatan_terima_dari_rencana_nilai_proporsional_qty_diterima(monkeypatch):
    async def izin(*a, **k):
        return True
    monkeypatch.setattr(JB, "boleh_lihat_hpp", izin)
    rencana = {"semua": [{"id": "a", "item_id": P2, "quantity": D("10"), "unit": "pcs", "subtotal": D("1000000"), "jasa": False},
                         {"id": "b", "item_id": P2, "quantity": D("10"), "unit": "pcs", "subtotal": D("100000"), "jasa": True},
                         {"id": "c", "item_id": P1, "quantity": D("1"), "unit": "karton", "subtotal": D("375000"), "jasa": False}],
               "akan": {"a": D("4"), "b": D("5"), "c": D("0")}}
    out = jalan(JB.peringatan_terima(Kon(), "T", {"user_id": "u"}, rencana))
    # a: 4 pcs dari subtotal 1.000.000/10 = 100.000/pcs vs HPP 50.000 = +100% -> peringatan baris 1; b jasa dilewati; c tak diterima (akan 0)
    assert [(o["code"], o["baris"]) for o in out] == [("BIAYA_TERIMA_JANGGAL", 1)] and out[0]["biaya"] == "100000.00"


def test_titik_pasang_calculate_tiga_dokumen_dan_skema_faktur():
    for fn, rujuk in ((SI.calculate_invoice, 'res["items"]'), (SOR.calculate_sales_order, 'doc["items"]'), (QU.calculate_quote, '_doc["items"]')):
        src = inspect.getsource(fn)
        assert "_peringatan = await JB.peringatan_jual(" in src and f"JB.baris_dari_hasil({rujuk})" in src and "request.state.user" in src
    assert '"peringatan": _peringatan,' in inspect.getsource(SI.calculate_invoice) and '"peringatan": _peringatan,' in inspect.getsource(SOR.calculate_sales_order)
    assert '"peringatan": _peringatan,' in inspect.getsource(QU.calculate_quote)
    assert InvoiceCalculation.model_fields["peringatan"].default == []                                                  # skema Faktur tak menyaring kunci baru


def test_titik_pasang_terima_pratinjau_dan_tulis_sebelum_stok_masuk():
    pra = inspect.getsource(PO.preview_receive_goods)
    assert '"peringatan": [] if r["blocks"] else await JB.peringatan_terima(' in pra
    tulis = inspect.getsource(PO.receive_goods)
    assert "_peringatan_terima = await JB.peringatan_terima(" in tulis and '"peringatan": _peringatan_terima,' in tulis
    assert tulis.index("_peringatan_terima = await JB.peringatan_terima(") < tulis.index("UPDATE purchase_order_items")   # HPP pembanding = SEBELUM penerimaan ini
