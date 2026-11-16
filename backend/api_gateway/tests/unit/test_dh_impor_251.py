"""BUG-251 (16 Nov 2026): impor/ekspor Excel harga Daftar Harga -- baca, ekspor, pratinjau == tulis, kunci berjangka, atomik, izin rute."""
import asyncio
import io
import pathlib
import re
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import price_lists as PL
from app.services import harga_massal as HM
from app.services import impor_harga as IH
from app.services import satuan_dasar as SD
from app.services.galat_berkode import GalatBerkode

GW = pathlib.Path(__file__).resolve().parents[2]
TID = "t-1"
PID = uuid4()
PL_ID = uuid4()
HARI = date(2026, 11, 16)


def xlsx(judul, baris):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(judul)
    for b in baris:
        ws.append(b)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- baca
def test_baca_judul_bebas_dan_nomor_baris_berkas():
    isi = xlsx(["SKU", "Untuk Kts Diatas", "Harga Baru", "Tanggal Mulai", "Catatan"],
               [["A-1", 10, "9.500", "2026-11-20", "x"], [None, None, None, None, None], ["A-2", None, 7000, None, None]])
    r = IH.baca_xlsx(isi)
    assert [b["_no"] for b in r["baris"]] == [2, 4]                     # baris kosong dilewati; nomor = baris Excel
    assert r["baris"][0]["kode"] == "A-1" and r["baris"][0]["min_qty"] == 10 and r["baris"][0]["harga"] == "9.500"
    assert r["tak_dipakai"] == ["Catatan"] and any("Catatan" in p for p in r["peringatan"])


def test_baca_kolom_wajib_hilang_ditolak_berkalimat():
    with pytest.raises(HTTPException) as e:
        IH.baca_xlsx(xlsx(["Kode barang", "Nama"], [["A", "B"]]))
    assert e.value.detail["code"] == "KOLOM_TIDAK_DIKENAL" and "harga" in e.value.detail["message"].lower()


def test_baca_batas_baris_dan_ukuran():
    with pytest.raises(HTTPException) as e:
        IH.baca_xlsx(xlsx(["Kode", "Harga"], [[f"K{i}", 1] for i in range(IH.MAKS_BARIS + 1)]))
    assert e.value.detail["code"] == "IMPOR_TERLALU_BANYAK"
    with pytest.raises(HTTPException) as e2:
        IH.baca_xlsx(b"x" * (IH.MAKS_BYTE + 1))
    assert e2.value.detail["code"] == "BERKAS_TERLALU_BESAR"


def test_baca_bukan_excel_ditolak():
    with pytest.raises(HTTPException) as e:
        IH.baca_xlsx(b"bukan excel")
    assert e.value.detail["code"] == "BERKAS_TIDAK_TERBACA"


# ---------------------------------------------------------------- ekspor
RAW = [{"item_code": "A-1", "nama": "Minyak", "unit": None, "min_quantity": Decimal("1.000"), "price": Decimal("12500.00"),
        "min_price": Decimal("11000"), "discount_percent": Decimal("5.00"), "start_date": date(2026, 11, 20), "end_date": None, "dasar": "Pcs"},
       {"item_code": "A-1", "nama": "Minyak", "unit": "Dus", "min_quantity": Decimal("10"), "price": Decimal("140000"),
        "min_price": None, "discount_percent": None, "start_date": None, "end_date": date(2026, 12, 31), "dasar": "Pcs"}]


def test_ekspor_xlsx_dibaca_ulang_oleh_impor_dan_satuan_kosong_jadi_satuan_dasar():
    r = IH.baca_xlsx(IH.ekspor_xlsx(RAW))
    assert [m["medan"] for m in r["pemetaan"]] == ["kode", "nama", "satuan", "min_qty", "harga", "min_harga", "diskon", "mulai", "akhir"] and not r["tak_dipakai"]
    assert r["baris"][0]["satuan"] == "Pcs" and r["baris"][1]["satuan"] == "Dus" and r["baris"][0]["harga"] == 12500


def test_ekspor_xlsx_kosong_hanya_judul():
    r = IH.baca_xlsx(IH.ekspor_xlsx([]))
    assert r["baris"] == [] and len(r["pemetaan"]) == 9


