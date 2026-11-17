"""BE-B (17 Nov 2026): BUG-108 kartu stok satuan besar campuran (*_campur_text) + BUG-221 riwayat Penyesuaian Stok tanpa kata teknis ("diposting", "(void)")."""
import copy
import inspect
from decimal import Decimal as D

from app.routers import items as IT
from app.services import stok_sesuai as SS
from app.services import teks_laporan_persediaan as T

KONV = [("Dus", D("12")), ("Karton", D("120"))]


def test_campur_serakah_dari_satuan_terbesar():
    assert T.campur(D("172"), "Pcs", KONV) == "1 Karton 4 Dus 4 Pcs"    # 172 = 120 + 4x12 + 4
    assert T.campur(D("50"), "Pcs", KONV) == "4 Dus 2 Pcs"
    assert T.campur(D("24"), "Pcs", KONV) == "2 Dus"                    # tanpa sisa: tanpa buntut
    assert T.campur(D("-12"), "Pcs", KONV) == "-1 Dus"
    assert T.campur(D("-130"), "Pcs", KONV) == "-1 Karton 10 Pcs"


def test_campur_none_bila_tak_berlaku():
    assert T.campur(D("5"), "Pcs", KONV) is None                        # tak muat satuan besar -> FE pakai teks satuan dasar
    assert T.campur(D("172"), "Pcs", None) is None and T.campur(D("172"), "Pcs", []) is None
    assert T.campur(None, "Pcs", KONV) is None
    assert T.campur(D("172"), "Pcs", [("Dus", D("1")), ("X", D("0"))]) is None   # faktor <= 1 diabaikan


def test_campur_pecahan_sisa():
    assert T.campur(D("12.5"), "Pcs", KONV) == "1 Dus 0,5 Pcs"


def kartu():
    return {"success": True, "total": 2, "page": 1, "limit": 50, "has_more": False, "saldo_awal": 100, "ringkasan": {"saldo_awal": 100, "total_masuk": 120, "total_keluar": 12, "saldo_akhir": 208},
            "filter": {"warehouse_id": None, "date_from": "2026-11-01", "date_to": "2026-11-16"}, "item": {"id": "i", "code": "A", "name": "Minyak"},
            "data": [{"movement_date": "2026-11-03", "quantity_in": 120, "quantity_out": 0, "quantity_balance": 220, "unit_cost": 1, "total_cost": 120, "average_cost": 1},
                     {"movement_date": "2026-11-05", "quantity_in": 0, "quantity_out": 12, "quantity_balance": 208, "unit_cost": 1, "total_cost": 12, "average_cost": None}]}


def test_kartu_stok_memuat_medan_campur_aditif_dan_medan_lama_utuh():
    asli = kartu()
    k = T.tambah_teks_kartu_stok(copy.deepcopy(asli), "Pcs", 0, KONV)
    m1, m2 = k["data"]
    assert (m1["quantity_in_campur_text"], m1["quantity_out_campur_text"], m1["quantity_balance_campur_text"]) == ("1 Karton", None, "1 Karton 8 Dus 4 Pcs")
    assert (m2["quantity_out_campur_text"], m2["quantity_balance_campur_text"]) == ("1 Dus", "1 Karton 7 Dus 4 Pcs")
    assert (k["saldo_awal_campur_text"], k["ringkasan"]["saldo_akhir_campur_text"]) == ("8 Dus 4 Pcs", "1 Karton 7 Dus 4 Pcs")
    assert m1["quantity_balance_text"] == "220 Pcs" and k["saldo_awal_text"] == "100 Pcs"       # teks lama tetap
    tanpa = T.tambah_teks_kartu_stok(copy.deepcopy(asli), "Pcs", 0)                             # barang tanpa konversi
    assert tanpa["data"][0]["quantity_balance_campur_text"] is None and tanpa["saldo_awal_campur_text"] is None


def test_history_membaca_konversi_aktif_barang_ber_tenant():
    src = inspect.getsource(IT.get_item_history)
    assert "FROM unit_conversions WHERE tenant_id = $1 AND product_id = $2" in src and "conversion_factor > 1" in src and ", konv)" in src


def test_riwayat_penyesuaian_tanpa_kata_teknis():
    src = inspect.getsource(SS.riwayat)
    assert "diterbitkan" in src and "dibatalkan{alasan}" in src
    assert "diposting" not in src.replace("PENYESUAIAN_DIPOSTING", "") and "(void)" not in src
