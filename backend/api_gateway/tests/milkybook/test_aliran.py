"""#10 aliran SSE + kotak masuk (kontrak T3 kelima, FRONTEND/MASTER 25 Okt). Nol DB / Redis / LLM sungguhan.

Pagar MASTER yang dikunci di sini: Setuju atomik (pesan antre TIDAK menyela tulis), maks 5 pesan / 2000 karakter,
audit hash+panjang tanpa teks mentah, balapan "pesan datang saat kartu lahir" -> kartu lama BASI, tak ada dua menunggu,
semua rute sesi memeriksa tenant x pengguna (404)."""
import asyncio
import json
import time

from fastapi.testclient import TestClient

from app.milkybook.inti import aliran
from app.milkybook.inti.kartu import StatusKartu
from app.milkybook.inti.model import HasilModel
from app.milkybook.inti.penyimpanan import PenyimpananSesiMemori

from .conftest import ModelPalsu, panggil, teks
from .test_router_penyimpanan import _app

H = {"X-Uji-Tenant": "t1"}
SSE = {**H, "Accept": "text/event-stream"}


def _sesi(c, h=H):
    return c.post("/api/milkybook/sesi", headers=h).json()["sesi_id"]


def _uraikan(badan: str):
    """[(event, data)] dari teks SSE; komentar (": ...") dikembalikan sebagai ("#", teks)."""
    hasil = []
    for blok in badan.split("\n\n"):
        if not blok.strip():
            continue
        if blok.startswith(":"):
            hasil.append(("#", blok[1:].strip()))
            continue
        baris = dict(x.split(": ", 1) for x in blok.split("\n"))
        hasil.append((baris["event"], json.loads(baris["data"])))
    return hasil


def _antrekan(store, sid, id_pesan, isi):
    store._antre.setdefault(f"t1:u1:{sid}", []).append({"id_pesan": id_pesan, "teks": isi})


# ---------------------------------------------------------------- SSE

def test_sse_langkah_kartu_teks_selesai_dan_header():
    store = PenyimpananSesiMemori()
    app, _ = _app(store, model=ModelPalsu([panggil(("cari_x", {"q": "kaos"})), teks("Harga kaos Rp 45.000.")]))
    c = TestClient(app)
    sid = _sesi(c)
    with c.stream("POST", f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "harga?"}, headers=SSE) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no"
        badan = "".join(r.iter_text())
    assert badan.startswith(": mulai\n\n")  # byte pertama segera (Cloudflare 524)
    ev = [x for x in _uraikan(badan) if x[0] != "#"]
    assert all(nama == data["jenis"] for nama, data in ev)  # setiap data memuat jenis = nama event
    jenis = [n for n, _ in ev]
    assert jenis[0] == "langkah" and jenis[-1] == "selesai" and "teks" in jenis
    mulai, akhir = ev[0][1], next(d for n, d in ev if n == "langkah" and d["status"] != "mulai")
    assert mulai["status"] == "mulai" and akhir["status"] == "selesai" and mulai["id"] == akhir["id"] and mulai["label"]
    assert jenis.index("langkah") < jenis.index("teks")
    # langkah hidup saja: tidak masuk tampilan / GET
    pul = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
    assert "langkah" not in [e["jenis"] for e in pul["peristiwa"]] and "selesai" not in [e["jenis"] for e in pul["peristiwa"]]


def test_tanpa_accept_sse_json_lama_dengan_urutan_sama():
    store = PenyimpananSesiMemori()
    app, _ = _app(store, model=ModelPalsu([panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("Tersimpan.")]))
    c = TestClient(app)
    sid = _sesi(c)
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat"}, headers=H).json()["peristiwa"]
    assert ev[-1]["jenis"] == "selesai" and "langkah" not in [e["jenis"] for e in ev]
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    ev2 = c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=H).json()["peristiwa"]
    assert ev2[0]["jenis"] == "kartu_status" and ev2[0]["status"] == "selesai"  # status kartu SEBELUM penjelasan
    assert ev2[-1]["jenis"] == "selesai" and sum(1 for e in ev2 if e["jenis"] == "selesai") == 1


def test_label_langkah_ringkas_tanpa_kurung():
    for nama, lab in aliran.LABEL_LANGKAH.items():
        assert len(lab.split()) <= 4 and "(" not in lab, nama
    assert aliran.label_langkah("tak_dikenal", "WRITE") == "Menyiapkan kartu Tinjau"
    assert aliran.label_langkah("tak_dikenal") == "Memproses"


