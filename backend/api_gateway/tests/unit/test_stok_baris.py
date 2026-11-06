"""B1/B2 stok baris (BUG-049/075, 6 Nov 2026): satu penentu services/stok_baris untuk pemilih barang (POST /api/inventory/stok-baris)
dan isyarat_stok di Tinjau SO / Faktur / terbit draf / konversi SO -> faktur.

stok_tanggal & stok_kini dari inventory_ledger (bukan warehouse_stock; reserved_quantity tak pernah ditulis), dipesan = qty baris SO
terbuka - terkirim (satuan dasar), bisa_dijual = stok_kini - dipesan. Isyarat BUKAN blok; satu kalimat per barang ber-lacak-stok.
"""
import asyncio
import inspect
import os
import re
import uuid
from datetime import date
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import stok_baris as SB  # noqa: E402

T = "t-uji"
P, J = uuid.UUID(int=7), uuid.UUID(int=8)        # P = Minyak 1L ber-stok (dasar botol; dus 12, karton 120); J = jasa tanpa lacak
SO1, SO2, SOX, SRC, W = (uuid.UUID(int=i) for i in (21, 22, 23, 24, 31))
L1, L2, L3 = uuid.UUID(int=41), uuid.UUID(int=42), uuid.UUID(int=43)
KTX = {"alias": {}, "barang": {str(P): {"nama": "Minyak 1L", "dasar": "botol", "konversi": {"dus": D("12"), "karton": D("120")}},
                               str(J): {"nama": "Ongkos", "dasar": "kali", "konversi": {}}}}
HARI = date(2026, 11, 6)


def _jalan(c):
    return asyncio.run(c)


class _K:
    def __init__(self, stok=None, so=None):
        self.stok = stok if stok is not None else [{"product_id": P, "stok_tanggal": D("0"), "stok_kini": D("211")}]
        self.so = so if so is not None else [
            {"so_id": SO1, "warehouse_id": None, "soi_id": L1, "item_id": P, "quantity": D("2"), "unit": "dus"},
            {"so_id": SO2, "warehouse_id": W, "soi_id": L2, "item_id": P, "quantity": D("5"), "unit": "botol"}]
        self.args = {}

    async def fetch(self, sql, *a):
        if sql == SB.SQL_STOK:
            self.args["stok"] = a
            return self.stok
        if sql == SB.SQL_SO_TERBUKA:
            self.args["so"] = a
            return self.so
        if sql == SB.SQL_BARANG:
            return [{"id": P, "nama_produk": "Minyak 1L", "lacak": True}, {"id": J, "nama_produk": "Ongkos", "lacak": False}]
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def pasang(monkeypatch):
    async def muat(conn, tid, ids):
        return KTX
    monkeypatch.setattr(SB.SD, "muat", muat)
    kirim = {"per_baris": {L1: D("1")}, "tanpa_tautan": {}}

    async def terkirim(conn, tid, so_ids):
        return ({k: v for k, v in kirim["per_baris"].items()}, kirim["tanpa_tautan"])
    monkeypatch.setattr(SB, "terkirim_per_baris", terkirim)
    return kirim


# ---------------- penentu
def test_stok_dari_ledger_dipesan_sisa_so_dalam_satuan_dasar_bisa_dijual():
    k = _K()
    h = _jalan(SB.hitung(k, T, [P, J], date(2026, 10, 31)))
    p = h["barang"][str(P)]
    # SO1: 2 dus - 1 dus terkirim = 1 dus = 12 botol; SO2: 5 botol -> dipesan 17; bisa dijual = 211 - 17
    assert (p["stok_tanggal"], p["stok_kini"], p["dipesan"], p["bisa_dijual"]) == (D("0"), D("211"), D("17"), D("194"))
    assert h["barang"][str(J)]["stok_kini"] is None and h["barang"][str(J)]["lacak_stok"] is False  # jasa: null, bukan 0
    assert k.args["stok"][2:] == (date(2026, 10, 31), None, []) and h["catatan"] == []
    assert k.args["so"][2] == ["draft", "cancelled", "completed"] and k.args["so"][3] is None


def test_gudang_hanya_so_bergudang_sama_dan_catatan_so_tanpa_gudang():
    k = _K()
    h = _jalan(SB.hitung(k, T, [P], HARI, warehouse_id=W))
    assert h["barang"][str(P)]["dipesan"] == D("5") and k.args["stok"][3] == W
    assert h["catatan"] == ["1 pesanan tanpa gudang tidak dihitung di gudang ini; lihat semua gudang."]


def test_kecuali_so_dan_kecuali_sumber_diteruskan_ke_sql():
    k = _K()
    _jalan(SB.hitung(k, T, [P], HARI, kecuali_so=SOX, kecuali_sumber=[SRC]))
    assert k.args["so"][3] == SOX and k.args["stok"][4] == [SRC]


def test_kirim_tanpa_tautan_dan_satuan_so_tak_terkonversi_jadi_catatan_sekali(pasang):
    pasang["tanpa_tautan"] = {SO1: D("3")}
    k = _K(so=[{"so_id": SO1, "warehouse_id": None, "soi_id": L1, "item_id": P, "quantity": D("2"), "unit": "pak"},
               {"so_id": SO2, "warehouse_id": None, "soi_id": L3, "item_id": P, "quantity": D("4"), "unit": "pak"}])
    h = _jalan(SB.hitung(k, T, [P], HARI))
    assert h["barang"][str(P)]["dipesan"] == D("0")  # pak tak terkonversi -> tak dijumlahkan (bukan 1:1 karangan)
    assert h["catatan"] == ["Sebagian pengiriman pesanan tidak bertaut ke baris pesanan; jumlah dipesan bisa lebih besar dari sebenarnya.",
                            "Satuan pak untuk Minyak 1L belum punya konversi ke botol. Atur konversinya di Barang › Satuan."]


