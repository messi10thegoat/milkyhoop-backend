"""Tombol stop Milkybook (POST /sesi/{id}/berhenti). Nol DB / Redis / LLM sungguhan.

Pagar MASTER 25 Okt yang dikunci di sini: stop TIDAK memotong tulis Setuju yang sedang berjalan (tes balapan), riwayat
model tetap sah (setiap tool_use punya tool_result; pesan terakhir = user), sesi milik tenant x pengguna (404 lintas),
bendera basi tidak menghentikan giliran berikut, antre yang belum dibaca dikembalikan tanpa ditandai dibaca."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.milkybook.inti import aliran
from app.milkybook.inti.kartu import StatusKartu
from app.milkybook.inti.penyimpanan import PenyimpananSesiMemori, PenyimpananSesiRedis
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.inti.putaran import CATATAN_DIHENTIKAN

from .conftest import ModelPalsu, panggil, teks
from .test_router_penyimpanan import RedisPalsu, _app

H = {"X-Uji-Tenant": "t1"}


def _sesi(c):
    return c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]


def _henti(store, sid):
    store._henti.add(f"t1:u1:{sid}")  # sama dengan POST /berhenti dari worker lain saat giliran berjalan


def _riwayat_sah(messages):
    """Setiap tool_use dijawab tool_result di pesan user berikutnya; pesan terakhir = user."""
    for i, m in enumerate(messages):
        if m["role"] == "assistant" and isinstance(m["content"], list):
            ids = {b["id"] for b in m["content"] if b.get("type") == "tool_use"}
            if ids:
                jawab = {b.get("tool_use_id") for b in messages[i + 1]["content"] if isinstance(b, dict)}
                assert ids <= jawab, ids - jawab
    assert messages[-1]["role"] == "user"


def _sesi_store(store, sid):
    return asyncio.run(store.ambil("t1", "u1", sid))


def test_stop_di_antara_tool_tool_berikut_tak_jalan_tanpa_panggilan_model_lagi():
    store = PenyimpananSesiMemori()
    m = ModelPalsu([panggil(("cari_x", {"q": "a"}), ("cari_x", {"q": "b"})), teks("tak boleh sampai")])
    app, be = _app(store, model=m, kembalikan_be=True)
    c = TestClient(app)
    sid = _sesi(c)
    asli = be.rute[("GET", "/api/x")]

    def cari(**k):
        _henti(store, sid)
        return asli
    be.rute[("GET", "/api/x")] = cari
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "cari"}, headers=H).json()["peristiwa"]
    assert [x["jalur"] for x in be.panggilan].count("/api/x") == 1  # tool kedua tidak dijalankan
    assert len(m.permintaan) == 1
    assert ev[-2] == {"jenis": "dihentikan"} and ev[-1]["jenis"] == "selesai" and ev[-1]["dihentikan"] is True
    assert not any(e["jenis"] == "teks" for e in ev)
    s = _sesi_store(store, sid)
    _riwayat_sah(s.messages)
    hasil = s.messages[-1]["content"]
    assert hasil[1]["is_error"] is True and "Dihentikan pengguna" in hasil[1]["content"]
    assert hasil[-1] == {"type": "text", "text": CATATAN_DIHENTIKAN}
    assert app.state_audit.baris[-1].peristiwa == "giliran" and app.state_audit.baris[-1].hasil == "dihentikan"
    assert f"t1:u1:{sid}" not in store._henti  # bendera dihapus di akhir giliran


class ModelLambat:
    """Panggilan model yang lama; bendera stop dipasang SAAT panggilan berjalan."""

    def __init__(self, store, ref, detik=5.0):
        self.store, self.ref, self.detik, self.permintaan, self.dibatalkan = store, ref, detik, [], False

    async def __call__(self, *, system, messages, tools, effort):
        self.permintaan.append(messages)
        _henti(self.store, self.ref["sid"])
        try:
            await asyncio.sleep(self.detik)
        except asyncio.CancelledError:
            self.dibatalkan = True
            raise
        return teks("tak boleh sampai")


def test_stop_selama_panggilan_model_dibatalkan_cepat_riwayat_tetap_berakhir_di_user():
    store = PenyimpananSesiMemori()
    ref = {}
    m = ModelLambat(store, ref)
    app, _ = _app(store, model=m)
    c = TestClient(app)
    ref["sid"] = sid = _sesi(c)
    t0 = time.time()
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "ringkas penawaran"}, headers=H).json()["peristiwa"]
    assert time.time() - t0 < 2.0 and m.dibatalkan
    assert ev[-1]["dihentikan"] is True and not any(e["jenis"] == "teks" for e in ev)
    s = _sesi_store(store, sid)
    _riwayat_sah(s.messages)
    assert s.messages[-1]["content"][-1]["text"] == CATATAN_DIHENTIKAN and len(s.messages) == 1


def test_balapan_stop_saat_setuju_menulis_tulis_tetap_utuh_sekali():
    """Pagar atomik: stop datang TEPAT saat pelaksana menulis -> tulis selesai (sekali), kartu SELESAI, penjelasan
    model sesudahnya yang dilewati."""
    store = PenyimpananSesiMemori()
    m = ModelPalsu([panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("tak boleh sampai")])
    app, be = _app(store, model=m, kembalikan_be=True)
    c = TestClient(app)
    sid = _sesi(c)
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat"}, headers=H).json()["peristiwa"]
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    asli = be.rute[("POST", "/api/x")]

    async def tulis_lambat(**k):
        _henti(store, sid)
        await asyncio.sleep(1.2)  # > DETIK_CEK_HENTI: tulis TIDAK dibungkus atau_berhenti
        return asli
    sebelum = be.kirim

    async def kirim(metode, jalur, **k):
        if (metode, jalur) == ("POST", "/api/x"):
            be.panggilan.append({"metode": metode, "jalur": jalur, "badan": k.get("badan")})
            return await tulis_lambat(**k)
        return await sebelum(metode, jalur, **k)
    be.kirim = kirim
    n_model = len(m.permintaan)
    ev2 = c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=H).json()["peristiwa"]
    assert ev2[0]["jenis"] == "kartu_status" and ev2[0]["status"] == "selesai"
    assert len([b for b in be.panggilan if b["metode"] == "POST" and b["jalur"] == "/api/x"]) == 1
    assert len(m.permintaan) == n_model and ev2[-1]["dihentikan"] is True
    s = _sesi_store(store, sid)
    assert s.kartu[kid].status == StatusKartu.SELESAI
    _riwayat_sah(s.messages)


def test_bendera_basi_tidak_menghentikan_giliran_berikut_dan_model_tahu_dihentikan():
    store = PenyimpananSesiMemori()
    ref = {}
    m = ModelLambat(store, ref)
    app, _ = _app(store, model=m)
    c = TestClient(app)
    ref["sid"] = sid = _sesi(c)
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "pertama"}, headers=H)
    _henti(store, sid)  # stop terlambat: giliran sudah selesai, bendera tertinggal
    app2, m2 = _app(store, model=ModelPalsu([teks("Siap.")]))
    c2 = TestClient(app2)
    ev = c2.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "lanjut yang lain"}, headers=H).json()["peristiwa"]
    assert [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Siap."] and "dihentikan" not in ev[-1]
    kirim = json.dumps(m2.permintaan[0]["messages"], ensure_ascii=False)
    assert CATATAN_DIHENTIKAN in kirim and "lanjut yang lain" in kirim


def test_endpoint_berhenti_tidak_sibuk_sibuk_antre_dikembalikan_dan_404_lintas():
    store = PenyimpananSesiMemori()
    app, _ = _app(store)
    c = TestClient(app)
    sid = _sesi(c)
    r = c.post(f"/api/milkybook/sesi/{sid}/berhenti", headers=H)
    assert r.status_code == 200 and r.json() == {"berhenti": False, "kode": "tidak_bekerja", "antre_dikembalikan": []}
    assert f"t1:u1:{sid}" not in store._henti
    store._kunci.add(f"t1:u1:{sid}")  # giliran sedang berjalan
    store._antre[f"t1:u1:{sid}"] = [{"id_pesan": "pesan5001", "teks": "sekalian diskon 5%"}]
    r = c.post(f"/api/milkybook/sesi/{sid}/berhenti", headers=H).json()
    assert r == {"berhenti": True, "antre_dikembalikan": [{"id_pesan": "pesan5001", "teks": "sekalian diskon 5%"}]}
    assert f"t1:u1:{sid}" in store._henti and store._antre[f"t1:u1:{sid}"] == []
    assert asyncio.run(store.antre_status("t1", "u1", sid, "pesan5001")) is None  # tidak ditandai dibaca: boleh kirim ulang
    for h in ({"X-Uji-Tenant": "t2"}, {"X-Uji-Tenant": "t1", "X-Uji-User": "u2"}):
        assert c.post(f"/api/milkybook/sesi/{sid}/berhenti", headers=h).status_code == 404


def test_tanpa_pendengar_atau_berhenti_menunggu_biasa():
    async def jalan():
        return await aliran.atau_berhenti(asyncio.sleep(0, result=7))
    assert asyncio.run(jalan()) == (True, 7)


@pytest.mark.parametrize("store_fn", [PenyimpananSesiMemori, lambda: PenyimpananSesiRedis(RedisPalsu())])
async def test_penyimpanan_bendera_dan_tarik_antre(store_fn):
    store = store_fn()
    assert await store.berhenti_diminta("t", "u", "s") is False
    await store.berhenti_minta("t", "u", "s")
    assert await store.berhenti_diminta("t", "u", "s") is True
    assert await store.berhenti_diminta("t", "u2", "s") is False  # ruang tenant x pengguna
    await store.berhenti_hapus("t", "u", "s")
    assert await store.berhenti_diminta("t", "u", "s") is False
    await store.antre_tambah("t", "u", "s", "pesan6001", "satu")
    assert await store.antre_tarik_semua("t", "u", "s") == [{"id_pesan": "pesan6001", "teks": "satu"}]
    assert await store.antre_daftar("t", "u", "s") == [] and await store.antre_status("t", "u", "s", "pesan6001") is None
