"""DAFTAR HARGA D3 (putusan pemilik 13 Nov): UBAH HARGA MASSAL per daftar -- persen ATAU nominal (naik/turun), tanggal berlaku, pratinjau PER BARANG
(READ ONLY) dari penentu SAMA dengan penulis (services/harga_massal); tulis per barang di transaksi sendiri, idempotensi per barang (X-Idempotency-Key WAJIB),
audit BULK_HARGA. Harga hasil < 0 = blok per barang (DH_HARGA_NEGATIF). Tanggal berlaku = start_date baris (kunci unik daftar tak mengenal tanggal)."""
import pathlib
from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.middleware import permission_middleware as PM
from app.services import bulk
from app.services import harga_massal as HM

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
RUTE = (APP / "routers" / "bulk_price_lists.py").read_text()
MAIN = (APP / "main.py").read_text()
HARI = date(2026, 11, 13)


def _baris(**kw):
    b = {"id": "l1", "unit": None, "dasar": "Pcs", "price": D("1000"), "min_quantity": D("1"), "start_date": None}
    b.update(kw)
    return b


# --- hitung --------------------------------------------------------------------------------------------------------------
def test_hitung_baru_persen_nominal_pembulatan_dan_negatif():
    assert HM.hitung_baru(D("1000"), "persen", D("10")) == D("1100.00")
    assert HM.hitung_baru(D("1000"), "persen", D("-10")) == D("900.00")
    assert HM.hitung_baru(D("1000"), "nominal", D("500")) == D("1500.00")
    assert HM.hitung_baru(D("1000"), "nominal", D("-1000")) == D("0.00")          # nol boleh (CHECK price >= 0)
    assert HM.hitung_baru(D("1000"), "nominal", D("-2000")) is None               # negatif = blok
    assert HM.hitung_baru(D("0.05"), "persen", D("10")) == D("0.06")              # HALF_UP 2dp (Law 9/25)
    assert HM.hitung_baru(D("1000.01"), "persen", D("10")) == D("1100.01")


# --- rencana per barang --------------------------------------------------------------------------------------------------
def test_rencana_barang_daftar_baris_dari_ke_selisih():
    x = HM.rencana_barang("Kaos", [_baris(), _baris(id="l2", unit="Lusin", price=D("11000"), min_quantity=D("1"))], "persen", D("10"), None, HARI)
    assert x["blocks"] == [] and x["ok"] is True
    r = x["rencana"]
    assert r["barang"] == "Kaos" and r["jumlah_baris"] == 2 and r["berlaku_mulai"] is None
    assert r["baris"][0] == {"id": "l1", "unit_text": "Pcs", "min_quantity": 1.0, "dari": D("1000"), "ke": D("1100.00"), "selisih": D("100.00")}
    assert r["baris"][1]["unit_text"] == "Lusin" and r["baris"][1]["ke"] == D("12100.00")


def test_rencana_barang_negatif_blok_menyebut_barang_dan_satuan():
    x = HM.rencana_barang("Kaos", [_baris(unit="Lusin", price=D("500"))], "nominal", D("-900"), None, HARI)
    assert x["ok"] is False and x["blocks"][0]["code"] == "DH_HARGA_NEGATIF"
    assert "Kaos" in x["blocks"][0]["message"] and "Lusin" in x["blocks"][0]["message"]


def test_rencana_barang_tak_ada_di_daftar_dan_tanpa_perubahan():
    assert HM.rencana_barang("Kaos", [], "persen", D("5"), None, HARI)["blocks"][0]["code"] == "DH_BARANG_TAK_DI_DAFTAR"
    assert HM.rencana_barang("Kaos", [_baris()], "persen", D("0"), None, HARI)["blocks"][0]["code"] == "DH_TANPA_PERUBAHAN"


