"""Milkybook v2 draf per percakapan (03 §1, V469): pemilik tenant+user, 7 hari + hapus basi saat tulis, versi basi 409,
batas ukuran, rute ber-flag. Semantik SQL terhadap Postgres sungguhan: test_v2_draf_pg.py (kontainer sekali-pakai)."""
import re
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.milkybook.v2 import draf as D
from app.milkybook.v2.router import buat_router_v2

USER = {"tenant_id": "kaos", "user_id": "u1"}
PID = "6f1c2c1e-1111-4222-8333-444455556666"
DRAF = {"stage": "items", "customer": {"name": "Rocky", "isNew": False}, "lines": [{"name": "Kaos", "qty": 2}]}


def test_setiap_sql_tersaring_tenant_dan_pengguna():
    for nama in ("SQL_BUANG_BASI", "SQL_AMBIL", "SQL_SIMPAN", "SQL_VERSI", "SQL_DAFTAR"):
        sql = getattr(D, nama)
        assert re.search(r"tenant_id = \$\d", sql) and re.search(r"user_id = \$\d", sql), nama


def test_umur_tujuh_hari_baca_dan_hapus():
    assert D.UMUR_HARI == 7
    for nama in ("SQL_AMBIL", "SQL_SIMPAN", "SQL_VERSI", "SQL_DAFTAR"):
        assert "diubah >= NOW() - INTERVAL '7 days'" in getattr(D, nama), nama
    assert D.SQL_BUANG_BASI.startswith("DELETE FROM milkybook_v2_percakapan") and "diubah < NOW() - INTERVAL '7 days'" in D.SQL_BUANG_BASI


def test_simpan_bersyarat_versi_dan_menaikkan_versi():
    assert "versi = $4" in D.SQL_SIMPAN and "versi = versi + 1" in D.SQL_SIMPAN


def test_batas_ukuran_sama_dengan_check_migrasi():
    mig = open("../migrations/V469__milkybook_v2_percakapan.sql").read()
    assert f"<= {D.MAKS_BAIT})" in mig
    for kata in ("BEGIN;", "COMMIT;", "CONCURRENTLY"):
        assert kata not in mig


def test_isi_simpan_terlalu_besar_413():
    besar = D.SimpanDraf(versi=1, draf={"stage": "items", "catatan": "x" * (D.MAKS_BAIT + 1)})
    with pytest.raises(HTTPException) as e:
        D.isi_simpan(besar)
    assert e.value.status_code == 413


def test_ringkas_tanpa_harga():
    r = D.ringkas({**DRAF, "lines": [{"name": "Kaos", "qty": 2, "unitPrice": 75000}], "savedQuoteId": "q1"})
    assert r == {"stage": "items", "pelanggan": "Rocky", "jumlah_baris": 1, "tersimpan": True}
    assert D.ringkas(None) is None


class _Conn:
    """Merekam urutan SQL; hasil fetchrow/fetchval diatur per tes."""

    def __init__(self, baris=None, versi_kini=None, daftar=()):
        self.baris, self.versi_kini, self.daftar_baris, self.log = baris, versi_kini, list(daftar), []

    @asynccontextmanager
    async def transaction(self):
        self.log.append("BEGIN")
        yield
        self.log.append("COMMIT")

    async def execute(self, sql, *a):
        self.log.append(("execute", sql, a))

    async def fetchrow(self, sql, *a):
        self.log.append(("fetchrow", sql, a))
        return self.baris

    async def fetchval(self, sql, *a):
        self.log.append(("fetchval", sql, a))
        return self.versi_kini

    async def fetch(self, sql, *a):
        self.log.append(("fetch", sql, a))
        return self.daftar_baris


def _baris(versi=2):
    return {"id": uuid.UUID(PID), "judul": None, "draf": '{"stage": "items"}', "parkir": "[]", "versi": versi,
            "dibuat": None, "diubah": None}


@pytest.mark.asyncio
async def test_simpan_versi_basi_409_dengan_versi_kini():
    conn = _Conn(baris=None, versi_kini=5)
    with pytest.raises(HTTPException) as e:
        await D.simpan(conn, USER, PID, D.SimpanDraf(versi=3, draf=DRAF))
    assert e.value.status_code == 409 and e.value.detail["code"] == "VERSI_BASI" and e.value.detail["versi_kini"] == 5