def test_ekspor_csv_id_id_bom_titik_koma_koma_desimal():
    b = IH.ekspor_csv(RAW)
    assert b.startswith(b"\xef\xbb\xbf")
    baris = b.decode("utf-8-sig").split("\r\n")
    assert baris[0] == "Kode barang;Nama;Satuan;Mulai qty;Harga;Harga minimum;Diskon %;Mulai berlaku;Berakhir"
    assert baris[1] == "A-1;Minyak;Pcs;1;12500,00;11000;5,00;2026-11-20;"


def test_ekspor_csv_tak_menjalankan_rumus():
    r = [{**RAW[0], "nama": "=HYPERLINK(\"x\")"}]
    assert "'=HYPERLINK" in IH.ekspor_csv(r).decode("utf-8-sig")


# ---------------------------------------------------------------- rencana (konn palsu; penentu per baris di-stub)
class Konn:
    def __init__(self, ada=(), barang=None):
        self.ada, self.barang = list(ada), barang if barang is not None else {"A-1": {"id": PID, "nama_produk": "Minyak", "dasar": "Pcs", "item_code": "A-1"}}
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM price_list_items" in sql:
            return self.ada
        if "FROM products" in sql:
            b = self.barang.get(a[1].upper() if isinstance(a[1], str) else a[1])
            return [b] if b else []
        raise AssertionError(sql)


def baris_ada(harga, unit=None, qty=1, mulai=None, akhir=None, diskon=None, minimum=None):
    return {"id": uuid4(), "item_id": PID, "unit": unit, "min_quantity": Decimal(qty), "price": Decimal(harga), "start_date": mulai,
            "end_date": akhir, "discount_percent": diskon, "is_default_unit": False, "item_code": "A-1", "min_price": minimum}


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    calls = {"siapkan": [], "tulis": [], "audit": []}

    async def alias(conn, tid):
        return {}

    async def hari(conn, tid):
        return HARI

    async def siapkan(conn, tid, pl_id, item, line_id=None):
        calls["siapkan"].append((item, line_id))
        if item.unit == "Rusak":
            raise GalatBerkode(400, "SATUAN_TAK_TERKONVERSI", "Satuan Rusak belum dikonversi.", field="unit")
        return PID

    async def hpp(conn, tid, item_id, baris):
        for b in baris:
            b["hpp"] = Decimal("10000")

    async def tulis_baris(conn, tid, pl_id, item, line_id=None):
        calls["tulis"].append((item, line_id))
        return uuid4()

    async def audit(conn, ctx, aksi, modul, ids, ringkas=None):
        calls["audit"].append((aksi, ringkas))
    monkeypatch.setattr(SD, "_alias", alias)
    monkeypatch.setattr("app.utils.tanggal_tenant.tanggal_dokumen", hari)
    monkeypatch.setattr(PL, "siapkan_baris_harga", siapkan)
    monkeypatch.setattr(PL, "tulis_baris_harga", tulis_baris)
    monkeypatch.setattr(HM, "_hpp_baris", hpp)
    monkeypatch.setattr(IH.bulk, "catat_audit", audit)
    return calls


def run(c, baris, mulai=None, kunci=False):
    return asyncio.run(IH.rencana(c, TID, PL_ID, baris, mulai, kunci))


def b(kode="A-1", harga=12000, **kw):
    return {"kode": kode, "harga": harga, **kw}


def aksi(r):
    return [x["aksi"] for x in r["baris"]]


def test_tambah_ubah_tetap_dan_hitungan():
    c = Konn([baris_ada(10000, qty=1), baris_ada(5000, unit="Dus", qty=10)])
    r = run(c, [b(harga=11000), b(satuan="Dus", min_qty=10, harga=5000), b(min_qty=50, harga=12000)])
    assert aksi(r) == ["ubah", "tetap", "tambah"] and r["ok"]
    assert r["ringkas"] == {"dibaca": 3, "tambah": 1, "ubah": 1, "tetap": 1, "ditolak": 0, "peringatan": 0}
    x = r["baris"][0]["rencana"]
    assert x["dari"] == Decimal(10000) and x["ke"] == Decimal(11000) and x["selisih"] == Decimal(1000) and r["teks"].startswith("3 baris dibaca")


def test_satuan_dasar_cocok_dengan_baris_satuan_kosong_dan_tidak_menggandakan():
    c = Konn([baris_ada(10000, unit=None)])
    r = run(c, [b(satuan="pcs", harga=10000)])           # ekspor menulis "Pcs" untuk satuan kosong -> impor ulang = tetap, bukan baris baru
    assert aksi(r) == ["tetap"]