def test_tanggal_berlaku_nanti_peringatan_harga_dasar_dan_lampau_tanpa_peringatan():
    nanti = HM.rencana_barang("Kaos", [_baris()], "persen", D("5"), date(2026, 12, 1), HARI)
    assert nanti["ok"] and [p["code"] for p in nanti["peringatan"]] == ["DH_BERLAKU_NANTI"]
    assert "1 Des 2026" in nanti["peringatan"][0]["message"] and nanti["rencana"]["berlaku_mulai"] == "1 Des 2026"
    lalu = HM.rencana_barang("Kaos", [_baris()], "persen", D("5"), date(2026, 11, 1), HARI)
    assert lalu["peringatan"] == [] and lalu["rencana"]["berlaku_mulai"] == "1 Nov 2026"


# --- penulis -------------------------------------------------------------------------------------------------------------
class _C:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append((sql, a))


@pytest.mark.asyncio
async def test_terapkan_update_harga_tiap_baris_bersaring_tenant_dan_tanggal_berlaku():
    c = _C()
    r = HM.rencana_barang("Kaos", [_baris(), _baris(id="l2", price=D("2000"))], "persen", D("10"), date(2026, 12, 1), HARI)["rencana"]
    await HM.terapkan(c, "t1", "pl1", r)
    assert len(c.sql) == 2
    for s, a in c.sql:
        assert s.lstrip().startswith("UPDATE price_list_items") and "tenant_id" in s and "price_list_id" in s
    assert c.sql[0][1][0] == "l1" and c.sql[0][1][3] == D("1100.00") and c.sql[0][1][4] == date(2026, 12, 1)


@pytest.mark.asyncio
async def test_terapkan_tanpa_tanggal_berlaku_tak_menyentuh_start_date():
    c = _C()
    r = HM.rencana_barang("Kaos", [_baris()], "persen", D("10"), None, HARI)["rencana"]
    await HM.terapkan(c, "t1", "pl1", r)
    assert "start_date" not in c.sql[0][0]


# --- skema ---------------------------------------------------------------------------------------------------------------
def test_badan_permintaan_validasi():
    from app.routers.bulk_price_lists import BulkHargaRequest
    ok = BulkHargaRequest(mode="persen", nilai=D("5"))
    assert ok.item_ids is None and ok.berlaku_mulai is None
    for salah in ({"mode": "persen", "nilai": D("0")}, {"mode": "persen", "nilai": D("-101")}, {"mode": "ngawur", "nilai": D("1")}):
        with pytest.raises(ValidationError):
            BulkHargaRequest(**salah)
    assert BulkHargaRequest(mode="nominal", nilai=D("-1500")).nilai == D("-1500")


# --- rute ----------------------------------------------------------------------------------------------------------------
def test_rute_pratinjau_dan_tulis_satu_penentu_idempotensi_dan_audit():
    assert '@router.post("/{price_list_id}/bulk/price/preview")' in RUTE and '@router.post("/{price_list_id}/bulk/price")' in RUTE
    assert RUTE.count("HM.ukur_rencana(") == 2                       # pratinjau + tulis = penentu SAMA
    assert "baca_saja=True" in RUTE                                  # pratinjau READ ONLY
    assert "BULK_KUNCI_WAJIB" in RUTE and "jalankan_per_item" in RUTE
    assert "HM.terapkan(" in RUTE                                    # satu penulis
    assert "price_list_id" in RUTE.split("payload=", 1)[1][:120]     # kunci per barang memuat daftar (isi beda = tak bentrok)


def test_aksi_harga_di_daftar_putih_dan_router_terdaftar():
    assert "harga" in bulk.AKSI_DIIZINKAN
    assert "bulk_price_lists" in MAIN and "include_router(bulk_price_lists.router" in MAIN


@pytest.mark.parametrize("jalur", ["/api/price-lists/ID/bulk/price/preview", "/api/price-lists/ID/bulk/price"])
def test_izin_ubah(jalur):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, "POST") == ("price_list", "U"), jalur
