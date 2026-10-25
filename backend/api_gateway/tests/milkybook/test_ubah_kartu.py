"""#8 ubah isian langsung di kartu Tinjau (kontrak T3 keenam + koreksi FRONTEND 25 Okt). Nol DB / Redis / LLM."""
import json

from fastapi.testclient import TestClient

from app.milkybook.inti import ubah_kartu as uk
from app.milkybook.inti.kartu import StatusKartu
from app.milkybook.inti.penyimpanan import PenyimpananSesiMemori
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.paket import penawaran as pw

from .conftest import ModelPalsu, panggil, teks
from .test_router_penyimpanan import _app

H = {"X-Uji-Tenant": "t1"}


def _kartu_menunggu(store, model_naskah=None, alat=("buat_x", {"jumlah": 2})):
    m = ModelPalsu(model_naskah or [panggil(alat), teks("Tinjau.")])
    app, be = _app(store, model=m, kembalikan_be=True)
    c = TestClient(app)
    sid = c.post("/api/milkybook/sesi", headers=H).json()["sesi_id"]
    ev = c.post(f"/api/milkybook/sesi/{sid}/pesan", json={"teks": "buat"}, headers=H).json()["peristiwa"]
    k = next(e for e in ev if e["jenis"] == "kartu")
    return c, m, be, sid, k


def _sesi(store, sid):
    import asyncio
    return asyncio.run(store.ambil("t1", "u1", sid))


def test_ubah_sukses_kartu_baru_menggantikan_tanpa_model_angka_string_dinormalkan():
    store = PenyimpananSesiMemori()
    c, m, be, sid, a = _kartu_menunggu(store)
    assert a["ubah"] == [{"path": "jumlah", "label": "Jumlah", "tipe": "angka", "wajib": True}]
    n_model = len(m.permintaan)
    r = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": "5"}}, headers=H)
    assert r.status_code == 200
    ev = r.json()["peristiwa"]
    assert ev[0] == {"jenis": "kartu_status", "kartu_id": a["kartu_id"], "status": "basi"}
    b = ev[1]
    assert b["jenis"] == "kartu" and b["menggantikan"] == a["kartu_id"] and b["kartu_id"] != a["kartu_id"]
    assert b["payload"] == {"jumlah": 5} and b["pratinjau"] == {"total": 225000} and b["payload_hash"] != a["payload_hash"]
    assert ev[-1]["jenis"] == "selesai" and len(m.permintaan) == n_model  # nol panggilan model
    s = _sesi(store, sid)
    assert s.kartu[a["kartu_id"]].status == StatusKartu.BASI and s.kartu_menunggu().kartu_id == b["kartu_id"]
    assert "[sistem] Pengguna mengubah isian kartu" in json.dumps(s.messages[-1], ensure_ascii=False)
    tampil = c.get(f"/api/milkybook/sesi/{sid}", headers=H).json()
    assert {"jenis": "aksi_pengguna", "aksi": "ubah", "kartu_id": a["kartu_id"]} in tampil["peristiwa"]
    assert not any(e["jenis"] == "pengguna" and "[sistem]" in e.get("teks", "") for e in tampil["peristiwa"])
    assert tampil["kartu_menunggu"] == b["kartu_id"]
    # kartu baru yang disetujui = satu tulis dengan payload baru; kartu lama tak bisa disetujui
    assert c.post(f"/api/milkybook/kartu/{a['kartu_id']}/setuju", headers=H).json()["peristiwa"][0]["kode"] == "kartu_tidak_menunggu"
    c.post(f"/api/milkybook/kartu/{b['kartu_id']}/setuju", headers=H)
    tulis = [x for x in be.panggilan if x["metode"] == "POST" and x["jalur"] == "/api/x"]
    assert len(tulis) == 1 and json.loads(tulis[0]["badan"]) == {"jumlah": 5}


def test_ubah_422_per_kolom_dan_kartu_lama_tetap_menunggu():
    store = PenyimpananSesiMemori()
    c, _, _, sid, a = _kartu_menunggu(store)
    r = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": "abc"}}, headers=H)
    assert r.status_code == 422
    assert r.json() == {"kode": "masukan_tidak_sah", "detail": [{"path": "jumlah", "pesan": "Jumlah harus berupa angka."}]}
    r2 = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 1, "asing": 2}}, headers=H)
    assert r2.status_code == 422 and r2.json()["detail"][0]["path"] == "asing"
    assert _sesi(store, sid).kartu[a["kartu_id"]].status == StatusKartu.MENUNGGU


