"""FORM BARANG CW (18 Nov 2026; putusan pemilik 14 Nov + MASTER 18 Nov: ikut Accurate 'Saldo Awal' per gudang): stok awal PER GUDANG (satuan -> dasar, jurnal Dr Persediaan/Cr Ekuitas
Saldo Awal, BUKAN dokumen Penyesuaian), pratinjau Buat memuat akun/pajak yang berlaku + sumbernya, GET /items/form-bawaan, pratinjau Ubah ber-teks, BUG-290 (batch aktif lagi saat void).
Paritas nyata = uji salinan (scripts/ops/uji-salinan-form-barang.sh)."""
import asyncio
import inspect
import pathlib
import re
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.schemas.items import CreateItemRequest
from app.services import barang_aksi as BA
from app.services import bawaan_berlaku as BB
from app.services import inventory_helpers as IH
from app.services import saldo_awal_stok as SAS
from app.services import stok_awal_barang as SAB

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
W1, W2 = uuid.uuid4(), uuid.uuid4()
T = "kaos-biru-konveksi"


def run(aw):
    return asyncio.run(aw)


def badan(**kw):
    d = dict(name="Minyak", base_unit="Pcs", item_type="goods", track_inventory=True, purchase_price=1000, opening_stock_date="2026-11-18",
             conversions=[{"conversion_unit": "Dus", "conversion_factor": 12}],
             stok_awal=[{"warehouse_id": str(W1), "quantity": 2, "unit": "Dus", "unit_cost": 12000}, {"warehouse_id": str(W2), "quantity": 5}])
    d.update(kw)
    return CreateItemRequest(**d)


class Kon:
    def __init__(self, berjalan=False, periode=None, gudang=None):
        self.berjalan, self.periode = berjalan, periode
        self.gudang = gudang if gudang is not None else {W1: ("Gudang A", True, False), W2: ("Gudang B", True, False)}
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM warehouses" in sql:
            assert "tenant_id = $1" in sql     # gudang disaring tenant
            g = self.gudang.get(a[1])
            return {"id": a[1], "name": g[0], "is_active": g[1], "is_system": g[2]} if g else None
        if "FROM fiscal_periods" in sql:
            return {"status": self.periode} if self.periode else None
        return None

    async def fetchval(self, sql, *a):
        if "stock_opnames" in sql:
            return None   # Stok Opname: penjaga gudang terkunci (assert_gudang_tidak_terkunci) -- tak ada opname
        self.sql.append(sql)
        if "journal_entries" in sql and "EXISTS" in sql:
            return self.berjalan
        return None


@pytest.fixture(autouse=True)
def akun_ok(monkeypatch):
    async def ok(conn, tid, akun, role, nama): return "akun-1"
    monkeypatch.setattr(SAS, "_akun", ok)


def kode(r):
    return [b["code"] for b in r["blocks"]]


def test_tanpa_stok_awal_jalur_lama():
    assert run(SAB.rencana(Kon(), T, badan(stok_awal=None))) is None


def test_dua_gudang_satuan_dikonversi_ke_dasar_dan_total():
    r = run(SAB.rencana(Kon(), T, badan()))
    assert r["blocks"] == [] and r["tanggal"] == date(2026, 11, 18)
    a, b = r["baris"]
    assert (a["qty_dasar"], a["rate_dasar"], a["nilai"]) == (D("24"), D("1000.0000"), D("24000.00")) and a["unit_masuk"] == "Dus" and a["qty_masuk"] == D("2")
    assert (b["qty_dasar"], b["rate_dasar"], b["nilai"]) == (D("5"), D("1000"), D("5000.00")) and b["unit_masuk"] is None     # tanpa harga = harga beli
    assert r["total_nilai"] == D("29000.00")
    assert r["efek"][0]["qty_text"] == "2 Dus = 24 Pcs" and r["efek"][0]["gudang"] == "Gudang A" and r["efek"][1]["qty_text"] == "5 Pcs"


