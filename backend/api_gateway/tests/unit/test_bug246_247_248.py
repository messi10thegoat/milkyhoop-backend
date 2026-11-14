"""BUG-246/247/248 (triase BACKEND 15 Nov; putusan MASTER): status_text di daftar pemindahan, Transit tak bisa dibuka lewat GET gudang + label server di stok_per_gudang,
pratinjau Kirim/Terima tak menyesatkan, galat PB_* aditif code/message di tingkat atas (detail objek TETAP)."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import stock_transfers as ST
from app.routers import warehouses as WH
from app.services import gudang_daftar as GD
from app.services import gudang_transit as GT
from app.services import pemindahan_barang as PB
from app.services import pemindahan_dua_langkah as D2
from app.services.galat_berkode import GalatDetailObjek

APP = Path(__file__).resolve().parents[2] / "app"
T = "kaos-biru-konveksi"


def jalan(aw):
    return asyncio.run(aw)


# ------------------------------------------------------------------ 246
class KonDaftar:
    def __init__(self, diterima):
        self.diterima, self.q = diterima, []

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        return [{"source_id": k, "diterima": v} for k, v in self.diterima.items()]


def test_teks_daftar_satu_kueri_untuk_in_transit_dan_teks_turunan():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    rows = [{"id": a, "status": "in_transit"}, {"id": b, "status": "in_transit"}, {"id": c, "status": "received"}, {"id": d, "status": "draft"}]
    k = KonDaftar({a: D("4")})
    out = jalan(D2.teks_daftar(k, T, rows))
    assert len(k.q) == 1 and "ANY($3" in k.q[0][0] and set(k.q[0][1][2]) == {a, b}                       # SATU kueri, hanya pemindahan in_transit
    assert out[a] == ("Dikirim · diterima sebagian", True) and out[b] == ("Dikirim", False)
    assert out[c] == ("Diterima", False) and out[d] == ("Draf", False)
    k2 = KonDaftar({})
    assert jalan(D2.teks_daftar(k2, T, [{"id": c, "status": "received"}])) == {c: ("Diterima", False)} and k2.q == []   # tanpa in_transit: nol kueri


def test_daftar_pemindahan_memakai_teks_daftar():
    s = inspect.getsource(ST.list_stock_transfers)
    assert "pd2.teks_daftar(conn, ctx[\"tenant_id\"], rows)" in s and '"status_text": teks[row["id"]][0]' in s and '"diterima_sebagian": teks[row["id"]][1]' in s


# ------------------------------------------------------------------ 247-1
def test_gudang_sistem_tak_terbuka_lewat_get_detail_dan_stok():
    for fn in (WH.get_warehouse, WH.get_warehouse_stock, WH.get_warehouse_stock_value):
        src = inspect.getsource(fn)
        assert "AND NOT is_system" in src, fn.__name__                                                  # 404 sama dgn daftar yang menyembunyikannya


# ------------------------------------------------------------------ 247-2
class KonSPG:
    async def fetch(self, sql, *a):
        assert "w.is_system" in sql
        return [{"warehouse_id": uuid.uuid4(), "nama": "Toko", "kode": "T1", "aktif": True, "is_system": False, "saldo": D("5")},
                {"warehouse_id": uuid.uuid4(), "nama": GT.NAMA, "kode": GT.KODE, "aktif": True, "is_system": True, "saldo": D("3")}]


def test_stok_per_gudang_transit_berlabel_dari_server():
    out = jalan(GD.stok_per_gudang(KonSPG(), T, uuid.uuid4()))
    toko, transit = out
    assert (toko["sistem"], toko["nama_text"], toko["nama"]) == (False, "Toko", "Toko")
    assert (transit["sistem"], transit["nama_text"], transit["nama"]) == (True, "Dalam perjalanan (Transit)", GT.NAMA)   # teks dari server; FE tanpa logika label
    assert GT.LABEL == "Dalam perjalanan (Transit)"


# ------------------------------------------------------------------ 248-a, 248-b
class Kon:
    pass


def _siap(monkeypatch, transit_id, saldo_transit):
    async def hdr(conn, tid, tr):
        return {"id": tr, "transfer_number": "ST-1", "status": "in_transit", "to_warehouse_id": uuid.uuid4()}

    async def ib(conn, tid, tr):
        return [{"item_id": uuid.uuid4()}]

    async def id_transit(conn, tid):
        return transit_id

    async def stok(conn, tid, gid, iid, kunci):
        return saldo_transit
    monkeypatch.setattr(D2.pb, "_header", hdr)
    monkeypatch.setattr(D2, "item_baris", ib)
    monkeypatch.setattr(D2.GT, "id_transit", id_transit)
    monkeypatch.setattr(D2.pb, "_stok", stok)


def test_pratinjau_kirim_tak_menyiratkan_stok_di_tujuan(monkeypatch):
    iid = uuid.uuid4()

    async def rk(conn, tid, h, items, kunci=False):
        return {"blocks": [], "lines": [{"item_id": iid, "quantity": D("3"), "to_before": D("0"), "to_after": D("3"), "available_to": D("0")}],
                "dari": {"id": "a", "name": "A"}, "ke": {"id": "b", "name": "B"}, "total_quantity": D("3"), "total_value": D("0")}
    _siap(monkeypatch, uuid.uuid4(), D("2"))
    monkeypatch.setattr(D2, "rencana_kirim", rk)
    r = jalan(D2.pratinjau_kirim(Kon(), {"tenant_id": T}, uuid.uuid4()))
    ln = r["lines"][0]
    assert ln["to_before"] is None and ln["to_after"] is None and ln["available_to"] is None             # bukan stok tujuan
    assert ln["transit_before"] == D("2") and ln["transit_after"] == D("5")                              # barang masuk Transit


def test_pratinjau_kirim_transit_belum_ada_saldo_nol(monkeypatch):
    async def rk(conn, tid, h, items, kunci=False):
        return {"blocks": [], "lines": [{"item_id": uuid.uuid4(), "quantity": D("3"), "to_before": D("1"), "to_after": D("4"), "available_to": D("1")}],
                "dari": None, "ke": None, "total_quantity": D("3"), "total_value": D("0")}
    _siap(monkeypatch, None, D("9"))
    monkeypatch.setattr(D2, "rencana_kirim", rk)
    ln = jalan(D2.pratinjau_kirim(Kon(), {"tenant_id": T}, uuid.uuid4()))["lines"][0]
    assert ln["transit_before"] == D("0") and ln["transit_after"] == D("3")


def test_pratinjau_terima_dari_transit_ke_gudang_tujuan(monkeypatch):
    tr, tuj = uuid.uuid4(), uuid.uuid4()

    async def rt(conn, tid, h, items, baris, kunci=False):
        return {"blocks": [], "lines": [], "tujuan": {"id": str(tuj), "name": "Gudang B"}, "total_quantity": D("0"), "total_value": D("0"), "tuntas": False}
    _siap(monkeypatch, tr, D("0"))
    monkeypatch.setattr(D2, "rencana_terima", rt)
    r = jalan(D2.pratinjau_terima(Kon(), {"tenant_id": T}, uuid.uuid4(), None))
    assert r["dari"] == {"id": str(tr), "name": GT.NAMA} and r["ke"] == {"id": str(tuj), "name": "Gudang B"}
    out = ST._pv({**r, "ok": True, "blocks": [], "lines": []})
    assert out["from"] == {"id": str(tr), "name": GT.NAMA} and out["to"]["name"] == "Gudang B"


# ------------------------------------------------------------------ 248-c
def test_galat_pb_dua_bentuk_aditif():
    e = PB._blok("PB_TERIMA_QTY_MELEBIHI", "Jumlah melebihi.", 409)
    assert isinstance(e, GalatDetailObjek) and isinstance(e, HTTPException) and e.status_code == 409
    assert e.detail == {"code": "PB_TERIMA_QTY_MELEBIHI", "message": "Jumlah melebihi."}                  # detail objek TETAP persis (FE lama membaca detail.code)
    assert e.badan() == {"detail": {"code": "PB_TERIMA_QTY_MELEBIHI", "message": "Jumlah melebihi."}, "code": "PB_TERIMA_QTY_MELEBIHI", "message": "Jumlah melebihi."}
    with pytest.raises(GalatDetailObjek) as x:
        PB.angkat_blok_pertama({"blocks": [{"code": "PB_X", "message": "m", "status": 422}]})
    assert x.value.detail == {"code": "PB_X", "message": "m"} and x.value.badan()["code"] == "PB_X"
    src = (APP / "services" / "pemindahan_barang.py").read_text()
    assert 'raise HTTPException(status_code=b["status"], detail={"code"' not in src                        # tak ada jalur PB_* lama yang lolos dari bentuk baru
    main = (APP / "main.py").read_text()
    import re
    assert "GalatDetailObjek as _GalatDetailObjek" in main and re.search(r"def _galat_detail_objek_handler.*?content=exc\.badan\(\)", main, re.S)
