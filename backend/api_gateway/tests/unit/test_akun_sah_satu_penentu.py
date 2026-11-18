"""Akun sah SATU penentu (18 Nov 2026): KAK.akun_sah/daftar_akun dipakai penulis kategori, salin ke barang, GET /items/accounts/<jenis>, dan GET /items/categories/akun-pilihan.
hpp = HANYA COGS (ikut Accurate); semua jenis: aktif + bukan header."""
import asyncio
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.services import kategori_akun as KAK

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def A(i, kode, nama, tipe, aktif=True, header=False):
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "account_code": kode, "name": nama, "account_type": tipe, "is_active": aktif, "is_header": header}


COA = [
    A(1, "4-10000", "Pendapatan (induk)", "REVENUE", header=True), A(2, "4-10100", "Penjualan", "REVENUE"), A(3, "4-20100", "Pendapatan Lain", "OTHER_INCOME"),
    A(4, "4-90000", "Penjualan lama", "REVENUE", aktif=False),
    A(5, "1-10600", "Persediaan Barang", "ASSET"), A(6, "1-10000", "Aset Lancar", "ASSET"), A(7, "1-10700", "Persediaan (induk)", "ASSET", header=True),
    A(8, "5-10100", "Beban Pokok Penjualan", "COGS"), A(9, "5-10200", "HPP Jasa", "EXPENSE"), A(10, "5-00000", "HPP (induk)", "COGS", header=True),
    A(11, "6-10100", "Beban Pembelian", "EXPENSE"), A(12, "5-10300", "Pembelian Bahan", "COGS"), A(13, "6-90000", "Beban nonaktif", "EXPENSE", aktif=False),
]


class Conn:
    """Meniru SQL daftar_akun: tenant ini, aktif, bukan header (saringan SQL), urut kode."""
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return [r for r in sorted(self.rows, key=lambda r: r["account_code"]) if r["is_active"] and not r["is_header"]]


def kodes(jenis):
    return [a["code"] for a in asyncio.run(KAK.daftar_akun(Conn(COA), "t", jenis))]


def test_penjualan_hanya_pendapatan_aktif_detail():
    assert kodes("penjualan") == ["4-10100", "4-20100"]


def test_persediaan_hanya_asset_persediaan_bukan_header():
    assert kodes("persediaan") == ["1-10600"]


def test_hpp_hanya_cogs_bukan_expense():
    assert kodes("hpp") == ["5-10100", "5-10300"]


def test_pembelian_expense_cogs_dan_asset_persediaan():
    assert kodes("pembelian") == ["1-10600", "5-10100", "5-10300", "6-10100", "5-10200"] or sorted(kodes("pembelian")) == sorted(["1-10600", "5-10100", "5-10200", "5-10300", "6-10100"])


@pytest.mark.parametrize("jenis", KAK.JENIS)
def test_daftar_tak_pernah_memuat_header_atau_nonaktif(jenis):
    ids = {a["id"] for a in asyncio.run(KAK.daftar_akun(Conn(COA), "t", jenis))}
    buruk = {a["id"] for a in COA if a["is_header"] or not a["is_active"]}
    assert not ids & buruk


@pytest.mark.parametrize("jenis", KAK.JENIS)
def test_uji_silang_setiap_akun_daftar_lolos_penulis_dan_sebaliknya(jenis):
    """daftar ≡ penulis: akun di daftar lolos rencana_akun; akun sah menurut akun_sah ada di daftar (bila lolos SQL aktif+bukan header)."""
    daftar = {a["id"] for a in asyncio.run(KAK.daftar_akun(Conn(COA), "t", jenis))}
    akun = {a["id"]: a for a in COA}
    kat = {"id": "c1", "name": "K", **{f"akun_{j}": None for j in KAK.JENIS}}
    for a in COA:
        r = KAK.rencana_akun(kat, {jenis: a["id"]}, akun)
        assert r["ok"] == (a["id"] in daftar), (jenis, a["account_code"], r["blocks"])
        assert KAK.akun_sah(jenis, a) == r["ok"]


def test_header_ditolak_semua_jenis_walau_tipenya_cocok():
    for j, a in (("penjualan", COA[0]), ("persediaan", COA[6]), ("hpp", COA[9])):
        assert not KAK.akun_sah(j, a)


def test_hpp_tak_menerima_expense():
    assert not KAK.akun_sah("hpp", COA[8])
    assert KAK.TIPE["hpp"] == ("COGS",)


def test_salin_ke_barang_memakai_penentu(monkeypatch):
    src = (APP / "services" / "kategori_akun.py").read_text()
    assert re.search(r"if a and akun_sah\(j, a\):", src)


def test_endpoint_barang_semua_lewat_satu_penentu():
    s = (APP / "routers" / "items.py").read_text()
    for fn, j in (("list_sales_accounts", "penjualan"), ("list_purchase_accounts", "pembelian"), ("list_inventory_accounts", "persediaan"), ("list_cogs_accounts", "hpp")):
        m = re.search(rf"async def {fn}\(request: Request\):\n(.*?)(?=\n\n\n)", s, re.S)
        assert m and f'_daftar_akun_sah(request, "{j}")' in m.group(1), fn
        assert "FROM chart_of_accounts" not in m.group(1), fn
    assert "KAK.daftar_akun(" in s


def test_rute_akun_pilihan_sebelum_rute_id_kategori():
    s = (APP / "routers" / "items.py").read_text()
    assert s.index('"/items/categories/akun-pilihan"') < s.index('"/items/categories/{category_id}")')


def test_akun_pilihan_jenis_tak_dikenal_422(monkeypatch):
    from app.routers import items as R
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.akun_pilihan_kategori(None, jenis="x"))
    assert e.value.status_code == 422 and e.value.detail["code"] == "AKUN_JENIS_TAK_DIKENAL" and e.value.detail["field"] == "jenis"


def test_akun_pilihan_bentuk_respons(monkeypatch):
    from app.routers import items as R
    from types import SimpleNamespace

    async def konek():
        return Conn(COA)

    class _C(Conn):
        async def close(self):
            pass
    async def konek2():
        return _C(COA)
    monkeypatch.setattr(R, "get_db_connection", konek2)
    monkeypatch.setattr(R, "get_tenant_id", lambda r: "t")
    r = asyncio.run(R.akun_pilihan_kategori(SimpleNamespace(), jenis="hpp"))
    assert [a["text"] for a in r["accounts"]] == ["5-10100 Beban Pokok Penjualan", "5-10300 Pembelian Bahan"]
    assert set(r["accounts"][0]) == {"id", "code", "name", "account_type", "text"}


def test_izin_akun_pilihan_baca_item():
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur("/api/items/categories/akun-pilihan", "GET") == ("item", "R")