@pytest.mark.parametrize("kw,kd,field", [
    (dict(item_type="service", track_inventory=False), "STOK_AWAL_BUKAN_BARANG_STOK", "stok_awal"),
    (dict(track_batches=True), "STOK_AWAL_BARANG_BATCH", "stok_awal"),
    (dict(track_expiry=True), "STOK_AWAL_BARANG_BATCH", "stok_awal"),
    (dict(opening_stock_date="bukan-tanggal"), "STOK_AWAL_TANGGAL_TAK_SAH", "opening_stock_date"),
])
def test_blok_barang_dan_tanggal(kw, kd, field):
    r = run(SAB.rencana(Kon(), T, badan(**kw)))
    assert kode(r) == [kd] and r["blocks"][0]["field"] == field


def test_blok_gudang_tak_ada_nonaktif_ganda_dan_field():
    w3 = uuid.uuid4()
    r = run(SAB.rencana(Kon(), T, badan(stok_awal=[{"warehouse_id": str(w3), "quantity": 1}, {"warehouse_id": "bukan-uuid", "quantity": 1}])))
    assert kode(r) == ["STOK_AWAL_GUDANG_TAK_ADA", "STOK_AWAL_GUDANG_TAK_ADA"] and [b["field"] for b in r["blocks"]] == ["stok_awal[0].warehouse_id", "stok_awal[1].warehouse_id"]
    r = run(SAB.rencana(Kon(gudang={W1: ("Gudang A", False, False)}), T, badan(stok_awal=[{"warehouse_id": str(W1), "quantity": 1}])))
    assert kode(r) == ["STOK_AWAL_GUDANG_NONAKTIF"] and "Gudang A" in r["blocks"][0]["message"]
    r = run(SAB.rencana(Kon(), T, badan(stok_awal=[{"warehouse_id": str(W1), "quantity": 1}, {"warehouse_id": str(W1), "quantity": 2}])))
    assert kode(r) == ["STOK_AWAL_GUDANG_GANDA"]
    r = run(SAB.rencana(Kon(gudang={W1: ("Transit", True, True)}), T, badan(stok_awal=[{"warehouse_id": str(W1), "quantity": 1}])))
    assert kode(r) == ["STOK_AWAL_GUDANG_TAK_ADA"]     # gudang sistem bukan tempat stok awal


def test_satuan_tanpa_konversi_blok_berkalimat_dengan_field():
    r = run(SAB.rencana(Kon(), T, badan(stok_awal=[{"warehouse_id": str(W1), "quantity": 1, "unit": "Karton"}])))
    assert kode(r) == ["SATUAN_TAK_TERKONVERSI"] and r["blocks"][0]["field"] == "stok_awal[0].unit" and "Karton" in r["blocks"][0]["message"] and "Minyak" in r["blocks"][0]["message"]


def test_harga_nol_ditolak():
    r = run(SAB.rencana(Kon(), T, badan(purchase_price=0, stok_awal=[{"warehouse_id": str(W1), "quantity": 1}])))
    assert kode(r) == ["STOK_AWAL_HARGA_NOL"] and r["blocks"][0]["field"] == "stok_awal[0].unit_cost" and "harga pokok" in r["blocks"][0]["message"].lower()


def test_usaha_berjalan_periode_tertutup_akun_belum_diatur(monkeypatch):
    assert kode(run(SAB.rencana(Kon(berjalan=True), T, badan()))) == ["STOK_AWAL_USAHA_BERJALAN"]
    assert kode(run(SAB.rencana(Kon(periode="CLOSED"), T, badan()))) == ["STOK_AWAL_PERIODE_TERTUTUP"]

    async def tak(conn, tid, akun, role, nama): raise HTTPException(409, f"Akun {nama} belum dipetakan")
    monkeypatch.setattr(SAS, "_akun", tak)
    r = run(SAB.rencana(Kon(), T, badan()))
    assert kode(r) == ["STOK_AWAL_AKUN_BELUM_DIATUR"] and "Persediaan" in r["blocks"][0]["message"]


def test_galat_pertama_berkode_sama_dengan_pratinjau():
    r = run(SAB.rencana(Kon(), T, badan(track_batches=True)))
    e = SAB.galat_pertama(r)
    assert e.code == "STOK_AWAL_BARANG_BATCH" and e.field == "stok_awal" and str(e.detail) == r["blocks"][0]["message"]


