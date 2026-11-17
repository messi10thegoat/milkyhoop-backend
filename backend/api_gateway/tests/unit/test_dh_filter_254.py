"""BUG-254 (16 Nov 2026): saringan Kategori/Pemasok/Merek untuk Ubah harga massal D3 + GET /{id}/barang-pilihan + batas barang `harga` 500."""
import asyncio
import inspect
import pathlib
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.routers import bulk_price_lists as BP
from app.services import bulk
from app.services import harga_filter as HF

GW = pathlib.Path(__file__).resolve().parents[2]
PL_ID, TID = uuid4(), "t-1"
V1, V2 = str(uuid4()), str(uuid4())


# ---------------------------------------------------------------- bersihkan
def test_bersihkan_rapikan_spasi_huruf_kecil_dan_unik():
    f = HF.bersihkan([" Minyak  Goreng ", "minyak goreng", "Beras"], [V1, V1, V2], ["  ABC ", "abc"])
    assert f == {"kategori": ["minyak goreng", "beras"], "merek": ["abc"], "pemasok": [V1, V2]}
    assert HF.ada_filter(f) and not HF.ada_filter(HF.bersihkan(None, None, None)) and not HF.ada_filter(HF.bersihkan([" "], [""], []))


def test_bersihkan_pemasok_bukan_uuid_dan_terlalu_banyak_ditolak_berkalimat():
    with pytest.raises(HTTPException) as e:
        HF.bersihkan(None, ["bukan-uuid"], None)
    assert e.value.detail["code"] == "FILTER_PEMASOK_TIDAK_SAH" and e.value.status_code == 400
    assert HF.BATAS_FILTER == 50                                                 # angka kontrak (kontrak FE: maksimal 50 nilai per saringan)
    with pytest.raises(HTTPException) as e2:
        HF.bersihkan([f"k{i}" for i in range(HF.BATAS_FILTER + 1)], None, None)
    assert e2.value.detail["code"] == "FILTER_TERLALU_BANYAK"


# ---------------------------------------------------------------- syarat SQL
def test_syarat_gabungan_and_urutan_parameter_dan_di_daftar():
    f = HF.bersihkan(["a"], [V1], ["m"])
    sql, p, idx = HF.syarat(f, PL_ID, True, 2)
    assert "lower(p.kategori) = ANY($2::text[])" in sql and "p.preferred_vendor_id = ANY($3::uuid[])" in sql and "lower(p.merek) = ANY($4::text[])" in sql
    assert "AND EXISTS (SELECT 1 FROM price_list_items pli WHERE pli.price_list_id = $5" in sql and p == [["a"], [V1], ["m"], PL_ID] and idx == 6
    assert "p.status = 'active'" not in sql                                     # barang di daftar: status apa pun (ubah massal)
    s2, _, _ = HF.syarat(f, PL_ID, False, 2)
    assert "NOT EXISTS (SELECT 1 FROM price_list_items" in s2 and "p.status = 'active' AND COALESCE(p.for_sales, true)" in s2
    s3, p3, i3 = HF.syarat(HF.bersihkan(["a"], None, None), PL_ID, None, 2)
    assert "price_list_items" not in s3 and p3 == [["a"]] and i3 == 3 and "p.status = 'active'" in s3


def test_syarat_menyentuh_kolom_merek_hanya_bila_dipakai():
    s, _, _ = HF.syarat(HF.bersihkan(["a"], [V1], None), PL_ID, True, 2)
    assert "merek" not in s                                                     # deployable sebelum V502 selama saringan merek tak dipakai


# ---------------------------------------------------------------- resolusi (konn palsu)
class Konn:
    def __init__(self, rows):
        self.rows, self.sql, self.args = rows, [], []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return self.rows


def test_resolusi_tanpa_filter_kosong_nol_kueri_dan_dengan_filter_bersaring_tenant_urut_nama_batas():
    c = Konn([{"id": uuid4()}, {"id": uuid4()}])
    assert asyncio.run(HF.resolusi(c, TID, PL_ID, HF.bersihkan(None, None, None))) == [] and c.sql == []
    f = HF.bersihkan(["a"], None, None)
    r = asyncio.run(HF.resolusi(c, TID, PL_ID, f, True, 500))
    assert len(r) == 2 and "p.tenant_id = $1" in c.sql[0] and "ORDER BY p.nama_produk, p.id LIMIT 501" in c.sql[0] and c.args[0][0] == TID


# ---------------------------------------------------------------- batas + skema + rute + izin
def test_batas_harga_500_dipakai_ubah_massal_dan_batas_lain_tak_berubah():
    assert bulk.BATAS["harga"] == 500 and bulk.BATAS["tulis"] == 50
    ids = [str(uuid4()) for _ in range(501)]
    with pytest.raises(HTTPException) as e:
        bulk.validasi_ids(ids, "harga")
    assert e.value.detail["code"] == "BULK_TERLALU_BANYAK" and e.value.detail["batas"] == 500
    assert len(bulk.validasi_ids(ids[:500], "harga")) == 500
    s = inspect.getsource(BP._siapkan)
    assert '"tulis"' not in s and s.count('"harga"') >= 2


def test_skema_filter_bawaan_di_daftar_true_dan_aditif():
    b = BP.BulkHargaRequest(mode="persen", nilai=10)
    assert b.filter is None and b.item_ids is None
    b2 = BP.BulkHargaRequest(mode="persen", nilai=10, filter={"kategori": ["a"]})
    assert b2.filter.di_daftar is True and b2.filter.pemasok_id == [] and b2.filter.merek == []


def test_filter_kosong_hasil_nol_barang_ditolak_berkalimat_dan_item_ids_menang():
    s = inspect.getsource(BP._siapkan)
    assert "FILTER_TAK_ADA_BARANG" in s
    assert s.index("if body.item_ids:") < s.index("HF.ada_filter(f)")            # item_ids eksplisit mengalahkan filter


def test_pratinjau_mengembalikan_id_terselesaikan_di_payload_untuk_tulis():
    s = inspect.getsource(BP.bulk_price_preview)
    assert 'data["payload"] = {"item_ids": [str(i) for i in ids], "jumlah": len(ids)}' in s


def test_rute_barang_pilihan_terdaftar_izin_baca_dan_memakai_helper_items():
    jalur = {(m, x.path) for x in BP.router.routes for m in x.methods}
    assert ("GET", "/{price_list_id}/barang-pilihan") in jalur
    pm = (GW / "app/middleware/permission_middleware.py").read_text()
    assert 'price\\-lists/[^/]+/barang\\-pilihan$", ["GET"], "price_list", "R"' in pm
    s = inspect.getsource(BP.barang_pilihan)
    assert "from .items import _syarat_cari" in s and "p.tenant_id = $1" in s and "v.tenant_id = p.tenant_id" in s and "HF.syarat(" in s


def test_barang_pilihan_membaca_daftar_pilihan_dari_server_dan_tak_bocor_tenant():
    s = inspect.getsource(BP.barang_pilihan)
    for k in ("kategori_pilihan", "pemasok_pilihan", "merek_pilihan", '"total"', "di_daftar", "jumlah_baris"):
        assert k in s
    assert s.count("WHERE p.tenant_id = $1") >= 5                                  # total, baris, kategori, merek, pemasok: SEMUA bersaring tenant
