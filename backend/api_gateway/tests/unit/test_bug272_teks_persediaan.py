"""BUG-272 (16 Nov 2026): teks tampil SERVER aditif untuk Penjualan per Barang (profitability/items) dan Kartu Stok (K9 history). Medan lama utuh; FE menampilkan teks apa adanya."""
import copy
import pathlib

from app.services import teks_laporan_persediaan as T

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def data_pj():
    return {"period": {"start_date": "2026-11-01", "end_date": "2026-11-16", "days": 16},
            "summary": {"total_revenue_gross": "1250000.00", "total_discount": "0.00", "total_revenue_with_tax": "1387500.00", "total_cogs": "1500000.00",
                        "total_gross_profit": "-250000.00", "overall_margin_pct": "-20.00", "total_qty_sold": "1234.50", "unique_items": 3, "unique_invoices": 7, "total_retur": "5000.00"},
            "items": [{"product_id": "a", "item_code": "A-1", "product_name": "Aqua", "category": "", "unit": "pcs", "qty_sold": "56.00", "revenue_gross": "1250000.00",
                       "total_discount": "0.00", "revenue_with_tax": "1387500.00", "cogs": "1500000.00", "gross_profit": "-250000.00", "margin_pct": "-20.00", "invoice_count": 7,
                       "min_price": "9000.00", "max_price": "10500.50", "avg_price": "9750.25", "is_loss_making": True, "has_zero_price": False, "qty_retur": "1.50", "retur_value": "5000.00"},
                      {"product_id": "b", "item_code": "B-1", "product_name": "Beras", "category": "", "unit": "kg", "qty_sold": "1000.00", "revenue_gross": "100.00", "total_discount": "0.00",
                       "revenue_with_tax": "100.00", "cogs": "80.00", "gross_profit": "20.00", "margin_pct": "20.00", "invoice_count": 1, "min_price": "0.00", "max_price": "0.00",
                       "avg_price": "0.00", "is_loss_making": False, "has_zero_price": True, "qty_retur": "0.00", "retur_value": "0.00"}],
            "pagination": {"total": 2, "limit": 50, "offset": 0, "has_more": False},
            "reconciliation": {"matches_pnl": False, "severity": "minor_drift", "drift_amount": "97100.00", "cogs_drift": "0.00", "pnl_cogs_non_jual": "0.00"}}


def test_penjualan_barang_teks_uang_qty_persen_periode_dan_medan_lama_utuh():
    asli = data_pj()
    d = T.tambah_teks_penjualan_barang(copy.deepcopy(asli), 0)
    a, b = d["items"]
    assert (a["qty_sold_text"], a["revenue_gross_text"], a["cogs_text"], a["gross_profit_text"], a["margin_text"]) == ("56 pcs", "1.250.000", "1.500.000", "(250.000)", "-20%")
    assert (a["min_price_text"], a["max_price_text"], a["avg_price_text"], a["invoice_count_text"]) == ("9.000", "10.500,50", "9.750,25", "7 faktur")
    assert (a["qty_retur_text"], a["retur_value_text"], b["qty_sold_text"], b["margin_text"], b["min_price_text"]) == ("1,5 pcs", "5.000", "1.000 kg", "20%", "0")
    s = d["summary"]
    assert (s["total_gross_profit_text"], s["overall_margin_text"], s["total_qty_sold_text"], s["unique_items_text"], s["unique_invoices_text"]) == ("(250.000)", "-20%", "1.234,5", "3 barang", "7 faktur")
    assert d["periode_text"] == "1 Nov – 16 Nov 2026" and d["satuan_text"] == "(Disajikan dalam Rupiah)"
    assert d["reconciliation"]["status_text"] == "Selisih kecil dengan Laba Rugi" and d["reconciliation"]["drift_amount_text"] == "97.100"
    # medan lama TIDAK berubah (aditif): tiap kunci asli bernilai sama
    for k, v in asli["items"][0].items():
        assert d["items"][0][k] == v
    assert all(d["summary"][k] == v for k, v in asli["summary"].items()) and d["pagination"] == asli["pagination"]


def test_presisi_tenant_2_menampilkan_sen_hanya_bila_ada():
    d = T.tambah_teks_penjualan_barang(data_pj(), 2)
    assert d["items"][0]["revenue_gross_text"] == "1.250.000" and d["items"][0]["gross_profit_text"] == "(250.000)"
    d2 = data_pj()
    d2["items"][0]["revenue_gross"] = "1250000.50"
    assert T.tambah_teks_penjualan_barang(d2, 2)["items"][0]["revenue_gross_text"] == "1.250.000,50"