def test_beda_tanggal_mulai_adalah_baris_berjangka_baru_bukan_ganda():
    c = Konn([baris_ada(75000)])
    r = run(c, [b(harga=90000, mulai="2026-11-20"), b(harga=100000, mulai="25/11/2026")])
    assert aksi(r) == ["tambah", "tambah"] and r["ok"]
    assert [x["rencana"]["berlaku_mulai"] for x in r["baris"]] == ["2026-11-20", "2026-11-25"]


def test_kunci_ganda_di_berkas_ditolak_dengan_nomor_baris():
    r = run(Konn(), [{**b(), "_no": 2}, {**b(harga=1), "_no": 7}])
    assert not r["ok"] and r["baris"][1]["blocks"][0]["code"] == "HARGA_GANDA" and "baris 2" in r["baris"][1]["blocks"][0]["message"]
    assert r["baris"][1]["no"] == 7


def test_blok_per_baris_kode_kosong_barang_tak_ada_harga_salah_tanggal_salah():
    r = run(Konn(), [b(kode=""), b(kode="ZZZ"), b(harga="abc"), b(harga=-5), b(harga="1,555"), b(mulai="kemarin"), b(min_qty=0),
                     b(mulai="2026-12-01", akhir="2026-11-01")])
    kode = [x["blocks"][0]["code"] for x in r["baris"]]
    assert kode == ["IMPOR_KODE_WAJIB", "IMPOR_BARANG_TAK_ADA", "IMPOR_HARGA_TIDAK_SAH", "IMPOR_HARGA_TIDAK_SAH", "IMPOR_HARGA_TIDAK_SAH",
                    "IMPOR_TANGGAL_TIDAK_SAH", "IMPOR_QTY_TIDAK_SAH", "PERIODE_TIDAK_SAH"] and not r["ok"] and r["ringkas"]["ditolak"] == 8


def test_galat_penentu_baris_jadi_blok_berkode_dan_berfield():
    r = run(Konn(), [b(satuan="Rusak")])
    assert r["baris"][0]["blocks"][0] == {"code": "SATUAN_TAK_TERKONVERSI", "message": "Satuan Rusak belum dikonversi.", "field": "unit"}


def test_kosong_dan_terlalu_banyak_adalah_blok_batch():
    assert run(Konn(), [])["blocks"][0]["code"] == "IMPOR_KOSONG"
    assert run(Konn(), [b()] * (IH.MAKS_BARIS + 1))["blocks"][0]["code"] == "IMPOR_TERLALU_BANYAK"


def test_peringatan_nol_masa_depan_dan_bawah_hpp_bentuk_bug207():
    r = run(Konn(), [b(harga=0), b(min_qty=2, harga=9000, mulai="2026-12-01"), b(min_qty=3, harga=15000)])
    kode = [[p["code"] for p in x["peringatan"]] for x in r["baris"]]
    assert kode == [["DH_HARGA_NOL"], ["DH_BERLAKU_NANTI", "DH_DI_BAWAH_HPP"], []]       # harga 0 = nol saja (bukan juga bawah HPP); HPP stub 10.000
    p = r["baris"][1]["peringatan"][0]
    assert p["butuh_konfirmasi"] is True and p["pesan"] == p["message"] and r["ok"]          # peringatan tak memblok


def test_ubah_tanpa_kolom_berakhir_mempertahankan_akhir_lama_dengan_kolom_mengosongkan():
    lama = baris_ada(10000, akhir=date(2026, 12, 31))
    r1 = run(Konn([lama]), [b(harga=11000)])
    assert r1["baris"][0]["rencana"]["berakhir"] == "2026-12-31"
    r2 = run(Konn([lama]), [b(harga=11000, akhir=None)])
    assert r2["baris"][0]["rencana"]["berakhir"] is None


def test_berlaku_mulai_opsional_mengisi_baris_tanpa_tanggal():
    r = run(Konn(), [b(), b(min_qty=2, mulai="2026-12-05")], mulai=date(2026, 12, 1))
    assert [x["rencana"]["berlaku_mulai"] for x in r["baris"]] == ["2026-12-01", "2026-12-05"]


