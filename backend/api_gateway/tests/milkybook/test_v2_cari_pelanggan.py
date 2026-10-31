"""Milkybook v2 cari pelanggan (03 §3 baris 1): parameter kueri, fakta, dan rute ber-flag milkybook_v2.
SQL-nya sendiri diukur di DB nyata (baca saja; bukti di laporan unit), bukan di suite unit (nol DB)."""
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.milkybook.v2 import cari_pelanggan as C
from app.milkybook.v2.router import FLAG, buat_router_v2


def test_flag_v2_terpisah_dari_v1():
    assert FLAG == "milkybook_v2"


def test_parameter_kata_tengah_awal_dan_lolos_wildcard():
    p = C.parameter("t1", "  Tangka  50%_\\ ", 6)
    assert p[0] == "t1"
    assert p[1] == ["%tangka%", "%50\\%\\_\\\\%"]          # tengah kata, wildcard pengguna dinetralkan
    assert p[2] == ["% tangka%", "% 50\\%\\_\\\\%"]         # awal kata (kelas 0)
    assert p[3] == "Tangka 50%_\\" and p[4] is True
    assert p[5:] == [C.AMBANG_MIRIP, C.MAKS_KANDIDAT, C.HARI_SKOR, 6]


def test_mirip_hanya_untuk_ketikan_panjang():
    assert C.parameter("t1", "j", 6)[4] is False
    assert C.parameter("t1", "rok", 6)[4] is False
    assert C.parameter("t1", "tangkawarow", 6)[4] is True


def test_kueri_kosong_tanpa_kata_berarti_pilihan_awal():
    p = C.parameter("t1", "", 6)
    assert p[1] == [] and p[2] == [] and p[4] is False


def test_fakta_pesanan_dulu_lalu_penawaran():
    assert C.fakta(3, 1) == "1 pesanan"
    assert C.fakta(3, 0) == "3 penawaran"
    assert C.fakta(0, 0) == "Belum ada pesanan"


def test_baris_ke_hasil():
    b = {"id": "c1", "nama": "Rocky", "telepon": None, "kelas": 2, "mirip": 0.8,
         "penawaran": 3, "pesanan": 0, "terakhir": date(2026, 10, 28), "skor": 9}
    assert C.baris_ke_hasil(b) == {"id": "c1", "nama": "Rocky", "telepon": None, "fakta": "3 penawaran",
                                   "penawaran": 3, "pesanan": 0, "terakhir": "2026-10-28", "skor": 9, "mirip": True}
    assert C.baris_ke_hasil({**b, "kelas": 0, "terakhir": None})["terakhir"] is None


def test_sql_mengecualikan_void_batal_dan_terhapus():
    assert "status <> 'void'" in C.SQL and "status <> 'cancelled'" in C.SQL
    assert "c.is_active AND c.deleted_at IS NULL" in C.SQL
    assert C.SQL.count("tenant_id = $1") == 3


class _Conn:
    def __init__(self, baris):
        self.baris, self.panggil = baris, []

    async def fetch(self, sql, *args):
        self.panggil.append(args)
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

    app.include_router(buat_router_v2(flag_fn=flag_fn, conn_fn=koneksi), prefix="/api/milkybook/v2")
    return TestClient(app), conn


BARIS = {"id": "c1", "nama": "Justin Tangkawarouw", "telepon": "0812", "kelas": 0, "mirip": 0,
         "penawaran": 0, "pesanan": 1, "terakhir": date(2026, 10, 1), "skor": 5}


def test_rute_flag_mati_404_tanpa_token_401():
    c, conn = _app(flag=False)
    assert c.get("/api/milkybook/v2/pelanggan?q=j", headers={"X-Uji-Tenant": "t1"}).status_code == 404
    assert c.get("/api/milkybook/v2/pelanggan?q=j").status_code == 401
    assert conn.panggil == []


def test_rute_tenant_dari_token_dan_batas():
    c, conn = _app(baris=[BARIS])
    r = c.get("/api/milkybook/v2/pelanggan?q=tangka", headers={"X-Uji-Tenant": "kaos"})
    assert r.status_code == 200
    assert r.json()["items"][0]["fakta"] == "1 pesanan"
    assert conn.panggil[0][0] == "kaos" and conn.panggil[0][-1] == 6
    assert c.get("/api/milkybook/v2/pelanggan?batas=11", headers={"X-Uji-Tenant": "kaos"}).status_code == 422
    assert c.get("/api/milkybook/v2/pelanggan?q=" + "a" * 81, headers={"X-Uji-Tenant": "kaos"}).status_code == 422