def test_persen_dan_qty_dasar():
    assert (T.persen("23.50"), T.persen("0.00"), T.persen(None), T.persen("-5.25"), T.persen("100.00")) == ("23,5%", "0%", None, "-5,25%", "100%")
    assert (T.qty("172", "pcs"), T.qty("1500.50", "kg"), T.qty("-3", "pcs"), T.qty(None, "pcs"), T.qty("5")) == ("172 pcs", "1.500,5 kg", "-3 pcs", None, "5")


def kartu():
    return {"success": True, "total": 3, "page": 1, "limit": 50, "has_more": False, "saldo_awal": 10, "ringkasan": {"saldo_awal": 10, "total_masuk": 20, "total_keluar": 5, "saldo_akhir": 25},
            "filter": {"warehouse_id": None, "date_from": "2026-11-01", "date_to": "2026-11-16"}, "item": {"id": "i", "code": "A-1", "name": "Aqua"},
            "data": [{"movement_date": "2026-11-03", "quantity_in": 20, "quantity_out": 0, "quantity_balance": 30, "unit_cost": 8333.33, "total_cost": 166666.6, "average_cost": 8333.33},
                     {"movement_date": "2026-11-05", "quantity_in": 0, "quantity_out": 5, "quantity_balance": 25, "unit_cost": 8333.33, "total_cost": 41666.65, "average_cost": None}]}


def test_kartu_stok_teks_baris_ringkasan_periode_dan_medan_lama_utuh():
    asli = kartu()
    k = T.tambah_teks_kartu_stok(copy.deepcopy(asli), "pcs", 0)
    m1, m2 = k["data"]
    assert (m1["tanggal_text"], m1["arah"], m1["quantity_in_text"], m1["quantity_out_text"], m1["perubahan_text"], m1["quantity_balance_text"]) == ("3 Nov 2026", "masuk", "20 pcs", None, "+20 pcs", "30 pcs")
    assert (m2["arah"], m2["quantity_in_text"], m2["quantity_out_text"], m2["perubahan_text"], m2["average_cost_text"]) == ("keluar", None, "5 pcs", "-5 pcs", None)
    assert (m1["unit_cost_text"], m1["total_cost_text"], m1["average_cost_text"]) == ("8.333,33", "166.667", "8.333,33")   # HPP satuan 2dp; total presisi tenant
    assert (k["saldo_awal_text"], k["periode_text"], k["item"]["satuan"]) == ("10 pcs", "1 Nov – 16 Nov 2026", "pcs")
    assert (k["ringkasan"]["total_masuk_text"], k["ringkasan"]["total_keluar_text"], k["ringkasan"]["saldo_akhir_text"]) == ("20 pcs", "5 pcs", "25 pcs")
    for kk, v in asli["data"][0].items():
        assert m1[kk] == v
    assert k["saldo_awal"] == 10 and k["total"] == 3 and k["ringkasan"]["total_masuk"] == 20


def test_kartu_stok_tanpa_filter_tanpa_ringkasan_dan_periode_sebagian():
    k = kartu()
    k["ringkasan"], k["filter"] = None, {"warehouse_id": None, "date_from": None, "date_to": None}
    assert T.tambah_teks_kartu_stok(k, "pcs", 0)["periode_text"] is None
    k2 = kartu()
    k2["filter"]["date_to"] = None
    assert T.tambah_teks_kartu_stok(k2, "pcs", 0)["periode_text"] == "Sejak 1 Nov 2026"


def test_rute_memakai_teks_dan_k9_membaca_satuan_barang():
    pj = (APP / "routers" / "reports_profitability.py").read_text()
    assert "TLP.tambah_teks_penjualan_barang({" in pj and "}, presisi)" in pj and "presisi = await presisi_tenant(conn, tenant_id)" in pj
    it = (APP / "routers" / "items.py").read_text()
    assert "TLP.tambah_teks_kartu_stok({" in it and 'item["satuan"], await presisi_tenant(conn, tenant_id))' in it
    assert "COALESCE(NULLIF(base_unit, ''), satuan) AS satuan FROM products WHERE id = $1 AND tenant_id = $2" in it