# ---- penulis: SATU jurnal + satu baris kartu per gudang
class Tulis:
    def __init__(self):
        self.exe, self.fv = [], []

    def is_in_transaction(self): return True

    async def fetchrow(self, sql, *a): return None

    async def fetchval(self, sql, *a):
        if "stock_opnames" in sql:
            return None   # Stok Opname: penjaga gudang terkunci (assert_gudang_tidak_terkunci) -- tak ada opname
        self.fv.append((sql, a))
        if "SQL_SUDAH" in sql or ("EXISTS" in sql and "journal_entries" in sql): return False
        if "COUNT(*)" in sql: return 0
        if "INSERT INTO journal_entries" in sql: return uuid.uuid4()
        return None

    async def execute(self, sql, *a):
        self.exe.append((sql, a))
        return "OK"


def tulis(baris, monkeypatch):
    async def ok(conn, tid, akun, role, nama): return uuid.uuid4()
    monkeypatch.setattr(SAS, "_akun", ok)
    k = Tulis()
    r = run(SAS.catat_saldo_awal_per_gudang(k, tenant_id=T, user_id=None, product_id=uuid.uuid4(), product_code="M", product_name="Minyak", tanggal=date(2026, 11, 18), baris=baris))
    return k, r


def test_penulis_satu_jurnal_dua_baris_kartu_wac_berjalan(monkeypatch):
    k, r = tulis([{"warehouse_id": W1, "qty": D("24"), "rate": D("1000")}, {"warehouse_id": W2, "qty": D("6"), "rate": D("1500")}], monkeypatch)
    assert r["nilai"] == D("33000.00")
    jurnal = [a for s, a in k.fv if "INSERT INTO journal_entries" in s]
    assert len(jurnal) == 1 and jurnal[0][6] == D("33000.00")
    kartu = [a for s, a in k.exe if "INSERT INTO inventory_ledger" in s]
    assert len(kartu) == 2
    # args: tenant, product, code, name, movement, tanggal, SUMBER, jurnal, source_no, qty, rate, nilai, wid, catatan, wac, saldo
    assert kartu[0][9] == D("24") and kartu[0][14] == D("1000") and kartu[0][15] == D("24")
    assert kartu[1][9] == D("6") and kartu[1][14] == D("1100.0000") and kartu[1][15] == D("30")     # (24000+9000)/30
    assert kartu[0][4] == "OPENING_BALANCE" and kartu[1][4] == "OPENING_BALANCE"
    assert sum(1 for s, _ in k.exe if "INSERT INTO journal_lines" in s) == 2


def test_penulis_satu_gudang_persis_perilaku_lama(monkeypatch):
    k, r = tulis([{"warehouse_id": W1, "qty": D("5"), "rate": D("1000")}], monkeypatch)
    kartu = [a for s, a in k.exe if "INSERT INTO inventory_ledger" in s]
    assert len(kartu) == 1 and kartu[0][14] == D("1000") and kartu[0][15] == D("5") and r["nilai"] == D("5000.00")


def test_wrapper_lama_masih_ada_dan_memanggil_inti():
    assert "catat_saldo_awal_per_gudang(" in inspect.getsource(SAS.catat_saldo_awal_stok)


def test_penulis_barang_dan_penentu_terpasang():
    from app.routers import items as IT
    siap = inspect.getsource(IT._siapkan_buat_barang)
    assert "SAB.rencana(" in siap and siap.index("SAB.rencana(") < siap.index("periksa_saldo_awal(") and "SAB.galat_pertama(" in siap
    tulis_ = inspect.getsource(IT.tulis_barang_baru)
    assert "SAB.rencana(" in tulis_ and "catat_saldo_awal_per_gudang(" in tulis_ and "elif body.track_inventory" in tulis_


# ---- berlaku + teks
def test_berlaku_beri_teks_dan_sumber_kategori():
    usaha = {"akun_penjualan": {"id": "a1", "kode": "4-1", "nama": "Penjualan"}, "akun_persediaan": {"id": "a2", "kode": "1-1", "nama": "Persediaan"}}
    h = BB.berlaku({"item_type": "goods", "track_inventory": True, "sales_account_id": "a1", "inventory_account_id": "a9"}, usaha, {"a9": {"id": "a9", "kode": "1-9", "nama": "Persediaan Khusus"}})
    BB.beri_teks(h, {"kategori": "Minuman", "jenis": ["persediaan"]})
    assert h["akun_penjualan"]["sumber"] == "bawaan_usaha" and h["akun_penjualan"]["text"] == "4-1 Penjualan" and h["akun_penjualan"]["sumber_text"] == "Bawaan usaha"
    assert h["akun_persediaan"]["sumber"] == "kategori" and h["akun_persediaan"]["sumber_text"] == "Dari kategori 'Minuman'" and h["akun_persediaan"]["text"] == "1-9 Persediaan Khusus"