def test_ubah_409_kartu_basi_dan_404_pengguna_lain():
    store = PenyimpananSesiMemori()
    c, _, _, _, a = _kartu_menunggu(store)
    c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 3}}, headers=H)
    r = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 4}}, headers=H)
    assert r.status_code == 409 and r.json()["kode"] == "kartu_tidak_menunggu"
    for h in ({"X-Uji-Tenant": "t2"}, {"X-Uji-Tenant": "t1", "X-Uji-User": "u2"}):
        assert c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 4}}, headers=h).status_code == 404


def test_ubah_idempoten_ulang_kunci_sama_respons_pertama():
    store = PenyimpananSesiMemori()
    c, _, _, sid, a = _kartu_menunggu(store)
    h = {**H, "X-Idempotency-Key": "ubah-0001"}
    r1 = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 7}}, headers=h).json()
    r2 = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 7}}, headers=h).json()
    assert r2["replay"] is True and r2["peristiwa"] == r1["peristiwa"]
    s = _sesi(store, sid)
    assert sum(1 for k in s.kartu.values() if k.status == StatusKartu.MENUNGGU) == 1 and len(s.kartu) == 2


def test_ubah_pratinjau_menolak_kartu_lama_tetap_dan_blok_dinormalkan():
    store = PenyimpananSesiMemori()
    c, _, be, sid, a = _kartu_menunggu(store)
    be.rute[("POST", "/api/x/calculate")] = JawabanBE(200, {"data": {"ok": False, "blocks": [
        {"code": "JUMLAH_TERLALU_BESAR", "message": "Jumlah melebihi stok.", "field": "jumlah"}]}})
    ev = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"jumlah": 999}}, headers=H).json()["peristiwa"]
    assert ev[0]["kode"] == "pratinjau_menolak"
    assert ev[0]["blocks"] == [{"kode": "JUMLAH_TERLALU_BESAR", "pesan": "Jumlah melebihi stok.", "path": "jumlah"}]
    assert _sesi(store, sid).kartu_menunggu().kartu_id == a["kartu_id"]


def test_ubah_id_dokumen_dari_kartu_lama_tidak_bisa_diubah():
    store = PenyimpananSesiMemori()
    c, _, be, sid, a = _kartu_menunggu(store, alat=("ubah_x", {"id": "a1", "jumlah": 2}))
    assert [m["path"] for m in a["ubah"]] == ["jumlah"]  # parameter jalur tak ada di daftar
    ev = c.post(f"/api/milkybook/kartu/{a['kartu_id']}/ubah", json={"payload": {"id": "lain", "jumlah": 3}},
                headers=H).json()["peristiwa"]
    b = next(e for e in ev if e["jenis"] == "kartu")
    assert _sesi(store, sid).kartu[b["kartu_id"]].jalur == "/api/x/a1"
    assert [x["jalur"] for x in be.panggilan if x["jalur"].endswith("/edit/preview")][-1] == "/api/x/a1/edit/preview"


def test_daftar_medan_skema_penawaran_asli():
    t = next(x for x in pw.TOOLS if x.nama == "buat_penawaran")
    payload = {"quote_date": "2026-10-25", "customer_id": "c1", "customer_name": "Flowry",
               "items": [{"item_id": "i1", "description": "Kaos", "quantity": 2, "unit_price": 100000, "tax_rate": 0},
                         {"description": "Sablon", "quantity": 1, "unit_price": 15000}]}
    m = {x["path"]: x for x in uk.daftar_medan(t.skema, payload, t.param_jalur())}
    assert m["items[0].quantity"]["tipe"] == "angka" and m["items[1].unit_price"]["tipe"] == "uang"
    assert m["quote_date"]["tipe"] == "tanggal" and m["quote_date"]["wajib"] is True
    assert m["discount_type"]["pilihan"] == ["fixed", "percentage"]
    assert not {"customer_id", "customer_name", "items[0].item_id", "items[0].tax_id"} & set(m)
    assert "items[2].quantity" not in m  # tambah baris lewat obrolan


def test_blok_pratinjau_pydantic_dan_teks():
    assert uk.blok_pratinjau(422, {"detail": [{"loc": ["body", "items", 0, "quantity"], "msg": "harus > 0"}]}) == [
        {"kode": "DATA_TIDAK_SAH", "pesan": "harus > 0", "path": "items.0.quantity"}]
    assert uk.blok_pratinjau(400, {"detail": "Penawaran sudah diterima."}) == [{"kode": "DITOLAK", "pesan": "Penawaran sudah diterima."}]
    assert uk.normalkan_angka({"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "string"}}},
                              {"a": "45000.50", "b": "12"}) == {"a": 45000.5, "b": "12"}
