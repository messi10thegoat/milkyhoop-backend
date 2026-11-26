"""U-V2 Varian (27 Nov 2026, flag gelap varian_v1; DESAIN-varian §1 B4-B6 + putusan Q1/Q3):
SKU server <KODE-INDUK>-<NILAI>.., mode_harga sama/per_varian, aksi eksplisit terapkan harga induk -> varian, picker sembunyikan induk."""
import asyncio
import inspect
import json
import uuid

import pytest
from fastapi import HTTPException

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import matrix_items as MR
from app.schemas.barang_varian import BuatMatriksRequest, TambahVarianRequest, TerapkanHargaRequest
from app.services import barang_varian as BV

T = "t-uji"
CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}
INDUK = {"id": uuid.uuid4(), "nama_produk": "Kaos", "item_code": "BRG-0007", "base_unit": "Pcs", "kategori": "Kaos", "deskripsi": None,
         "sales_price": 75000, "purchase_price": 40000}


class K:
    def __init__(self, sku_dipakai=(), anak=(), induk=INDUK, kode_induk="BRG-0010"):
        self.sku_dipakai, self.anak, self.induk, self.kode_induk = set(sku_dipakai), list(anak), induk, kode_induk
        self.tulis = []

    async def fetch(self, sql, *a):
        assert "tenant_id = $1" in sql
        if "upper(sku)" in sql:
            return [{"sku": s} for s in a[1] if s in self.sku_dipakai]
        if "nama_produk = ANY" in sql:
            return []
        if "matrix_parent_id = $2" in sql:
            return self.anak
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        if "is_matrix_parent" in sql:
            return self.induk
        if "matrix_attributes" in sql:
            return None
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        if "item_code" in sql:
            return self.kode_induk
        return 0

    async def execute(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))
        return "UPDATE 1"


def _body(**kw):
    d = dict(name="Kaos", base_unit="Pcs", sales_price=75000, purchase_price=40000,
             dimensi=[{"nama": "ukuran", "nilai": ["S", "XL"]}, {"nama": "warna", "nilai": ["Biru Muda"]}])
    d.update(kw)
    return BuatMatriksRequest(**d)


@pytest.fixture
def kode(monkeypatch):
    from app.routers import items

    async def kode_berikut(conn, tid, jenis):
        return "BRG-0009"
    monkeypatch.setattr(items, "_kode_otomatis", kode_berikut)
    dibuat = []

    async def palsu(conn, ctx, body, user_id, user_name):
        dibuat.append(body)
        return uuid.uuid4()
    monkeypatch.setattr(items, "tulis_barang_baru", palsu)
    return dibuat


def test_sku_varian_murni():
    assert BV.sku_varian("brg-0007", {"ukuran": "XL", "warna": "Biru Muda"}) == "BRG-0007-XL-BIRUMUDA"
    assert BV.sku_varian("BRG-1", {"ukuran": "3/4"}) == "BRG-1-34"


def test_pratinjau_v1_sku_harga_teks_dan_tanpa_flag_tetap_b1(kode):
    r = asyncio.run(BV.rencana(K(), T, _body(), True))
    assert [v["sku"] for v in r["varian"]] == ["BRG-0009-S-BIRUMUDA", "BRG-0009-XL-BIRUMUDA"]
    assert r["varian"][0]["harga_jual_text"] == "Rp 75.000" and r["jumlah_varian_text"] == "2 varian akan dibuat"
    lama = asyncio.run(BV.rencana(K(), T, _body()))
    assert "sku" not in lama["varian"][0] and "jumlah_varian_text" not in lama


