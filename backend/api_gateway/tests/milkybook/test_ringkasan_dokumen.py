"""Ringkasan dokumen di kartu tulis (tiket MASTER 25 Okt): kartu atas dokumen yang sudah ada membawa `dokumen`
dari server (pelanggan, total, jumlah baris, berlaku sampai) saat lahir; baca gagal = null, kartu tetap lahir."""
import os
import re
from dataclasses import replace

import pytest

from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.kartu import Kartu
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.inti.penyimpanan import _kartu_dari_dict, _kartu_ke_dict
from app.milkybook.inti.putaran import Putaran, Sesi
from app.milkybook.inti.registri import Kelas, Paket, Registri, Tool
from app.milkybook.paket import penawaran as P

from .conftest import ModelPalsu, izin_semua, panggil, teks
from .test_inti import OBJ, T_CARI, T_HITUNG, T_UBAH, T_UBAH_PR, be_standar

T_DETAIL = Tool("detail_x", Kelas.READ, "d", OBJ({"id": {"type": "string"}}, ["id"]), "GET", "/api/x/{id}")
DETAIL = {"success": True, "data": {"id": "a1", "nomor": "X-1", "jumlah": 2, "baris": [1, 2, 3]}}


def _ringkas(d):
    return {"nomor": d["nomor"], "jumlah_baris": len(d["baris"])}


def _putaran(ubah, naskah):
    reg = Registri([Paket("contoh", [T_CARI, T_HITUNG, T_UBAH_PR, ubah, T_DETAIL])])
    return Putaran(reg, ModelPalsu(naskah), AuditMemori(), gap_mendarat=("G1",))


def _kartu(ev):
    return [e for e in ev if e["jenis"] == "kartu"]


UBAH_DOK = replace(T_UBAH, ringkasan_dari="detail_x", ringkas_dokumen=_ringkas)


