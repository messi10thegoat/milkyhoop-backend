"""BUG-256 (16 Nov 2026): salin daftar harga -- pratinjau == tulis (satu penentu), atomik, idempoten, riwayat 252; header + SEMUA baris apa adanya, bukan bawaan."""
import asyncio
import inspect
import json
import pathlib
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import bulk_price_lists as BP
from app.services import harga_riwayat as HR
from app.services import harga_salin as HS
from app.services.galat_berkode import GalatBerkode

GW = pathlib.Path(__file__).resolve().parents[2]
SRC, BARU, TID = uuid4(), uuid4(), "t-1"
S = {"id": SRC, "code": "GRS", "name": "Grosir", "price_type": "fixed", "default_discount": 0, "default_markup": 0, "start_date": None, "end_date": None,
     "priority": 100, "description": None, "is_active": True}


class Konn:
    def __init__(self, sumber=S, kode_ada=False, n=3, barang=2, berjangka=1, tak_aktif=0):
        self.sumber, self.kode_ada, self.ukur = sumber, kode_ada, {"n": n, "barang": barang, "berjangka": berjangka, "tak_aktif": tak_aktif}
        self.sql, self.exec = [], []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM price_lists WHERE id = $1 AND tenant_id = $2" in sql:
            return self.sumber
        if "count(*) AS n" in sql:
            return self.ukur
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "SELECT 1 FROM price_lists WHERE tenant_id = $1 AND code = $2" in sql:
            return 1 if self.kode_ada else None
        if sql.lstrip().startswith("INSERT INTO price_lists"):
            return BARU
        if sql.lstrip().startswith("WITH ins AS"):
            return self.ukur["n"]
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        self.exec.append((sql, a))


def rencana(c, kode="NEW-1", nama="Salinan", kunci=False):
    return asyncio.run(HS.rencana(c, TID, SRC, kode, nama, kunci))


def test_rencana_ok_memberi_ringkasan_hitungan_dan_teks_dari_server():
    r = rencana(Konn())
    assert r["ok"] and r["blocks"] == [] and r["peringatan"] == []
    x = r["rencana"]
    assert x["sumber"] == {"id": str(SRC), "code": "GRS", "name": "Grosir"} and x["baru"] == {"code": "NEW-1", "name": "Salinan"}
    assert (x["jumlah_baris"], x["jumlah_barang"], x["jumlah_berjangka"]) == (3, 2, 1) and "3 harga (2 barang, 1 berjangka)" in x["teks"] and "bukan bawaan" in x["teks"]


def test_blok_sumber_tak_ada_kode_kosong_ganda_panjang_dan_nama_kosong():
    assert rencana(Konn(sumber=None))["blocks"][0]["code"] == "DH_TAK_ADA"
    k = [b["code"] for b in rencana(Konn(), kode="  ", nama="")["blocks"]]
    assert k == ["KODE_DAFTAR_KOSONG", "NAMA_DAFTAR_KOSONG"]
    g = rencana(Konn(kode_ada=True))
    assert not g["ok"] and g["blocks"][0] == {"code": "KODE_DAFTAR_GANDA", "message": "Kode daftar harga itu sudah dipakai.", "field": "code"} and g["rencana"] is None
    assert rencana(Konn(), kode="K" * 51)["blocks"][0]["code"] == "KODE_DAFTAR_TERLALU_PANJANG"


def test_kode_dan_nama_dirapikan_spasinya_seperti_buat_daftar():
    r = rencana(Konn(), kode="  NEW   1 ", nama=" Salinan   Grosir ")
    assert r["rencana"]["baru"] == {"code": "NEW 1", "name": "Salinan Grosir"}


def test_peringatan_konfirmasi_bila_ada_barang_nonaktif_bentuk_bug207():
    r = rencana(Konn(tak_aktif=2))
    p = r["peringatan"][0]
    assert r["ok"] and p["code"] == "DH_SALIN_BARANG_TIDAK_AKTIF" and p["butuh_konfirmasi"] is True and p["pesan"] == p["message"] and "2 harga" in p["pesan"]


def test_kunci_baris_sumber_hanya_saat_tulis_dan_bersaring_tenant():
    c1, c2 = Konn(), Konn()
    rencana(c1)
    rencana(c2, kunci=True)
    assert "FOR UPDATE" not in c1.sql[0][0] and "FOR UPDATE" in c2.sql[0][0]
    assert all("tenant_id" in q or "pl.tenant_id" in q for q, _ in c1.sql if "price_lists" in q or "price_list_items" in q)