def test_rencana_baca_saja_dan_kunci_hanya_saat_tulis():
    c1, c2 = Konn(), Konn()
    run(c1, [b()])
    run(c2, [b()], kunci=True)
    assert "FOR UPDATE" not in "".join(c1.sql) and "FOR UPDATE" in "".join(c2.sql)


# ---------------------------------------------------------------- tulis atomik
def test_tulis_blok_menolak_seluruh_batch_nol_tulis(stub):
    c = Konn()
    with pytest.raises(IH.GalatImporDitolak) as e:
        asyncio.run(IH.tulis(c, {"tenant_id": TID, "user_id": "u"}, PL_ID, [b(), b(kode="ZZZ")]))
    d = e.value.detail
    assert e.value.status_code == 422 and d["code"] == "IMPOR_DITOLAK" and d["rencana"]["ringkas"]["ditolak"] == 1 and "_baris_tulis" not in d["rencana"]
    assert stub["tulis"] == [] and stub["audit"] == []


def test_tulis_memakai_penulis_tunggal_hanya_untuk_baris_berubah_dan_mencatat_audit(stub):
    ada = baris_ada(10000, qty=1)
    r = asyncio.run(IH.tulis(Konn([ada, baris_ada(5000, qty=5)]), {"tenant_id": TID, "user_id": "u"}, PL_ID,
                             [b(harga=11000), b(min_qty=5, harga=5000), b(min_qty=9, harga=1)]))
    assert r == {"tambah": 1, "ubah": 1, "tetap": 1, "jumlah": 3}
    assert [(i.price, l) for i, l in stub["tulis"]] == [(Decimal(11000), ada["id"]), (Decimal(1), None)]
    assert stub["audit"] == [("impor_harga", {"tambah": 1, "ubah": 1, "tetap": 1})]


def test_ubah_menyalin_diskon_dan_default_unit_baris_lama(stub):
    asyncio.run(IH.tulis(Konn([baris_ada(10000, diskon=Decimal("5"))]), {"tenant_id": TID}, PL_ID, [b(harga=11000)]))
    item = stub["tulis"][0][0]
    assert item.discount_percent == 5.0 and item.price == Decimal(11000)


# ---------------------------------------------------------------- rute & izin
def test_rute_impor_ekspor_terdaftar_dan_izin_eksplisit():
    pm = (GW / "app/middleware/permission_middleware.py").read_text()
    assert re.search(r'price-lists/\[\^/\]\+/import\(/baca\|/preview\)\?\$", \["POST"\], "price_list", "U"', pm)
    assert re.search(r'price\\-lists/\[\^/\]\+/export\$", \["GET"\], "price_list", "R"', pm)
    inv = (GW / "tests/unit/inventaris_rute_tulis.txt").read_text()
    for r in ("import/preview", "import/baca", "import"):
        assert f"POST /api/price-lists/{{price_list_id}}/{r}\n" in inv
    jalur = {(m, x.path) for x in PL.router.routes for m in x.methods}
    assert ("GET", "/{price_list_id}/export") in jalur and ("POST", "/{price_list_id}/import") in jalur
    assert "/api/price-lists/{price_list_id}/import/preview" in (GW / "app/middleware/rate_limit_middleware.py").read_text()


def test_tulis_wajib_kunci_idempotensi_dan_badan_wajib_baris():
    s = pathlib.Path(PL.__file__).read_text()
    assert 'IMPOR_KUNCI_WAJIB' in s and 'idem_buat.mulai_aksi(conn, ctx, kunci, "DH_IMPOR"' in s
    with pytest.raises(GalatBerkode) as e:
        PL._badan_impor({"baris": "bukan daftar"})
    assert e.value.code == "IMPOR_BARIS_WAJIB"
    assert PL._badan_impor({"baris": [], "berlaku_mulai": "2026-12-01"}) == ([], date(2026, 12, 1))