def test_detak_ping_saat_model_lama(monkeypatch):
    monkeypatch.setattr(aliran, "DETIK_DETAK", 0.05)

    class Lambat:
        async def __call__(self, **kw):
            await asyncio.sleep(0.3)
            return HasilModel([{"type": "text", "text": "Selesai."}], "end_turn", {}, "palsu")
    app, _ = _app(PenyimpananSesiMemori(), model=Lambat())
    c = TestClient(app)
    sid = _sesi(c)
    with c.stream("POST", f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "x"}, headers=SSE) as r:
        badan = "".join(r.iter_text())
    assert ("#", "ping") in _uraikan(badan)


# ---------------------------------------------------------------- kotak masuk

def test_pesan_antre_dibaca_sebelum_panggilan_model_berikut_dan_audit_tanpa_teks():
    store = PenyimpananSesiMemori()
    sid_ref = {}

    def langkah1(_):
        _antrekan(store, sid_ref["s"], "pesan0001", "eh, jumlahnya 5 ya")  # datang saat giliran berjalan
        return panggil(("cari_x", {"q": "kaos"}))
    m = ModelPalsu([langkah1, teks("Baik, 5 pcs.")])
    app, _ = _app(store, model=m)
    c = TestClient(app)
    sid_ref["s"] = sid = _sesi(c)
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat penawaran kaos", "id_pesan": "pesan0000"},
                headers=H).json()["peristiwa"]
    akhir = m.permintaan[1]["messages"][-1]
    assert akhir["role"] == "user" and any("[pesan baru pengguna" in b.get("text", "") and "jumlahnya 5" in b.get("text", "")
                                           for b in akhir["content"] if isinstance(b, dict))
    assert {"jenis": "antre_dibaca", "id_pesan": ["pesan0001"]} in ev
    assert {"jenis": "pengguna", "id_pesan": "pesan0001", "teks": "eh, jumlahnya 5 ya", "dari_antrean": True} in ev
    s = asyncio.run(store.ambil("t1", "u1", sid))
    assert {"pesan0000", "pesan0001"} <= set(s.diproses_id)
    giliran = [b for b in app.state_audit.baris if b.peristiwa == "giliran"]
    assert giliran[-1].hasil.startswith("antre:") and "jumlahnya" not in giliran[-1].hasil
    peran = [x["role"] for x in m.permintaan[1]["messages"]]
    assert all(a != b for a, b in zip(peran, peran[1:]))  # peran tetap berselang


def test_setuju_atomik_pesan_antre_diproses_sesudah_tulis_sebagai_giliran_baru():
    store = PenyimpananSesiMemori()
    m = ModelPalsu([panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("Tersimpan."), teks("Baik, saya kirim nanti.")])
    app, be = _app(store, model=m, kembalikan_be=True)
    c = TestClient(app)
    sid = _sesi(c)
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat"}, headers=H).json()["peristiwa"]
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    asli = be.rute[("POST", "/api/x")]

    def tulis(**k):  # pesan datang TEPAT saat Setuju menulis
        _antrekan(store, sid, "pesan0002", "sekalian kirim ke pelanggan")
        return asli
    be.rute[("POST", "/api/x")] = tulis
    ev2 = c.post(f"/api/milkybook/kartu/{kid}/setuju", headers=H).json()["peristiwa"]
    penjelasan = m.permintaan[2]["messages"]  # panggilan model sesudah tulis
    assert not any("sekalian kirim" in json.dumps(x, ensure_ascii=False) for x in penjelasan)
    assert ev2[0]["jenis"] == "kartu_status" and ev2[0]["status"] == "selesai"
    i_teks = [e["jenis"] for e in ev2].index("teks")
    i_gema = next(i for i, e in enumerate(ev2) if e.get("id_pesan") == "pesan0002" and e["jenis"] == "pengguna")
    assert i_teks < i_gema  # penjelasan Setuju dulu, baru giliran baru dari antre
    assert m.permintaan[3]["messages"][-1] == {"role": "user", "content": "sekalian kirim ke pelanggan"}
    assert len([b for b in be.panggilan if b["metode"] == "POST" and b["jalur"] == "/api/x"]) == 1


def test_balapan_pesan_saat_kartu_lahir_kartu_lama_basi_satu_menunggu():
    store = PenyimpananSesiMemori()
    ref = {}

    def lahir(_):
        _antrekan(store, ref["s"], "pesan0003", "eh jadi 5")
        return panggil(("buat_x", {"jumlah": 3}))
    m = ModelPalsu([lahir, panggil(("buat_x", {"jumlah": 5})), teks("Kartu baru 5 pcs.")])
    app, _ = _app(store, model=m)
    c = TestClient(app)
    ref["s"] = sid = _sesi(c)
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat 3"}, headers=H).json()["peristiwa"]
    kartu = [e for e in ev if e["jenis"] == "kartu"]
    assert len(kartu) == 2
    assert {"jenis": "kartu_status", "kartu_id": kartu[0]["kartu_id"], "status": "basi"} in ev
    s = asyncio.run(store.ambil("t1", "u1", sid))
    assert [k.kartu_id for k in s.kartu.values() if k.status == StatusKartu.MENUNGGU] == [kartu[1]["kartu_id"]]


