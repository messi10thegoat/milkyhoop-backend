"""V517 dimensi/berat + min qty jual/beli (flag dimensi_barang_v1) dan unggah gambar barang (flag gambar_barang_v1)."""
import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import dimensi_barang as DIMB
from app.services import gambar_barang as GB
from app.schemas.items import UpdateItemRequest

MIG = Path(__file__).resolve().parents[3] / "migrations"
APP = Path(__file__).resolve().parents[2] / "app"


class Kon:
    def __init__(self, flag=True, minq=Decimal("12")):
        self.flag, self.minq = flag, minq

    async def fetchval(self, sql, *a):
        return self.flag

    async def fetch(self, sql, *a):
        return [{"id": "p1", "nama_produk": "Kaos", "satuan": "pcs", "minq": self.minq}]


def jalan(c):
    return asyncio.run(c)


def test_flag_mati_tolak_medan_diisi_saja():
    jalan(DIMB.cek(Kon(flag=False), "t", UpdateItemRequest(name="x")))          # tanpa medan V517 = perilaku lama
    with pytest.raises(HTTPException) as e:
        jalan(DIMB.cek(Kon(flag=False), "t", UpdateItemRequest(weight=Decimal("1.5"))))
    assert e.value.status_code == 404 and e.value.detail["code"] == "FITUR_BELUM_AKTIF"
    jalan(DIMB.cek(Kon(flag=True), "t", UpdateItemRequest(weight=Decimal("1.5"), weight_unit="kg")))


def test_skema_tolak_satuan_dan_negatif():
    with pytest.raises(Exception):
        UpdateItemRequest(weight_unit="ton")
    with pytest.raises(Exception):
        UpdateItemRequest(min_qty_jual=Decimal("0"))


def test_teks():
    t = DIMB.teks({"weight": Decimal("1.500"), "weight_unit": "kg", "length": Decimal("30"), "width": Decimal("20"), "height": None,
                   "dimension_unit": "cm", "min_qty_jual": Decimal("12"), "min_qty_beli": None}, "pcs")
    assert t["berat_text"] == "1,5 kg" and t["dimensi_text"] == "30 × 20 × – cm" and t["min_qty_jual_text"] == "Min. jual 12 pcs" and t["min_qty_beli_text"] is None
    assert DIMB.teks({k: None for k in DIMB.MEDAN})["berat_text"] is None


def test_peringatan_min_jual_bukan_blok():
    p = jalan(DIMB.peringatan(Kon(), "t", [{"item_id": "p1", "qty_dasar": Decimal("6"), "baris": 1}, {"item_id": "p1", "qty_dasar": Decimal("12"), "baris": 2}], "jual"))
    assert p == [{"code": "QTY_DI_BAWAH_MIN_JUAL", "message": "Kaos: 6 pcs di bawah minimal jual 12 pcs.", "item_id": "p1", "baris": 1}]
    assert jalan(DIMB.peringatan(Kon(flag=False), "t", [{"item_id": "p1", "qty_dasar": Decimal("1")}], "beli")) == []


def test_peringatan_terpasang_di_semua_calculate():
    for f, arah in (("quotes", "jual"), ("sales_orders", "jual"), ("sales_invoices", "jual"), ("purchase_orders", "beli"), ("bills", "beli")):
        assert f'DIMB.peringatan_baris(' in (APP / f"routers/{f}.py").read_text() and f'"{arah}")' in (APP / f"routers/{f}.py").read_text(), f


def test_gambar_tanda_tangan_byte():
    assert GB.tentukan(b"\xff\xd8\xff\xe0" + b"0" * 10)[:2] == ("image/jpeg", ".jpg")
    assert GB.tentukan(b"\x89PNG\r\n\x1a\n" + b"0")[:2] == ("image/png", ".png")
    assert GB.tentukan(b"RIFF\x00\x00\x00\x00WEBPVP8 ")[:2] == ("image/webp", ".webp")
    assert GB.tentukan(b"<svg onload=x>")[2][1] == "GAMBAR_TIPE_TIDAK_DIDUKUNG"
    assert GB.tentukan(b"\xff\xd8\xff" + b"0" * GB.MAKS_BYTE)[2][:2] == (413, "GAMBAR_TERLALU_BESAR")
    assert GB.tentukan(b"")[2][1] == "GAMBAR_KOSONG"
    assert GB.jalur("t", b"abc", ".png").startswith("t/barang/") and GB.jalur("t", b"abc", ".png").endswith(".png")


def test_url_tampil_non_s3_apa_adanya():
    assert jalan(GB.url_tampil("https://x/y.png")) == "https://x/y.png" and jalan(GB.url_tampil(None)) is None


def test_migrasi_v517_aditif_berpembalik():
    up = (MIG / "V517__dimensi_minqty_barang.sql").read_text()
    assert up.count("ADD COLUMN IF NOT EXISTS") == 8 and "UPDATE" not in up.upper().split("NOT VALID")[0].replace("UPDATE PRODUCTS", "") and "BEGIN" not in up
    assert "DROP COLUMN IF EXISTS min_qty_beli" in (MIG / "V517__dimensi_minqty_barang_ROLLBACK.sql").read_text()


def test_izin_gambar_sama_dengan_ubah_barang():
    s = (APP / "middleware/permission_middleware.py").read_text()
    assert '(r"^/api/items/[^/]+/image$", ["POST", "DELETE"], "item", "U")' in s
