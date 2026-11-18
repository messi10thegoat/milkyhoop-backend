"""BUG-123: Penyesuaian Stok -- jumlah baris bersatuan besar ("14 Karton"): server mengonversi ke satuan dasar (satuan_dasar, seperti Pemindahan K1), satuan tanpa konversi = blok berkalimat + field,
pratinjau/draf mengembalikan baris yang sudah dikonversi + teks campur. FE tak menghitung. Paritas nyata = uji salinan."""
import asyncio
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

from app.schemas.stock_adjustments import StockAdjustmentItemCreate, StockAdjustmentItemUpdate
from app.services import stok_sesuai as SS
from app.services.bayar_keluar_rencana import Blok
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
G = uuid.uuid4()
A = uuid.uuid4()


SQL_FETCH: list = []


def jalan(aw):
    return asyncio.run(aw)


class Kon:
    """Barang A (Minyak): dasar Pcs, 1 Karton = 12 Pcs, 1 Dus = 6 Pcs; stok gudang 172."""
    async def fetchrow(self, sql, *a):
        if "FROM products p" in sql:
            return {"id": a[0], "name": "Minyak", "unit": "Pcs", "code": "M", "purchase_price": 1000}
        if "SELECT item_type, track_inventory FROM products" in sql:
            return {"item_type": "goods", "track_inventory": True}
        return None

    async def fetchval(self, sql, *a):
        return D("172") if "warehouse_id = $3" in sql or "average_cost" not in sql else 1000

    async def fetch(self, sql, *a):
        SQL_FETCH.append(sql)
        if "FROM product_units" in sql:
            return [{"name": "Karton", "abbreviation": "ktn"}, {"name": "Pcs", "abbreviation": "pcs"}]
        if "nama_produk, COALESCE" in sql:
            return [{"id": A, "nama_produk": "Minyak", "dasar": "Pcs"}]
        if "FROM unit_conversions" in sql and "base_unit" in sql:
            return [{"product_id": A, "base_unit": "Pcs", "conversion_unit": "Karton", "conversion_factor": D("12")}]
        if "FROM unit_conversions" in sql:
            return [{"product_id": A, "conversion_unit": "Karton", "conversion_factor": D("12")}]
        return []


def baris(qty, unit=None, fisik=None):
    return SimpleNamespace(product_id=str(A), quantity_adjustment=D(str(qty)), physical_quantity=None if fisik is None else D(str(fisik)), reason_detail=None, unit=unit,
                           warehouse_id=None, source_transfer_id=None)


def susun(tipe, items, pratinjau=True):
    bl = Blok(pratinjau)
    return jalan(SS.susun_baris(Kon(), T, items, tipe, G, bl, [])), bl.daftar


def test_skema_item_punya_unit_opsional():
    assert StockAdjustmentItemCreate(product_id=str(A), quantity_adjustment=1).unit is None
    assert StockAdjustmentItemCreate(product_id=str(A), quantity_adjustment=1, unit="Karton").unit == "Karton"
    assert StockAdjustmentItemUpdate(product_id=str(A), quantity_adjustment=1, unit="Karton").unit == "Karton"


def test_tambah_karton_dikonversi_ke_dasar():
    h, bl = susun("increase", [baris(14, "Karton")])
    assert bl == [] and h[0]["quantity_adjustment"] == D("168") and h[0]["unit"] == "Pcs" and h[0]["quantity_after"] == D("340")
    assert h[0]["unit_masuk"] == "Karton" and h[0]["jumlah_masuk"] == D("14")


def test_kurangi_karton_negatif_dan_alias_singkatan():
    h, bl = susun("decrease", [baris(-2, "ktn")])
    assert bl == [] and h[0]["quantity_adjustment"] == D("-24")


def test_hitung_ulang_fisik_dalam_karton():
    h, bl = susun("recount", [baris(0, "Karton", fisik=14)])
    assert bl == [] and h[0]["physical_quantity"] == D("168") and h[0]["quantity_adjustment"] == D("168") - D("172") and h[0]["system_quantity"] == D("172")


def test_tanpa_satuan_atau_satuan_dasar_tak_berubah():
    for u in (None, "", "Pcs", "pcs"):
        h, bl = susun("increase", [baris(5, u)])
        assert bl == [] and h[0]["quantity_adjustment"] == D("5") and h[0]["unit_masuk"] is None and h[0]["jumlah_masuk"] is None


def test_satuan_tanpa_konversi_blok_berkalimat_dengan_field():
    h, bl = susun("increase", [baris(3, "Dus")])
    assert h == [] and [b["code"] for b in bl] == ["SATUAN_TAK_TERKONVERSI"]
    assert "Dus" in bl[0]["message"] and "Minyak" in bl[0]["message"] and "Pcs" in bl[0]["message"] and bl[0]["field"] == "items[0].unit"


def test_tulis_satuan_tak_terkonversi_melempar_galat_berkode_dengan_field():
    bl = Blok(False)
    try:
        jalan(SS.susun_baris(Kon(), T, [baris(3, "Dus")], "increase", G, bl, []))
        assert False, "harus melempar"
    except GalatBerkode as e:
        assert e.code == "SATUAN_TAK_TERKONVERSI" and e.field == "items[0].unit" and e.status_code == 400


def test_baris_draf_json_memuat_teks_campur_dan_masukan():
    h, _ = susun("increase", [baris(14, "Karton")])
    d = SS._draf_json(h[0])
    assert d["quantity_adjustment"] == "168" and d["unit"] == "Pcs" and d["unit_input"] == "Karton" and d["quantity_input"] == "14"
    assert d["quantity_adjustment_campur_text"] == "14 Karton" and d["quantity_before_campur_text"] == "14 Karton 4 Pcs" and d["quantity_after_campur_text"] == "28 Karton 4 Pcs"


def test_baris_tanpa_satuan_tetap_punya_teks_campur_dan_masukan_kosong():
    h, _ = susun("increase", [baris(5)])
    d = SS._draf_json(h[0])
    assert d["unit_input"] is None and d["quantity_input"] is None and d["quantity_adjustment_campur_text"] is None and d["quantity_before_campur_text"] == "14 Karton 4 Pcs"


def test_konversi_teks_campur_disaring_tenant():
    SQL_FETCH.clear()
    susun("increase", [baris(5)])
    konv = [q for q in SQL_FETCH if "FROM unit_conversions" in q and "conversion_factor > 1" in q]
    assert konv and all("tenant_id = $1" in q for q in konv)