def test_pesan_saat_sibuk_202_batas_429_dan_422():
    store = PenyimpananSesiMemori()
    app, _ = _app(store)
    c = TestClient(app)
    sid = _sesi(c)
    store._kunci.add(f"t1:u1:{sid}")
    for i in range(aliran.MAKS_ANTRE):
        r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": f"p{i}", "id_pesan": f"pesan100{i}"}, headers=H)
        assert r.status_code == 202 and r.json()["posisi"] == i + 1
    ulang = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "p0", "id_pesan": "pesan1000"}, headers=H)
    assert ulang.status_code == 202 and ulang.json()["posisi"] == 1  # idempoten: tak antre dua kali
    r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "lagi", "id_pesan": "pesan1009"}, headers=H)
    assert r.status_code == 429 and r.json()["kode"] == "antrean_penuh"
    store._antre[f"t1:u1:{sid}"] = []
    r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "x" * (aliran.MAKS_TEKS_ANTRE + 1)}, headers=H)
    assert r.status_code == 422
    pul = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
    assert pul["sedang_bekerja"] is True


def test_id_pesan_idempoten_sesudah_diproses():
    store = PenyimpananSesiMemori()
    m = ModelPalsu([teks("Halo."), teks("tak boleh dipanggil")])
    app, _ = _app(store, model=m)
    c = TestClient(app)
    sid = _sesi(c)
    b = {"teks": "hai", "id_pesan": "pesan2000"}
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json=b, headers=H).json()["peristiwa"][0]["teks"] == "Halo."
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json=b, headers=H).json() == {"duplikat": True, "peristiwa": []}
    with c.stream("POST", f"/api/milkybook/sesi/{sid}/pesan", json=b, headers=SSE) as r:
        ev = [x for x in _uraikan("".join(r.iter_text())) if x[0] != "#"]
    assert ev == [("selesai", {"jenis": "selesai", "duplikat": True})]
    assert len(m.permintaan) == 1


def test_delete_antre_dan_kepemilikan_sesi():
    store = PenyimpananSesiMemori()
    app, _ = _app(store)
    c = TestClient(app)
    sid = _sesi(c)
    store._kunci.add(f"t1:u1:{sid}")
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "a", "id_pesan": "pesan3000"}, headers=H)
    c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "b", "id_pesan": "pesan3001"}, headers=H)
    u = f"/api/milkybook/sesi/{sid}/antre"
    assert c.delete(f"{u}/pesan3000", headers={"X-Uji-Tenant": "t2"}).status_code == 404               # tenant lain
    assert c.delete(f"{u}/pesan3000", headers={"X-Uji-Tenant": "t1", "X-Uji-User": "u2"}).status_code == 404  # pengguna lain
    assert c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "c"},
                  headers={"X-Uji-Tenant": "t1", "X-Uji-User": "u2"}).status_code == 404
    assert c.delete(f"{u}/pesan3000", headers=H).json() == {"dibatalkan": True, "id_pesan": "pesan3000"}
    assert c.delete(f"{u}/pesan3000", headers=H).status_code == 404
    asyncio.run(store.antre_ambil_semua("t1", "u1", sid))  # model membaca pesan3001
    r = c.delete(f"{u}/pesan3001", headers=H)
    assert r.status_code == 409 and r.json()["kode"] == "antre_sudah_dibaca"
    assert c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()["antre"] == []


def test_202_tidak_terlantar_bila_giliran_selesai_tepat_sesudah_antre():
    """Penambah antre mencoba kunci SESUDAH menambah: giliran yang barusan lepas tak membaca -> penambah memproses."""
    store = PenyimpananSesiMemori()
    m = ModelPalsu([teks("Diproses dari antre.")])
    app, _ = _app(store, model=m)
    c = TestClient(app)
    sid = _sesi(c)
    kunci = f"t1:u1:{sid}"
    store._kunci.add(kunci)
    asli = store.antre_tambah

    async def tambah_lalu_lepas(*a, **k):
        n = await asli(*a, **k)
        store._kunci.discard(kunci)  # giliran lain selesai TEPAT sesudah pesan masuk antre
        return n
    store.antre_tambah = tambah_lalu_lepas
    r = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "tolong cek", "id_pesan": "pesan4000"}, headers=H)
    assert r.status_code == 202
    batas = time.time() + 2
    while time.time() < batas:
        pul = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
        if not pul["sedang_bekerja"] and any(e["jenis"] == "teks" for e in pul["peristiwa"]):
            break
        time.sleep(0.02)
    jenis = [(e["jenis"], e.get("id_pesan")) for e in pul["peristiwa"]]
    assert ("pengguna", "pesan4000") in jenis and ("teks", None) in jenis and pul["antre"] == []