@pytest.fixture
def riwayat(monkeypatch):
    log = []

    async def snap(conn, tid, pl, batas=200):
        return [{"nama": "X"}]

    async def catat(conn, ctx, pl_id, kode, aksi, ringkas, baris=None, header=None, jumlah=None):
        log.append((pl_id, kode, aksi, ringkas, baris, header, jumlah))
    monkeypatch.setattr(HR, "snapshot_daftar", snap)
    monkeypatch.setattr(HR, "catat", catat)
    return log


def test_tulis_insert_select_atomik_bukan_bawaan_dan_menulis_riwayat_buat(riwayat):
    c = Konn()
    r = asyncio.run(HS.tulis(c, {"tenant_id": TID, "user_id": "u1"}, SRC, "NEW-1", "Salinan"))
    assert r == {"id": str(BARU), "code": "NEW-1", "name": "Salinan", "jumlah_baris": 3, "sumber_id": str(SRC)}
    ins_h = next(q for q, _ in c.sql if q.lstrip().startswith("INSERT INTO price_lists"))
    assert "false, $5" in ins_h and "FROM price_lists WHERE id = $1 AND tenant_id = $2" in ins_h                       # is_default=false; sumber bersaring tenant
    ins_i = next(q for q, _ in c.sql if q.lstrip().startswith("WITH ins AS"))
    for kol in ("item_id", "item_code", "unit", "price", "min_quantity", "discount_percent", "markup_percent", "start_date", "end_date", "is_active", "is_default_unit", "min_price"):
        assert f"i.{kol}" in ins_i, kol                                                                              # SEMUA medan baris ikut (berjangka, tingkat, diskon, minimum)
    assert "pl.tenant_id = $3" in ins_i
    assert len(riwayat) == 1 and riwayat[0][0] == BARU and riwayat[0][2] == "BUAT" and "Disalin dari GRS" in riwayat[0][3] and riwayat[0][5]["ke"]["code"] == "NEW-1" and riwayat[0][6] == 3


def test_tulis_blok_menolak_dengan_kode_dan_status_berbeda_nol_tulis(riwayat):
    for c, kode, st, code in ((Konn(sumber=None), "A", 404, "DH_TAK_ADA"), (Konn(kode_ada=True), "A", 409, "KODE_DAFTAR_GANDA"), (Konn(), " ", 400, "KODE_DAFTAR_KOSONG")):
        with pytest.raises(GalatBerkode) as e:
            asyncio.run(HS.tulis(c, {"tenant_id": TID}, SRC, kode, "N"))
        assert (e.value.status_code, e.value.code) == (st, code)
        assert not [q for q, _ in c.sql if q.lstrip().startswith("INSERT")] and riwayat == []


def test_tulis_memvalidasi_ulang_di_bawah_kunci():
    s = inspect.getsource(HS.tulis)
    assert "kunci=True" in s


def test_rute_salin_terdaftar_izin_C_inventaris_rate_limit_dan_kunci_wajib():
    jalur = {(m, x.path) for x in BP.router.routes for m in x.methods}
    assert ("POST", "/{price_list_id}/salin/preview") in jalur and ("POST", "/{price_list_id}/salin") in jalur
    pm = (GW / "app/middleware/permission_middleware.py").read_text()
    assert 'salin(/preview)?$", ["POST"], "price_list", "C"' in pm
    inv = (GW / "tests/unit/inventaris_rute_tulis.txt").read_text()
    assert "POST /api/price-lists/{price_list_id}/salin\n" in inv and "POST /api/price-lists/{price_list_id}/salin/preview\n" in inv
    assert "/api/price-lists/{price_list_id}/salin/preview" in (GW / "app/middleware/rate_limit_middleware.py").read_text()
    s = inspect.getsource(BP.salin_daftar)
    assert "SALIN_KUNCI_WAJIB" in s and 'idem_buat.mulai_aksi(conn, ctx, kunci, "DH_SALIN"' in s and "HS.tulis(conn, ctx, price_list_id, kode, nama)" in s and "UniqueViolationError" in s


def test_pratinjau_read_only_dan_payload_sama_untuk_tulis():
    s = inspect.getsource(BP.pratinjau_salin)
    assert "readonly=True" in s and "tr.rollback()" in s and '"payload": {"code": kode, "name": nama}' in s
    assert BP._badan_salin({"code": " A  B ", "name": " N "}) == ("A B", "N") and BP._badan_salin(None) == ("", "")