@pytest.mark.asyncio
async def test_simpan_milik_orang_lain_atau_kedaluwarsa_404():
    conn = _Conn(baris=None, versi_kini=None)
    with pytest.raises(HTTPException) as e:
        await D.simpan(conn, USER, PID, D.SimpanDraf(versi=1, draf=DRAF))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_simpan_buang_basi_dulu_lalu_update_dalam_satu_transaksi():
    conn = _Conn(baris=_baris(2))
    hasil = await D.simpan(conn, USER, PID, D.SimpanDraf(versi=1, draf=DRAF))
    assert hasil["versi"] == 2 and hasil["draf"] == {"stage": "items"}
    langkah = [x if isinstance(x, str) else x[1] for x in conn.log]
    assert langkah == ["BEGIN", D.SQL_BUANG_BASI, D.SQL_SIMPAN, "COMMIT"]
    assert conn.log[1][2] == ("kaos", "u1")
    assert conn.log[2][2][:4] == (uuid.UUID(PID), "kaos", "u1", 1)


@pytest.mark.asyncio
async def test_buat_buang_basi_dulu_lalu_insert_milik_token():
    conn = _Conn(baris=_baris(1))
    await D.buat(conn, USER, D.BuatPercakapan(judul="Penawaran Rocky"))
    assert [x if isinstance(x, str) else x[1] for x in conn.log] == ["BEGIN", D.SQL_BUANG_BASI, D.SQL_BUAT, "COMMIT"]
    assert conn.log[2][2][1:] == ("kaos", "u1", "Penawaran Rocky")


@pytest.mark.asyncio
async def test_ambil_id_bukan_uuid_404_tanpa_db():
    conn = _Conn()
    with pytest.raises(HTTPException) as e:
        await D.ambil(conn, USER, "bukan-uuid")
    assert e.value.status_code == 404 and conn.log == []


def _app(flag=True, conn=None):
    conn = conn or _Conn()

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
            request.state.user = {"tenant_id": t, "user_id": request.headers.get("X-Uji-User", "u1")}
        return await lanjut(request)

    app.include_router(buat_router_v2(flag_fn=flag_fn, conn_fn=koneksi, izin_fn=None), prefix="/api/milkybook/v2")
    return TestClient(app), conn


H = {"X-Uji-Tenant": "kaos"}


def test_rute_flag_mati_404_tanpa_token_401_nol_db():
    c, conn = _app(flag=False)
    for m, u in (("get", "/percakapan"), ("post", "/percakapan"), ("get", f"/percakapan/{PID}"), ("put", f"/percakapan/{PID}")):
        assert c.request(m.upper(), "/api/milkybook/v2" + u, headers=H, json={"versi": 1}).status_code == 404, u
        assert c.request(m.upper(), "/api/milkybook/v2" + u, json={"versi": 1}).status_code == 401, u
    assert conn.log == []


def test_rute_tahap_tak_dikenal_422_tanpa_db():
    c, conn = _app()
    r = c.put(f"/api/milkybook/v2/percakapan/{PID}", headers=H, json={"versi": 1, "draf": {"stage": "ngawur"}})
    assert r.status_code == 422 and conn.log == []
    r = c.put(f"/api/milkybook/v2/percakapan/{PID}", headers=H, json={"versi": 1, "parkir": [DRAF] * (D.MAKS_PARKIR + 1)})
    assert r.status_code == 422 and conn.log == []


def test_rute_pemilik_dari_token_bukan_badan():
    c, conn = _app(conn=_Conn(baris=_baris(2)))
    r = c.put(f"/api/milkybook/v2/percakapan/{PID}", headers={**H, "X-Uji-User": "u9"},
              json={"versi": 1, "draf": DRAF, "tenant_id": "lain", "user_id": "u1"})
    assert r.status_code == 200
    assert conn.log[2][2][1:3] == ("kaos", "u9")


def test_rute_daftar_ringkas():
    baris = {"id": uuid.UUID(PID), "judul": "x", "draf": DRAF, "n_parkir": 1, "versi": 3, "dibuat": None, "diubah": None}
    c, conn = _app(conn=_Conn(daftar=[baris]))
    r = c.get("/api/milkybook/v2/percakapan", headers=H)
    assert r.json()["items"][0]["draf"] == {"stage": "items", "pelanggan": "Rocky", "jumlah_baris": 1, "tersimpan": False}
    assert conn.log[0][2] == ("kaos", "u1")
