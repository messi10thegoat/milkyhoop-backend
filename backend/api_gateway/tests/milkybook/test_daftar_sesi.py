"""(b) daftar percakapan + ganti nama / arsip (pola Claude Code). Nol DB / Redis / LLM sungguhan.

Pagar MASTER 25 Okt: sesi milik tenant x pengguna (404 lintas; daftar hanya milik sendiri). Ganti nama tak tertimpa
simpan giliran (medan meta ditulis terpisah)."""
import time

import pytest
from fastapi.testclient import TestClient

from app.milkybook.inti.penyimpanan import PenyimpananSesiMemori, PenyimpananSesiRedis

from .conftest import ModelPalsu, teks
from .test_router_penyimpanan import RedisPalsu, _app

H = {"X-Uji-Tenant": "t1"}


def _klien(store, n_jawab=4):
    app, _ = _app(store, model=ModelPalsu([teks("Siap.")] * n_jawab))
    return TestClient(app)


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
def test_daftar_terbaru_dulu_judul_otomatis_dari_ucapan_pertama(store_fn):
    store = store_fn()
    c = _klien(store)
    a = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    b = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    c.post(f"/api/milkybook/sesi/{b}/pesan", json={"teks": "Ringkas   penawaran bulan ini"}, headers=H)
    time.sleep(0.01)
    panjang = "Buat penawaran untuk Flowry: 200 kaos sablon dua warna, kirim minggu depan, tanpa pajak ya"
    c.post(f"/api/milkybook/sesi/{a}/pesan", json={"teks": panjang}, headers=H)
    d = c.get("/api/milkybook/sesi", headers=H).json()["sesi"]
    assert [x["sesi_id"] for x in d] == [a, b]
    assert d[1]["judul"] == "Ringkas penawaran bulan ini"
    assert len(d[0]["judul"]) == 60 and d[0]["judul"].endswith("…") and d[0]["diarsip"] is False
    # sesi tanpa ucapan: judul bawaan
    k = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    assert next(x for x in c.get("/api/milkybook/sesi", headers=H).json()["sesi"] if x["sesi_id"] == k)["judul"] == "Percakapan baru"


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
def test_ganti_nama_tak_tertimpa_giliran_berikut_dan_arsip(store_fn):
    store = store_fn()
    c = _klien(store)
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "halo"}, headers=H)
    r = c.patch(f"/api/milkybook/sesi/{sid}", json={"judul": "  Penawaran   Flowry "}, headers=H)
    assert r.status_code == 200 and r.json()["judul"] == "Penawaran Flowry"
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "lanjut"}, headers=H)  # simpan giliran
    assert c.get("/api/milkybook/sesi", headers=H).json()["sesi"][0]["judul"] == "Penawaran Flowry"
    assert c.patch(f"/api/milkybook/sesi/{sid}", json={"diarsip": True}, headers=H).json()["diarsip"] is True
    assert c.get("/api/milkybook/sesi", headers=H).json()["sesi"] == []
    arsip = c.get("/api/milkybook/sesi?arsip=true", headers=H).json()["sesi"]
    assert [x["sesi_id"] for x in arsip] == [sid] and arsip[0]["judul"] == "Penawaran Flowry"
    assert c.get(f"/api/milkybook/sesi/{sid}", headers=H).status_code == 200  # arsip tetap bisa dibuka


def test_ubah_sesi_422_dan_lintas_tenant_pengguna():
    store = PenyimpananSesiMemori()
    c = _klien(store)
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    assert c.patch(f"/api/milkybook/sesi/{sid}", json={"judul": "   "}, headers=H).status_code == 422
    assert c.patch(f"/api/milkybook/sesi/{sid}", json={}, headers=H).status_code == 422
    assert c.patch(f"/api/milkybook/sesi/{sid}", json={"judul": "x" * 81}, headers=H).status_code == 422
    assert c.patch(f"/api/milkybook/sesi/{sid}", json={"asing": 1}, headers=H).status_code == 422
    for h in ({"X-Uji-Tenant": "t2"}, {"X-Uji-Tenant": "t1", "X-Uji-User": "u2"}):
        assert c.patch(f"/api/milkybook/sesi/{sid}", json={"judul": "curi"}, headers=h).status_code == 404
        assert c.get("/api/milkybook/sesi", headers=h).json()["sesi"] == []


async def test_redis_daftar_membuang_sesi_kedaluwarsa():
    r = RedisPalsu()
    store = PenyimpananSesiRedis(r)
    s = await store.buat("t", "u")
    s2 = await store.buat("t", "u")
    await r.delete(f"milkybook:sesi:t:u:{s.sesi_id}")  # TTL habis
    assert [x["sesi_id"] for x in await store.daftar("t", "u")] == [s2.sesi_id]
    assert s.sesi_id not in r.d["milkybook:daftar:t:u:-"]
