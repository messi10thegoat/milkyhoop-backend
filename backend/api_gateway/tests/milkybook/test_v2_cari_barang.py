"""Milkybook v2 cari barang (03 §3 baris 2): parameter kueri, harga master, ukuran dari varian master, rute ber-flag.
SQL-nya sendiri diukur di DB nyata (baca saja; bukti di laporan unit), bukan di suite unit (nol DB)."""
import json
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.milkybook.v2 import cari_barang as B
from app.milkybook.v2 import cari_pelanggan as C
from app.milkybook.v2.router import buat_router_v2


def test_parameter_sama_dengan_cari_pelanggan():
    assert B.parameter("t1", "  Kaos  hitam_% ", 6) == C.parameter("t1", "  Kaos  hitam_% ", 6)
    assert B.parameter("t1", "", 4)[1] == [] and B.parameter("t1", "kaos", 4)[4] is True


def test_rupiah_bulat_atau_teks_desimal_persis():
    assert B.rupiah(Decimal("75000.00")) == 75000
    assert B.rupiah(Decimal("1250.50")) == "1250.5"
    assert B.rupiah("0.10") == "0.1"
    assert B.rupiah(None) is None


def test_label_ukuran_kunci_size_atau_ukuran_tanpa_peka_huruf():
    assert B.label_ukuran({"Size": "xl"}) == "XL"
    assert B.label_ukuran({"ukuran": " m "}) == "M"
    assert B.label_ukuran('{"UKURAN": "3xl", "warna": "Hitam"}') == "3XL"
    assert B.label_ukuran({"warna": "Hitam"}) is None
    assert B.label_ukuran({"size": ""}) is None
    assert B.label_ukuran(None) is None


def test_ukuran_urut_baku_lalu_abjad_dan_harga_per_ukuran_dari_master():
    varian = [{"id": "v3", "nama": "Kaos XXL", "atribut": {"size": "XXL"}, "harga": "85000.00"},
              {"id": "v1", "nama": "Kaos S", "atribut": {"size": "S"}, "harga": "75000.00"},
              {"id": "v9", "nama": "Kaos Anak 8", "atribut": {"size": "8"}, "harga": 60000},
              {"id": "v2", "nama": "Kaos M", "atribut": {"size": "M"}, "harga": 75000},
              {"id": "vw", "nama": "Kaos Merah", "atribut": {"warna": "Merah"}, "harga": 75000}]
    hasil = B.ukuran(json.dumps(varian))
    assert [u["ukuran"] for u in hasil] == ["S", "M", "XXL", "8"]          # varian tanpa ukuran tidak masuk
    assert [u["harga"] for u in hasil] == [75000, 75000, 85000, 60000]   # harga varian master, bukan rumus
    assert hasil[0] == {"ukuran": "S", "id": "v1", "nama": "Kaos S", "harga": 75000}
    assert B.ukuran(None) == [] and B.ukuran([]) == []


BARIS = {"id": "p1", "nama": "Kaos Combed 30s Hitam", "kode": "BRG-001", "satuan": "pcs", "jenis": "goods",
         "harga": Decimal("75000.00"), "kelas": 0, "mirip": 0, "skor": 4, "varian": None}


def test_baris_ke_hasil_tanpa_varian_ukuran_kosong():
    lama = {"id": "p1", "nama": "Kaos Combed 30s Hitam", "kode": "BRG-001", "satuan": "pcs",
            "jenis": "goods", "harga": 75000, "ukuran": [], "skor": 4, "mirip": False}
    # 12 Nov (BUG-159 B1 cari-varian): kunci `varian` ADITIF pada jawaban API (bawaan); tool LLM memanggil dengan_varian=False -> bentuk lama persis
    assert B.baris_ke_hasil(BARIS) == {**lama, "varian": []}
    assert B.baris_ke_hasil(BARIS, dengan_varian=False) == lama
    assert B.baris_ke_hasil({**BARIS, "kelas": 2})["mirip"] is True


def test_sql_tenant_aktif_barang_dasar_dan_skor_mengecualikan_void_batal():
    assert "p.status = 'active' AND p.deleted_at IS NULL AND p.matrix_parent_id IS NULL" in B.SQL
    assert "x.status = 'active' AND x.deleted_at IS NULL" in B.SQL                # varian nonaktif tak ditawarkan
    assert "h.status <> 'void'" in B.SQL and "h.status <> 'cancelled'" in B.SQL
    assert B.SQL.count("tenant_id = $1") == 6                                       # tiap tabel tersaring tenant
    assert "sales_price" in B.SQL and "purchase_price" not in B.SQL                # harga beli tak bocor ke obrolan


class _Conn:
    def __init__(self, baris):
        self.baris, self.panggil = baris, []

    async def fetch(self, sql, *args):
        self.panggil.append((sql, args))
        return self.baris


def _app(flag=True, baris=()):
    conn = _Conn(list(baris))

    @asynccontextmanager
    async def koneksi():
        yield conn

    async def flag_fn(t):
        return flag

    app = FastAPI()

    @app.middleware("http")
    async def pengguna(request: Request, lanjut):
        t = request.headers.get("X-Uji-Tenant")
        if t:
            request.state.user = {"tenant_id": t, "user_id": "u1"}
        return await lanjut(request)

    app.include_router(buat_router_v2(flag_fn=flag_fn, conn_fn=koneksi, izin_fn=None), prefix="/api/milkybook/v2")
    return TestClient(app), conn


def test_rute_flag_mati_404_tanpa_token_401():
    c, conn = _app(flag=False)
    assert c.get("/api/milkybook/v2/barang?q=kaos", headers={"X-Uji-Tenant": "t1"}).status_code == 404
    assert c.get("/api/milkybook/v2/barang?q=kaos").status_code == 401
    assert conn.panggil == []


def test_rute_tenant_dari_token_batas_dan_isi():
    c, conn = _app(baris=[BARIS])
    r = c.get("/api/milkybook/v2/barang?q=kaos", headers={"X-Uji-Tenant": "kaos"})
    assert r.status_code == 200
    assert r.json()["items"][0]["harga"] == 75000
    sql, args = conn.panggil[0]
    assert sql == B.SQL and args[0] == "kaos" and args[-1] == 6
    assert c.get("/api/milkybook/v2/barang?batas=11", headers={"X-Uji-Tenant": "kaos"}).status_code == 422
    assert c.get("/api/milkybook/v2/barang?q=" + "a" * 81, headers={"X-Uji-Tenant": "kaos"}).status_code == 422