# ---------------------------------------------------------------- BUG-040 harga minimum
def test_impor_harga_minimum_tambah_ubah_dan_pertahankan_bila_kolom_tak_ada():
    lama = baris_ada(10000, minimum=Decimal("8000"))
    r1 = run(Konn([lama]), [b(harga=11000)])                              # kolom Harga minimum tak ada -> lantai lama utuh
    assert r1["baris"][0]["rencana"]["harga_minimum"] == Decimal("8000") and r1["baris"][0]["aksi"] == "ubah"
    r2 = run(Konn([lama]), [b(harga=10000, min_harga="9.000")])           # kolom ada -> lantai berubah (aksi ubah walau harga sama)
    assert r2["baris"][0]["aksi"] == "ubah" and r2["baris"][0]["rencana"]["harga_minimum"] == Decimal("9000")
    r3 = run(Konn([lama]), [b(harga=10000, min_harga=None)])              # kolom ada tapi kosong -> dikosongkan
    assert r3["baris"][0]["aksi"] == "ubah" and r3["baris"][0]["rencana"]["harga_minimum"] is None
    r4 = run(Konn([lama]), [b(harga=10000, min_harga=8000)])
    assert r4["baris"][0]["aksi"] == "tetap"
    assert run(Konn(), [b(min_harga=500)])["baris"][0]["rencana"]["harga_minimum"] == Decimal(500)


def test_impor_harga_minimum_tidak_sah_diblok():
    r = run(Konn(), [b(min_harga=-1), b(min_harga="abc"), b(min_harga="1,555")])
    assert [x["blocks"][0]["code"] for x in r["baris"]] == ["IMPOR_HARGA_TIDAK_SAH"] * 3 and all(x["blocks"][0]["field"] == "min_harga" for x in r["baris"])


def test_tulis_impor_meneruskan_harga_minimum_ke_penulis_tunggal(stub):
    asyncio.run(IH.tulis(Konn(), {"tenant_id": TID}, PL_ID, [b(harga=12000, min_harga=9000)]))
    assert stub["tulis"][0][0].min_price == Decimal(9000)


class KonnSiap:
    def __init__(self):
        self.n = 0

    async def fetchval(self, sql, *a):
        self.n += 1
        return 1 if "FROM products" in sql else None


def test_penulis_baris_menolak_minimum_di_atas_harga_dan_menerima_sama(monkeypatch):
    monkeypatch.undo()                                                     # siapkan_baris_harga ASLI (stub autouse dilepas)
    from app.schemas.price_lists import PriceListItemCreate
    lantai = lambda p, m: PriceListItemCreate(item_id=str(PID), price=p, min_quantity=1, min_price=m)
    with pytest.raises(GalatBerkode) as e:
        asyncio.run(PL.siapkan_baris_harga(KonnSiap(), TID, PL_ID, lantai(1000, 1001), line_id=uuid4()))
    assert e.value.code == "HARGA_MINIMUM_DI_ATAS_HARGA" and e.value.status_code == 400 and e.value.field == "min_price"
    assert asyncio.run(PL.siapkan_baris_harga(KonnSiap(), TID, PL_ID, lantai(1000, 1000), line_id=uuid4())) == PID
    assert asyncio.run(PL.siapkan_baris_harga(KonnSiap(), TID, PL_ID, lantai(1000, None), line_id=uuid4())) == PID


def test_skema_min_price_nonnegatif_dua_desimal_dan_aditif():
    from app.schemas.price_lists import PriceListItemCreate, PriceListItemUpdate
    assert PriceListItemCreate(item_id="x", price=1).min_price is None and PriceListItemUpdate().min_price is None
    with pytest.raises(ValueError):
        PriceListItemCreate(item_id="x", price=1, min_price=-1)
    with pytest.raises(ValueError):
        PriceListItemCreate(item_id="x", price=1, min_price=Decimal("1.234"))


def test_penentu_harga_minimum_satu_sakelar():
    from app.services import harga_minimum as HMN
    assert HMN.MODE_PENEGAKAN == "peringatan"
    assert HMN.periksa(9000, None) is None and HMN.periksa(10000, 10000) is None and HMN.periksa(None, 10000) is None
    p = HMN.periksa(9000, 10000, nama="Minyak")
    assert p["code"] == "JUAL_DI_BAWAH_MINIMUM" and p["butuh_konfirmasi"] is True and p["blok"] is False and p["pesan"] == p["message"] and "Rp 9.000" in p["pesan"] and "Rp 10.000" in p["pesan"]
    k = HMN.periksa(9000, 10000, mode="blok")
    assert k["blok"] is True and k["butuh_konfirmasi"] is False
    with pytest.raises(GalatBerkode) as e:
        HMN.tolak_bila_blok(k)
    assert e.value.status_code == 409 and e.value.code == "JUAL_DI_BAWAH_MINIMUM"
    assert HMN.tolak_bila_blok(p) == p and HMN.periksa(1, 10000, mode="mati") is None
    with pytest.raises(ValueError):
        HMN.periksa(1, 2, mode="asal")