def test_baris_b1_dalam_satuan_diminta_dan_blok():
    h = _jalan(SB.hitung(_K(), T, [P, J], HARI))
    dus = SB.baris(h, P, "dus")
    assert (dus["satuan"], dus["faktor"], dus["stok_kini"], dus["dipesan"], dus["bisa_dijual"], dus["blok"]) == (
        "dus", "12", "17.5833", "1.4167", "16.1667", None)
    dasar = SB.baris(h, P, None)
    assert (dasar["satuan"], dasar["faktor"], dasar["stok_kini"], dasar["stok_tanggal"]) == ("botol", "1", "211", "0")
    assert SB.baris(h, P, "pak")["blok"]["code"] == "SATUAN_TAK_TERKONVERSI" and SB.baris(h, P, "pak")["stok_kini"] is None
    assert SB.baris(h, J, None)["lacak_stok"] is False and SB.baris(h, J, None)["stok_kini"] is None
    assert SB.baris(h, uuid.UUID(int=99), None)["blok"]["code"] == "BARANG_TIDAK_DITEMUKAN"


# ---------------- isyarat (B2)
def test_isyarat_tanggal_mundur_stok_per_tanggal():
    r = _jalan(SB.isyarat(_K(), T, date(2026, 10, 31), HARI, [{"item_id": P, "quantity": 1, "unit": None}]))
    assert r == [{"item_id": str(P), "kalimat": "Stok Minyak 1L per 31 Okt 2026: 0 botol (hari ini 211)."}]


def test_isyarat_bisa_dijual_satu_kalimat_per_barang_dalam_satuan_baris():
    lines = [{"item_id": P, "quantity": 10, "unit": "dus"}, {"item_id": P, "quantity": 8, "unit": "Dus"}]  # 216 botol > 194
    r = _jalan(SB.isyarat(_K(), T, HARI, HARI, lines))
    assert r == [{"item_id": str(P), "kalimat": "Bisa dijual Minyak 1L: 16,17 dus (dipesan 1,42), diminta 18 dus."}]


def test_isyarat_campur_satuan_pakai_dasar_jasa_dan_cukup_tanpa_kalimat():
    lines = [{"item_id": P, "quantity": 16, "unit": "dus"}, {"item_id": P, "quantity": 3, "unit": "botol"}, {"item_id": J, "quantity": 99}]
    r = _jalan(SB.isyarat(_K(), T, HARI, HARI, lines))  # 195 botol > 194
    assert r == [{"item_id": str(P), "kalimat": "Bisa dijual Minyak 1L: 194 botol (dipesan 17), diminta 195 botol."}]
    assert _jalan(SB.isyarat(_K(), T, HARI, HARI, [{"item_id": P, "quantity": 194, "unit": None}])) == []
    # hari ini (bukan mundur): stok per tanggal tak dipakai walau qty > stok_tanggal
    assert _jalan(SB.isyarat(_K(), T, HARI, HARI, [{"item_id": P, "quantity": 5, "unit": None}])) == []


# ---------------- B1 rute + izin + kabel B2
def test_rute_stok_baris_readonly_bentuk_respons_dan_422(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import inventory as INV
    ro = []

    class _Tx:
        def __init__(self, r): ro.append(r)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _C(_K):
        def transaction(self, readonly=False): return _Tx(readonly)

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s): return _C()
                async def __aexit__(s, *a): return False
            return _A()

    async def gp(): return _P()

    async def tgl(conn, tid): return HARI
    monkeypatch.setattr(INV, "get_pool", gp)
    monkeypatch.setattr(INV, "tanggal_dokumen", tgl)
    monkeypatch.setattr(INV, "_tenant", lambda r: T)
    app = FastAPI()
    app.include_router(INV.router, prefix="/api/inventory")
    c = TestClient(app)
    r = c.post("/api/inventory/stok-baris", json={"items": [{"item_id": str(P), "unit": "dus"}, {"item_id": str(J)}]})
    assert r.status_code == 200 and ro == [True]
    d = r.json()["data"]
    assert d["tanggal"] == "2026-11-06" and d["warehouse_id"] is None and d["catatan"] == []
    assert [(x["item_id"], x["satuan"], x["bisa_dijual"]) for x in d["lines"]] == [(str(P), "dus", "16.1667"), (str(J), "kali", None)]
    assert c.post("/api/inventory/stok-baris", json={"items": [{"item_id": "bukan-uuid"}]}).status_code == 422
    assert c.post("/api/inventory/stok-baris", json={"items": []}).status_code == 422


def test_izin_rute_stok_baris_baca_barang():
    from app.middleware.permission_middleware import ROUTE_PERMISSIONS
    cocok = [(m, lv) for pat, met, m, lv in ROUTE_PERMISSIONS if "POST" in met and re.match(pat, "/api/inventory/stok-baris")]
    assert cocok[0] == ("item", "R")


def test_empat_tinjau_memanggil_penentu_yang_sama():
    from app.routers import sales_invoices as SI
    from app.routers import sales_orders as SO
    for fn in (SO.calculate_sales_order, SO._baca_pratinjau, SI.calculate_invoice, SI._pratinjau_dari_posting):
        assert "SB.isyarat(" in inspect.getsource(fn), fn.__name__
