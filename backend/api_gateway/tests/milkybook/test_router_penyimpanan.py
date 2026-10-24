"""Paket aktivasi: router (401 tanpa token, tenant HANYA dari token, flag, 409 sesi sibuk) + penyimpanan sesi bersama
(round-trip JSON utuh: blok thinking, kartu, kunci idempotency, hash payload). Nol DB / Redis / LLM sungguhan."""
import asyncio
from datetime import date

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.kartu import StatusKartu, hash_payload
from app.milkybook.inti.penyimpanan import PenyimpananSesiMemori, PenyimpananSesiRedis, SesiSibuk, sesi_dari_dict, sesi_ke_dict
from app.milkybook.inti.putaran import Putaran
from app.milkybook.inti.router import buat_router

from .conftest import ModelPalsu, izin_semua, panggil, teks
from .test_inti import be_standar, registri


class RedisPalsu:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None, px=None, nx=False):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def delete(self, k):
        self.d.pop(k, None)


async def _sesi_berkartu(store):
    m = ModelPalsu([panggil(("buat_x", {"jumlah": 3})), teks("Tinjau.")])
    p = Putaran(registri(), m, AuditMemori(), gap_mendarat=("G1",))
    s = await store.buat("kaos-biru-konveksi", "u1", konteks="Hari ini 2026-10-24.", hari_ini=date(2026, 10, 24))
    await p.giliran(s, "buat 3", be_standar(), izin_semua)
    await store.simpan(s)
    return s


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
async def test_round_trip_sesi_utuh(store_fn):
    store = store_fn()
    s = await _sesi_berkartu(store)
    s2 = await store.ambil("kaos-biru-konveksi", "u1", s.sesi_id)
    assert s2.messages == s.messages and s2.hari_ini == date(2026, 10, 24)
    (k,) = s.kartu.values()
    k2 = s2.kartu[k.kartu_id]
    assert (k2.kunci, k2.badan, k2.payload_hash, k2.status) == (k.kunci, k.badan, hash_payload({"jumlah": 3}), StatusKartu.MENUNGGU)
    assert await store.sesi_untuk_kartu("kaos-biru-konveksi", "u1", k.kartu_id) == s.sesi_id
    assert await store.ambil("tenant-lain", "u1", s.sesi_id) is None          # ruang kunci tenant x pengguna
    assert await store.ambil("kaos-biru-konveksi", "u2", s.sesi_id) is None


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
async def test_kunci_giliran_menolak_berbarengan(store_fn):
    store = store_fn()
    async with store.kunci("t", "u", "s1"):
        with pytest.raises(SesiSibuk):
            async with store.kunci("t", "u", "s1"):
                pass
    async with store.kunci("t", "u", "s1"):  # terlepas sesudah selesai
        pass


def _app(store, flag=True, model=None):
    model = model or ModelPalsu([teks("Halo.")])
    p = Putaran(registri(), model, AuditMemori(), gap_mendarat=("G1",))
    app = FastAPI()

    @app.middleware("http")
    async def auth(request: Request, call_next):
        t = request.headers.get("X-Uji-Tenant")
        if t:
            request.state.user = {"tenant_id": t, "user_id": request.headers.get("X-Uji-User", "u1")}
        return await call_next(request)

    async def flag_fn(tenant):
        return flag

    async def hari(tenant):
        return date(2026, 10, 24)

    be = be_standar()

    class PelaksanaUji:
        def __init__(self, *a, **k):
            pass

        async def kirim(self, *a, **k):
            return await be.kirim(*a, **k)

    import app.milkybook.inti.router as R
    R.PelaksanaASGI = PelaksanaUji
    app.include_router(buat_router(lambda: p, store, app_fn=lambda: app, izin_fn_untuk=lambda u: izin_semua,
                                   flag_fn=flag_fn, hari_ini_fn=hari), prefix="/api/milkybook")
    return app, model


def test_tanpa_token_401():
    app, _ = _app(PenyimpananSesiMemori())
    c = TestClient(app)
    assert c.post("/api/milkybook/sesi").status_code == 401


def test_flag_mati_404():
    app, _ = _app(PenyimpananSesiMemori(), flag=False)
    assert TestClient(app).post("/api/milkybook/sesi", headers={"X-Uji-Tenant": "t1"}).status_code == 404


def test_tenant_hanya_dari_token_sesi_tenant_lain_404_dan_badan_tak_bisa_memilih_tenant():
    app, model = _app(PenyimpananSesiMemori())
    c = TestClient(app)
    sid = c.post("/api/milkybook/sesi", headers={"X-Uji-Tenant": "t1"}).json()["sesi_id"]
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai"}, headers={"X-Uji-Tenant": "t2"}).status_code == 404
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai", "tenant_id": "t2"},
                  headers={"X-Uji-Tenant": "t1"}).status_code == 422          # kunci asing di badan ditolak
    r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai"}, headers={"X-Uji-Tenant": "t1"})
    assert r.status_code == 200 and r.json()["peristiwa"][0]["teks"] == "Halo."
    assert model.permintaan[0]["messages"][0]["content"].startswith("[konteks] Hari ini 2026-10-24.")


def test_sesi_sibuk_409():
    store = PenyimpananSesiMemori()
    app, _ = _app(store)
    c = TestClient(app)
    sid = c.post("/api/milkybook/sesi", headers={"X-Uji-Tenant": "t1"}).json()["sesi_id"]
    store._kunci.add(f"t1:u1:{sid}")                                           # giliran lain sedang memegang kunci
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "x"}, headers={"X-Uji-Tenant": "t1"}).status_code == 409


def test_setuju_kartu_lewat_router_tulis_sekali():
    store = PenyimpananSesiMemori()
    model = ModelPalsu([panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("Tersimpan.")])
    app, _ = _app(store, model=model)
    c = TestClient(app)
    h = {"X-Uji-Tenant": "t1"}
    sid = c.post("/api/milkybook/sesi", headers=h).json()["sesi_id"]
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat"}, headers=h).json()["peristiwa"]
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    assert c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=h).status_code == 200
    ev2 = c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=h).json()["peristiwa"]   # Enter kedua
    assert ev2[0]["jenis"] == "galat"
    assert c.post(f"/api/milkybook/kartu/{kid}/setuju", headers={"X-Uji-Tenant": "t2"}).status_code == 404


def test_pasang_bisa_diimpor_tanpa_db_redis_dan_rute_lengkap():
    from app.milkybook.pasang import milkybook_router
    jalur = sorted((r.path, sorted(r.methods)[0]) for r in milkybook_router().routes)
    assert jalur == [("/kartu/{kartu_id}/setuju", "POST"), ("/kartu/{kartu_id}/tolak", "POST"),
                     ("/sesi", "POST"), ("/sesi/{sesi_id}/pesan", "POST")]
