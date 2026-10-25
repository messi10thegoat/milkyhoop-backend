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

    # list / set (kotak masuk #10): semantik redis-py asyncio (decode_responses=True)
    async def rpush(self, k, v):
        self.d.setdefault(k, []).append(v)
        return len(self.d[k])

    async def lrange(self, k, a, b):
        x = self.d.get(k, [])
        return list(x[a:] if b == -1 else x[a:b + 1])

    async def lrem(self, k, n, v):
        x = self.d.get(k, [])
        if v in x:
            x.remove(v)
            return 1
        return 0

    async def lpop(self, k):
        x = self.d.get(k, [])
        return x.pop(0) if x else None

    async def sadd(self, k, v):
        self.d.setdefault(k, set()).add(v)

    async def sismember(self, k, v):
        return v in self.d.get(k, set())

    async def expire(self, k, s):
        return True


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


def _app(store, flag=True, model=None, kembalikan_be=False):
    model = model or ModelPalsu([teks("Halo.")])
    audit = AuditMemori()
    p = Putaran(registri(), model, audit, gap_mendarat=("G1",))
    app = FastAPI()
    app.state_audit = audit

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
    return (app, be) if kembalikan_be else (app, model)


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


def test_sesi_sibuk_pesan_antre_202_kartu_409():
    # #10: pesan saat sibuk = kotak masuk (202); Setuju/Tolak berbarengan tetap 409.
    store = PenyimpananSesiMemori()
    app, _ = _app(store)
    c = TestClient(app)
    sid = c.post("/api/milkybook/sesi", headers={"X-Uji-Tenant": "t1"}).json()["sesi_id"]
    store._kunci.add(f"t1:u1:{sid}")                                           # giliran lain sedang memegang kunci
    r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "x", "id_pesan": "abcd1234"}, headers={"X-Uji-Tenant": "t1"})
    assert r.status_code == 202 and r.json() == {"antre": True, "posisi": 1, "id_pesan": "abcd1234"}
    store._kartu[f"t1:u1:k1"] = sid
    assert c.post("/api/milkybook/kartu/k1/setuju", headers={"X-Uji-Tenant": "t1"}).status_code == 409


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
    assert jalur == [("/kartu/{kartu_id}/setuju", "POST"), ("/kartu/{kartu_id}/tolak", "POST"), ("/kartu/{kartu_id}/ubah", "POST"),
                     ("/sesi", "POST"), ("/sesi/{sesi_id}", "GET"), ("/sesi/{sesi_id}/antre/{id_pesan}", "DELETE"),
                     ("/sesi/{sesi_id}/berhenti", "POST"), ("/sesi/{sesi_id}/pesan", "POST")]


# ---------------------------------------------------------------- #9 galat API model ramah (ukur 25 Okt: dulu 500 mentah)

def _galat_sdk(kelas, status):
    import anthropic
    import httpx

    def lempar(*_):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        if kelas is anthropic.APITimeoutError:
            raise anthropic.APITimeoutError(request=req)
        raise kelas("galat uji", response=httpx.Response(status, request=req), body=None)
    return lempar


@pytest.mark.parametrize("kelas,status,kode", [
    ("OverloadedError", 529, "model_sibuk"), ("InternalServerError", 500, "model_sibuk"),
    ("RateLimitError", 429, "model_batas"), ("APITimeoutError", 0, "model_koneksi"), ("BadRequestError", 400, "model_lain")])
def test_galat_sdk_di_giliran_jadi_peristiwa_ramah_bukan_500(kelas, status, kode):
    import anthropic
    store = PenyimpananSesiMemori()
    m = ModelPalsu([_galat_sdk(getattr(anthropic, kelas), status), teks("Pulih.")])
    app, _ = _app(store, model=m)
    c = TestClient(app, raise_server_exceptions=False)
    H = {"X-Uji-Tenant": "t1"}
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai"}, headers=H)
    assert r.status_code == 200
    ev = r.json()["peristiwa"]
    g = [e for e in ev if e["jenis"] == "galat"]
    assert g and g[0]["kode"] == kode and "Asisten" in g[0]["pesan"] and ev[-1]["jenis"] == "selesai"
    r2 = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai lagi"}, headers=H)  # kunci lepas, sesi lanjut
    assert r2.status_code == 200 and r2.json()["peristiwa"][0]["teks"] == "Pulih."