async def test_kartu_membawa_dokumen_dari_server():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    p = _putaran(UBAH_DOK, [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")])
    ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "ubah", be, izin_semua)
    assert _kartu(ev)[0]["dokumen"] == {"nomor": "X-1", "jumlah_baris": 3}


@pytest.mark.parametrize("jawab", [JawabanBE(500, {"detail": "x"}), JawabanBE(200, ["bukan dict"])])
async def test_baca_dokumen_gagal_dokumen_null_kartu_tetap_lahir(jawab):
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = jawab
    p = _putaran(UBAH_DOK, [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")])
    ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "ubah", be, izin_semua)
    k = _kartu(ev)
    assert len(k) == 1 and k[0]["dokumen"] is None


async def test_pemeta_meledak_dokumen_null_kartu_tetap_lahir():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, {"data": {"id": "a1"}})  # tanpa nomor/baris -> KeyError
    p = _putaran(UBAH_DOK, [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")])
    ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "ubah", be, izin_semua)
    assert len(_kartu(ev)) == 1 and _kartu(ev)[0]["dokumen"] is None


async def test_tanpa_ringkasan_dari_dokumen_null_dan_tanpa_get():
    be = be_standar()
    p = _putaran(T_UBAH, [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")])
    ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "ubah", be, izin_semua)
    assert _kartu(ev)[0]["dokumen"] is None
    assert not [c for c in be.panggilan if c["metode"] == "GET" and c["jalur"] == "/api/x/a1"]


async def test_pembanding_sama_satu_get_saja():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    p = _putaran(replace(UBAH_DOK, pembanding="detail_x"), [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")])
    ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "ubah", be, izin_semua)
    assert _kartu(ev)[0]["dokumen"] == {"nomor": "X-1", "jumlah_baris": 3}
    assert len([c for c in be.panggilan if c["metode"] == "GET" and c["jalur"] == "/api/x/a1"]) == 1


async def test_pratinjau_menolak_tidak_membaca_dokumen():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    be.rute[("POST", "/api/x/a1/edit/preview")] = JawabanBE(422, {"detail": "tidak boleh"})
    p = _putaran(UBAH_DOK, [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Ditolak.")])
    ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "ubah", be, izin_semua)
    assert not _kartu(ev)
    assert not [c for c in be.panggilan if c["metode"] == "GET" and c["jalur"] == "/api/x/a1"]


async def test_ubah_di_kartu_kartu_baru_membawa_dokumen():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    p = _putaran(UBAH_DOK, [panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")])
    s = Sesi("s1", "kaos-biru-konveksi", "u1")
    ev = await p.giliran(s, "ubah", be, izin_semua)
    lama = _kartu(ev)[0]["kartu_id"]
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, {"data": {**DETAIL["data"], "nomor": "X-1b"}})
    ev2 = await p.ubah_kartu(s, lama, {"jumlah": 7}, be, izin_semua)
    assert _kartu(ev2)[0]["dokumen"] == {"nomor": "X-1b", "jumlah_baris": 3}


def test_dokumen_bertahan_simpan_pulih():
    k = Kartu(sesi_id="s1", tool="ubah_x", metode="PATCH", jalur="/api/x/a1", payload={"jumlah": 5},
              dokumen={"nomor": "X-1"})
    assert _kartu_dari_dict(_kartu_ke_dict(k)).dokumen == {"nomor": "X-1"}
    assert _kartu_dari_dict({**_kartu_ke_dict(k), "dokumen": None}).dokumen is None
    d = _kartu_ke_dict(k)
    del d["dokumen"]  # kartu lama di Redis sebelum rilis ini
    assert _kartu_dari_dict(d).dokumen is None


@pytest.mark.parametrize("salah", [
    {"ringkasan_dari": "detail_x"},                                     # tanpa pemeta
    {"ringkasan_dari": "tak_ada", "ringkas_dokumen": _ringkas},          # tool tak sepaket
    {"ringkasan_dari": "cari_x", "ringkas_dokumen": _ringkas},           # cari_x tanpa {id}: boleh (subset kosong)
    {"ringkasan_dari": "hitung_x", "ringkas_dokumen": _ringkas},         # bukan READ GET
])
def test_paket_menolak_ringkasan_salah(salah):
    def bangun():
        Paket("contoh", [T_CARI, T_HITUNG, T_UBAH_PR, replace(T_UBAH, **salah), T_DETAIL])
    if salah["ringkasan_dari"] == "cari_x":
        bangun()
        return
    with pytest.raises(ValueError):
        bangun()


def test_paket_menolak_read_dengan_parameter_jalur_di_luar_write():
    detail_lain = Tool("detail_y", Kelas.READ, "d", OBJ({"kode": {"type": "string"}}, ["kode"]), "GET", "/api/y/{kode}")
    with pytest.raises(ValueError):
        Paket("contoh", [T_CARI, T_HITUNG, T_UBAH_PR, detail_lain,
                         replace(T_UBAH, ringkasan_dari="detail_y", ringkas_dokumen=_ringkas)])


# ---------------------------------------------------------------- paket Penawaran
WRITE_PENAWARAN = [t for t in P.TOOLS if t.kelas == Kelas.WRITE]


@pytest.mark.parametrize("t", WRITE_PENAWARAN, ids=lambda t: t.nama)
def test_semua_write_atas_penawaran_yang_ada_membawa_ringkasan(t):
    atas_dokumen = t.jalur.startswith("/api/quotes/{id}")
    assert (t.ringkasan_dari == "detail_penawaran") == atas_dokumen, t.nama


def test_medan_ringkasan_ada_di_quotedetail_be():
    akar = os.path.join(os.path.dirname(__file__), "..", "..", "app")
    src = open(os.path.join(akar, "schemas", "quotes.py"), encoding="utf-8").read()
    blok = src.split("class QuoteDetail(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    medan = set(re.findall(r"^    (\w+):", blok, re.M))
    pakai = set(re.findall(r'd\.get\("(\w+)"\)', open(P.__file__, encoding="utf-8").read().split("def ringkas_penawaran", 1)[1]
                           .split("\n\n\n", 1)[0]))
    assert pakai == {"quote_number", "customer_name", "total_amount", "items", "expiry_date", "status"}
    assert pakai <= medan, pakai - medan


def test_ringkas_penawaran_bentuk_kontrak():
    d = {"quote_number": "QUO-2610-0040", "customer_name": "Flowry", "total_amount": 1250000,
         "items": [{}, {}], "expiry_date": "2026-11-09", "status": "draft", "lain": 1}
    assert P.ringkas_penawaran(d) == {"nomor": "QUO-2610-0040", "pelanggan": "Flowry", "total": 1250000,
                                      "jumlah_baris": 2, "berlaku_sampai": "2026-11-09", "status": "draft"}
    assert P.ringkas_penawaran({})["jumlah_baris"] is None