def test_resolve_mengembalikan_harga_minimum_baris_yang_menang():
    import inspect
    from app.services import harga_jual as HJ
    s = inspect.getsource(HJ)
    assert "SELECT item_id, unit, price, min_price," in s and '"harga_minimum": None' in s and 'harga_minimum=float(_r2(r["min_price"]))' in s


def test_d3_peringatan_di_bawah_minimum_dan_salinan_berjangka_membawa_lantai():
    import inspect
    from app.services import harga_massal as HMS
    s = inspect.getsource(HMS)
    assert "DH_DI_BAWAH_MINIMUM" in s and "o.is_default_unit, o.min_price" in s and "pli.price, pli.min_price," in s


def test_migrasi_v501_maju_dan_rollback_tanpa_transaksi():
    mig = GW.parent / "migrations"
    maju = (mig / "V501__price_list_items_min_price.sql").read_text()
    mundur = (mig / "V501__price_list_items_min_price_ROLLBACK.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS min_price NUMERIC(18,2)" in maju and "CHECK (min_price IS NULL OR min_price >= 0)" in maju
    assert not re.search(r"^\s*(BEGIN|COMMIT)\b", maju + mundur, re.M | re.I)
    assert "DROP COLUMN IF EXISTS min_price" in mundur and "DROP CONSTRAINT IF EXISTS chk_price_list_items_min_price_nonneg" in mundur


# ---------------------------------------------------------------- BUG-253 diskon % per baris (resolve + impor/ekspor)
def test_impor_diskon_tambah_ubah_pertahankan_bila_kolom_tak_ada_dan_kosongkan_bila_kolom_kosong():
    lama = baris_ada(10000, diskon=Decimal("5"))
    r1 = run(Konn([lama]), [b(harga=10000)])                                # kolom Diskon % tak ada -> diskon lama utuh, harga sama = tetap
    assert r1["baris"][0]["aksi"] == "tetap" and r1["baris"][0]["rencana"]["diskon_persen"] == 5.0
    r2 = run(Konn([lama]), [b(harga=10000, diskon="7,5")])                  # kolom ada -> berubah
    assert r2["baris"][0]["aksi"] == "ubah" and r2["baris"][0]["rencana"]["diskon_persen"] == 7.5
    r3 = run(Konn([lama]), [b(harga=10000, diskon=None)])                   # kolom ada tapi kosong -> dikosongkan
    assert r3["baris"][0]["aksi"] == "ubah" and r3["baris"][0]["rencana"]["diskon_persen"] is None
    assert run(Konn([lama]), [b(harga=10000, diskon=5)])["baris"][0]["aksi"] == "tetap"
    assert run(Konn(), [b(diskon=10)])["baris"][0]["rencana"]["diskon_persen"] == 10.0


def test_impor_diskon_di_luar_0_100_atau_tak_terbaca_diblok():
    r = run(Konn(), [b(diskon=-1), b(diskon=100.5), b(diskon="abc"), b(diskon="1,555")])
    assert [x["blocks"][0]["code"] for x in r["baris"]] == ["IMPOR_DISKON_TIDAK_SAH"] * 4 and all(x["blocks"][0]["field"] == "diskon" for x in r["baris"])
    assert run(Konn(), [b(diskon=0)])["baris"][0]["ok"] and run(Konn(), [b(diskon=100)])["baris"][0]["ok"]


def test_tulis_impor_meneruskan_diskon_ke_penulis_tunggal(stub):
    asyncio.run(IH.tulis(Konn(), {"tenant_id": TID}, PL_ID, [b(harga=12000, diskon=12.5)]))
    assert stub["tulis"][0][0].discount_percent == 12.5


def test_resolve_mengembalikan_diskon_persen_baris_yang_menang_null_bila_tak_diatur():
    import inspect
    from app.services import harga_jual as HJ
    s = inspect.getsource(HJ)
    assert "min_price, discount_percent, min_quantity" in s and '"diskon_persen": None' in s
    assert 'diskon_persen=float(r["discount_percent"]) if r["discount_percent"] is not None else None' in s