def test_galat_bukan_sdk_json_tetap_500_sse_galat_internal_keduanya_log_dan_audit(caplog):
    # Putusan #9 + syarat MASTER #10: JSON tetap 500 untuk bug non-SDK; SSE = galat kode internal (stream tak putus
    # diam). Keduanya: Traceback di log + audit model "galat:internal:<jenis>"; kunci lepas, sesi lanjut.
    def bug(*_):
        raise ValueError("bug kode")
    store = PenyimpananSesiMemori()
    app, _ = _app(store, model=ModelPalsu([bug, bug, teks("Pulih.")]))
    c = TestClient(app, raise_server_exceptions=False)
    H = {"X-Uji-Tenant": "t1"}
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    with caplog.at_level("ERROR"):
        r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai"}, headers=H)
    assert r.status_code == 500
    with c.stream("POST", f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "hai lagi"},
                  headers={**H, "Accept": "text/event-stream"}) as rs:
        badan = "".join(rs.iter_text())
    blok = [x for x in badan.split("\n\n") if x.startswith("event:")]
    assert rs.status_code == 200 and '"kode": "internal"' in blok[-2] and blok[-1].startswith("event: selesai")
    assert '"galat": true' in blok[-1]
    assert sum(1 for x in caplog.records if "giliran gagal" in x.message and x.exc_info) == 2
    galat = [b for b in app.state_audit.baris if b.peristiwa == "model" and (b.hasil or "").startswith("galat:internal")]
    assert [b.hasil for b in galat] == ["galat:internal:ValueError"] * 2
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "lagi"}, headers=H).status_code == 200  # kunci lepas


def test_galat_model_sesudah_setuju_menegaskan_sudah_tersimpan_dan_setuju_ulang_jujur():
    import anthropic
    from app.milkybook.inti.putaran import PESAN_SESUDAH_SETUJU
    store = PenyimpananSesiMemori()
    m = ModelPalsu([panggil(("buat_x", {"jumlah": 3})), teks("Tinjau."), _galat_sdk(anthropic.OverloadedError, 529)])
    app, _ = _app(store, model=m)
    c = TestClient(app, raise_server_exceptions=False)
    H = {"X-Uji-Tenant": "t1"}
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat 3"}, headers=H).json()["peristiwa"]
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    r = c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=H)
    assert r.status_code == 200
    ev = r.json()["peristiwa"]
    assert ev[0]["jenis"] == "kartu_status" and ev[0]["status"] == "selesai"
    assert any(e["jenis"] == "galat" and e["pesan"].startswith(PESAN_SESUDAH_SETUJU) for e in ev)
    ulang = c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=H).json()["peristiwa"][0]
    assert ulang["kode"] == "kartu_sudah_disetujui" and ulang["hasil"] == {"id": "baru1", "total": 9000000}
    assert "tidak ada yang ditulis" not in ulang["pesan"]


def test_pilah_galat_sdk():
    import anthropic
    import httpx
    from app.milkybook.inti.model import pilah_galat_sdk
    req = httpx.Request("POST", "https://x")
    assert pilah_galat_sdk(anthropic.RateLimitError("x", response=httpx.Response(429, request=req), body=None)).jenis == "batas"
    assert pilah_galat_sdk(anthropic.APIConnectionError(request=req)).jenis == "koneksi"
    assert pilah_galat_sdk(anthropic.ServiceUnavailableError("x", response=httpx.Response(503, request=req), body=None)).jenis == "sibuk"
    assert pilah_galat_sdk(anthropic.AuthenticationError("x", response=httpx.Response(401, request=req), body=None)).jenis == "lain"
    assert pilah_galat_sdk(ValueError("x")) is None


# ---------------------------------------------------------------- #7 pulihkan percakapan