def test_pratinjau_ubah_teks_akun_pajak_boolean_dan_peringatan():
    lama = {"nama_produk": "Minyak", "sales_account_id": "a1", "is_returnable": True, "sales_price": 100}
    r = BA.rencana_ubah(lama, {"sales_account_id": "a2", "is_returnable": False, "name": "Minyak"})
    assert BA.ids_perubahan(r) == ["a1", "a2"]
    BA.beri_teks_ubah(r, {"a1": "4-1 Penjualan", "a2": "4-2 Penjualan Jasa"})
    p = {x["medan"]: x for x in r["rencana"]["perubahan"]}
    assert p["sales_account_id"]["medan_text"] == "Akun penjualan" and p["sales_account_id"]["dari_text"] == "4-1 Penjualan" and p["sales_account_id"]["ke_text"] == "4-2 Penjualan Jasa"
    assert p["is_returnable"]["dari_text"] == "Ya" and p["is_returnable"]["ke_text"] == "Tidak"
    assert [x["code"] for x in r["peringatan"]] == ["AKUN_PAJAK_BERLAKU_KEDEPAN"] and "jurnal dan dokumen yang sudah ada tidak berubah" in r["peringatan"][0]["message"]


def test_pratinjau_ubah_tanpa_akun_tak_ada_peringatan_kedepan():
    r = BA.beri_teks_ubah(BA.rencana_ubah({"nama_produk": "A", "sales_price": 1}, {"sales_price": 2}), {})
    assert r["peringatan"] == [] and r["rencana"]["perubahan"][0]["dari_text"] == "1" and r["rencana"]["perubahan"][0]["medan_text"] == "Harga jual"


# ---- form-bawaan
def test_form_bawaan_rute_sebelum_detail_dan_izin():
    src = (APP / "routers" / "items.py").read_text()
    assert src.index('@router.get("/items/form-bawaan")') < src.index('@router.get("/items/{item_id}"')
    assert re.search(r'\^/api/items/form-bawaan\$", \["GET"\], "item", "R"', (APP / "middleware" / "permission_middleware.py").read_text())
    f = inspect.getsource(__import__("app.routers.items", fromlist=["x"]).form_bawaan_barang)
    assert "tenant_id = $1" in f and "NOT is_system" in f and "berlaku_barang" in f and "stok_awal" in f


def test_pratinjau_buat_memuat_berlaku_peringatan_kategori():
    f = inspect.getsource(__import__("app.routers.items", fromlist=["x"]).preview_create_item)
    for k in ('"berlaku"', '"peringatan"', '"kategori_diisi_bawaan"', "SAB.rencana(", "_BB.beri_teks("):
        assert k in f


# ---- BUG-290
class KonPembalik:
    def __init__(self): self.exe = []

    async def fetch(self, sql, *a):
        return [{"id": uuid.uuid4(), "product_id": uuid.uuid4(), "product_code": "P", "product_name": "M", "quantity_in": D("0"), "quantity_out": D("3"), "unit_cost": D("1"),
                 "total_cost": D("3"), "warehouse_id": uuid.uuid4(), "source_number": "INV", "batch_id": uuid.uuid4()}]

    async def execute(self, sql, *a):
        self.exe.append(sql)
        return "UPDATE 1"


def test_void_mengaktifkan_ulang_batch_depleted(monkeypatch):
    async def td(conn, tid): return date.today()

    async def sdn(conn, tid, pid): return D("0"), D("0")

    async def wac(conn, tid, pid, a, b, uc, x): return D("1")
    monkeypatch.setattr(IH, "tanggal_dokumen", td)
    monkeypatch.setattr(IH, "saldo_dan_nilai", sdn)
    monkeypatch.setattr(IH, "snapshot_wac_sesudah", wac)
    k = KonPembalik()
    run(IH.record_inventory_reversal(k, T, "SALES_INVOICE", uuid.uuid4(), uuid.uuid4()))
    assert any("SET status = 'active'" in s and "current_quantity > 0 AND status = 'depleted'" in s for s in k.exe)
    assert any("SET status = 'depleted'" in s for s in k.exe)