def test_sku_dipakai_jadi_blok_dan_tulis_409_sebelum_menulis(kode):
    k = K(sku_dipakai={"BRG-0009-XL-BIRUMUDA"})
    r = asyncio.run(BV.rencana(k, T, _body(), True))
    assert not r["ok"] and r["blocks"][0]["code"] == "SKU_DIPAKAI" and "BRG-0009-XL-BIRUMUDA" in r["blocks"][0]["message"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(BV.tulis(k, CTX, _body(), "u", "n", True))
    assert e.value.status_code == 409 and not kode


def test_tulis_v1_sku_dari_kode_induk_yang_tertulis_dan_harga_per_varian(kode):
    body = _body(mode_harga="per_varian", harga_varian=[{"atribut": {"Ukuran": "xl", "warna": "Biru Muda"}, "sales_price": 85000}])
    h = asyncio.run(BV.tulis(K(kode_induk="BRG-0010"), CTX, body, "u", "n", True))
    anak = kode[1:]
    assert [b.sku for b in anak] == ["BRG-0010-S-BIRUMUDA", "BRG-0010-XL-BIRUMUDA"]     # dihitung ulang dari kode nyata, bukan tebakan
    assert [b.sales_price for b in anak] == [75000, 85000] and all(b.purchase_price == 40000 for b in anak)
    assert h["varian"][1]["sku"] == "BRG-0010-XL-BIRUMUDA"


def test_harga_varian_tak_cocok_422():
    body = _body(mode_harga="per_varian", harga_varian=[{"atribut": {"ukuran": "M", "warna": "Biru Muda"}, "sales_price": 1}])
    with pytest.raises(HTTPException) as e:
        BV.pasang_harga(BV.rakit("Kaos", BV._dimensi_dict(body.dimensi)), body)
    assert e.value.status_code == 422 and "tidak cocok" in e.value.detail["message"]


def test_tambah_v1_sku_dari_kode_induk():
    k = K()
    h = asyncio.run(BV.rencana_tambah(k, T, INDUK["id"], TambahVarianRequest(dimensi=[{"nama": "ukuran", "nilai": ["XXL"]}]), True))
    assert h["varian"][0]["sku"] == "BRG-0007-XXL" and h["varian"][0]["harga_jual_text"] == "Rp 75.000"


def _anak(harga):
    return {"id": uuid.uuid4(), "nama_produk": f"Kaos {harga}", "sales_price": harga, "purchase_price": 40000}


def test_terapkan_harga_pratinjau_dan_tulis_eksplisit():
    k = K(anak=[_anak(75000), _anak(80000)])
    body = TerapkanHargaRequest(sales_price=80000)
    r = asyncio.run(BV.rencana_harga(k, T, INDUK["id"], body))
    assert r["ok"] and r["jumlah_berubah"] == 1 and r["teks"] == "Harga Kaos diterapkan ke 1 dari 2 varian."
    assert r["baris"][0]["harga_jual_baru_text"] == "Rp 80.000" and r["baris"][0]["harga_beli_baru_text"] == "Rp 40.000"
    assert not k.tulis
    asyncio.run(BV.terapkan_harga(k, CTX, INDUK["id"], body))
    q = [s for s, _ in k.tulis]
    assert "pg_advisory_xact_lock" in q[0]
    assert any("matrix_parent_id = $2" in s and "harga_jual = $3" in s for s in q)
    assert not any("purchase_price = $3" in s and "matrix_parent_id" in s for s in q)   # beli tak dipilih -> varian tak disentuh


def test_terapkan_harga_tanpa_pilihan_422():
    with pytest.raises(HTTPException) as e:
        asyncio.run(BV.terapkan_harga(K(anak=[_anak(1)]), CTX, INDUK["id"], TerapkanHargaRequest(terapkan_jual=False)))
    assert e.value.status_code == 422 and e.value.detail["code"] == "HARGA_TAK_DIPILIH"


def test_flag_gelap_dan_izin():
    assert "_wajib_v1" in inspect.getsource(MR.terapkan_harga) and "_wajib_v1" in inspect.getsource(MR.pratinjau_terapkan_harga)
    assert "per_varian" not in inspect.getsource(MR._v1) or "FITUR_BELUM_AKTIF" in inspect.getsource(MR._v1)
    for p in ("/api/matrix-items/x/harga", "/api/matrix-items/x/harga/preview"):
        assert izin_untuk_jalur(p, "POST") == ("item", "U"), p


def test_picker_autocomplete_menyembunyikan_induk():
    from app.routers import items
    src = inspect.getsource(items)
    i = src.index('@router.get("/items/autocomplete")')
    assert "NOT COALESCE(p.is_matrix_parent, false)" in src[i:i + 4000]