def test_pulihkan_percakapan_urut_dan_tenant_aman():
    store = PenyimpananSesiMemori()
    m = ModelPalsu([panggil(("buat_x", {"jumlah": 3})), teks("Tinjau."), teks("Tersimpan.")])
    app, _ = _app(store, model=m)
    c = TestClient(app)
    H = {"X-Uji-Tenant": "t1"}
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    kosong = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
    assert kosong == {"sesi_id": sid, "peristiwa": [], "kartu_menunggu": None, "sedang_bekerja": False, "antre": [],
                      "konteks": {"token": 0, "batas": 60000, "persen": 0}}
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat 3"}, headers=H).json()["peristiwa"]
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    tengah = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
    assert tengah["kartu_menunggu"] == kid
    assert tengah["peristiwa"][0] == {"jenis": "pengguna", "teks": "buat 3"}
    assert tengah["peristiwa"][1:] == [e for e in ev if e["jenis"] != "selesai"]
    c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=H)
    akhir = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
    jenis = [e["jenis"] for e in akhir["peristiwa"]]
    assert akhir["kartu_menunggu"] is None and "aksi_pengguna" in jenis and jenis[-1] == "teks"
    assert "selesai" not in jenis
    assert c.get(f"/api/milkybook/sesi/{sid}", headers={"X-Uji-Tenant": "t2"}).status_code == 404
    assert c.get(f"/api/milkybook/sesi/{sid}", headers={"X-Uji-Tenant": "t1", "X-Uji-User": "u2"}).status_code == 404
    assert c.get(f"/api/milkybook/sesi/{sid}").status_code == 401


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
async def test_tampilan_ikut_round_trip_dan_dibatasi(store_fn):
    from app.milkybook.inti.router import MAKS_TAMPILAN
    store = store_fn()
    s = await store.buat("t", "u", konteks="", hari_ini=date(2026, 10, 25))
    s.tampilan = [{"jenis": "teks", "teks": str(i)} for i in range(5)]
    await store.simpan(s)
    assert (await store.ambil("t", "u", s.sesi_id)).tampilan == s.tampilan
    assert MAKS_TAMPILAN >= 100


def test_tampilan_dibatasi_yang_tertua_dibuang(monkeypatch):
    import app.milkybook.inti.router as R
    monkeypatch.setattr(R, "MAKS_TAMPILAN", 3)
    app, _ = _app(PenyimpananSesiMemori(), model=ModelPalsu([teks("a"), teks("b")]))
    c = TestClient(app)
    H = {"X-Uji-Tenant": "t1"}
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "satu"}, headers=H)
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "dua"}, headers=H)
    assert c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()["peristiwa"] == [
        {"jenis": "teks", "teks": "a"}, {"jenis": "pengguna", "teks": "dua"}, {"jenis": "teks", "teks": "b"}]


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
async def test_kotak_masuk_kedua_penyimpanan(store_fn):
    from app.milkybook.inti.aliran import MAKS_ANTRE
    from app.milkybook.inti.penyimpanan import AntreanPenuh
    st = store_fn()
    for i in range(MAKS_ANTRE):
        assert await st.antre_tambah("t", "u", "s", f"id{i}", f"teks {i}") == i + 1
    with pytest.raises(AntreanPenuh):
        await st.antre_tambah("t", "u", "s", "lebih", "x")
    assert [x["id_pesan"] for x in await st.antre_daftar("t", "u", "s")] == [f"id{i}" for i in range(MAKS_ANTRE)]
    assert await st.antre_status("t", "u", "s", "id1") == "antre"
    assert await st.antre_batal("t", "u", "s", "id1") == "dibatalkan"
    assert await st.antre_batal("t", "u", "s", "id1") is None
    dibaca = await st.antre_ambil_semua("t", "u", "s")
    assert [m["id_pesan"] for m in dibaca] == ["id0", "id2", "id3", "id4"] and dibaca[0]["teks"] == "teks 0"
    assert await st.antre_ambil_semua("t", "u", "s") == []
    assert await st.antre_status("t", "u", "s", "id2") == "dibaca"
    assert await st.antre_batal("t", "u", "s", "id2") == "sudah_dibaca"
    assert await st.antre_daftar("t", "u", "s") == [] and await st.antre_daftar("t", "u2", "s") == []  # ruang pengguna
    assert not await st.sibuk("t", "u", "s")
    async with st.kunci("t", "u", "s") as peg:
        assert await st.sibuk("t", "u", "s")
        await peg.perpanjang()
        assert await st.sibuk("t", "u", "s")
    assert not await st.sibuk("t", "u", "s")
